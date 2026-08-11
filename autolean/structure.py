"""Deterministic structural context for Lean source.

Tree-sitter supplies a fast, error-recovering outline for model prompts and
developer inspection. Lean's parser, elaborator, and kernel remain the
authority for every accepted proof.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node, Parser, Tree

_DECLARATION_KINDS = {
    "abbrev",
    "axiom",
    "class",
    "def",
    "deriving",
    "example",
    "inductive",
    "instance",
    "lemma",
    "opaque",
    "structure",
    "theorem",
}
_REFERENCE_NODE_TYPES = {"identifier", "qualified_identifier"}
_SPACE = re.compile(r"\s+")
DEFAULT_CONTEXT_CHARS = 6_000


class ParseQuality(StrEnum):
    """How completely Tree-sitter understood the source around a target."""

    COMPLETE = "complete"
    RECOVERED = "recovered"
    TARGET_RECOVERED = "target_recovered"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SourceSpan:
    """A one-indexed inclusive source-line span."""

    start_line: int
    end_line: int

    def as_dict(self) -> dict[str, int]:
        return {"start_line": self.start_line, "end_line": self.end_line}


@dataclass(frozen=True)
class Declaration:
    """One top-level Lean declaration recovered from the concrete syntax."""

    kind: str
    name: str
    qualified_name: str
    span: SourceSpan
    signature: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "span": self.span.as_dict(),
            "signature": self.signature,
        }


@dataclass(frozen=True)
class StructuralContext:
    """Bounded structural facts attached to one proof target."""

    source_sha256: str
    parser: str
    quality: ParseQuality
    error_spans: tuple[SourceSpan, ...] = ()
    imports: tuple[str, ...] = ()
    namespace: str = ""
    target: Declaration | None = None
    syntax_path: tuple[str, ...] = ()
    referenced_declarations: tuple[Declaration, ...] = ()
    preceding_declarations: tuple[Declaration, ...] = ()
    following_declarations: tuple[Declaration, ...] = ()
    unavailable_reason: str = ""

    @property
    def sha256(self) -> str:
        """Identity of the exact structural text supplied to the model."""
        return hashlib.sha256(self.render().encode()).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "source_sha256": self.source_sha256,
            "context_sha256": self.sha256,
            "parser": self.parser,
            "quality": self.quality.value,
            "error_spans": [span.as_dict() for span in self.error_spans],
            "imports": list(self.imports),
            "namespace": self.namespace,
            "target": self.target.as_dict() if self.target else None,
            "syntax_path": list(self.syntax_path),
            "referenced_declarations": [item.as_dict() for item in self.referenced_declarations],
            "preceding_declarations": [item.as_dict() for item in self.preceding_declarations],
            "following_declarations": [item.as_dict() for item in self.following_declarations],
            "unavailable_reason": self.unavailable_reason,
        }

    def render(self, max_chars: int = DEFAULT_CONTEXT_CHARS) -> str:
        """Render deterministic, prompt-ready context within ``max_chars``."""
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")

        lines = [
            "## Lean source structure (advisory)",
            f"parser: {self.parser}",
            f"source_sha256: {self.source_sha256}",
            f"parse_quality: {self.quality.value}",
        ]
        if self.unavailable_reason:
            lines.append(f"unavailable: {self.unavailable_reason}")
        if self.error_spans:
            spans = ", ".join(f"{span.start_line}-{span.end_line}" for span in self.error_spans[:8])
            lines.append(f"recovered_error_lines: {spans}")
        if self.imports:
            lines.append("imports: " + ", ".join(self.imports[:12]))
        if self.namespace:
            lines.append(f"namespace: {self.namespace}")
        if self.target:
            lines.extend(
                (
                    "target: " + _declaration_summary(self.target),
                    f"target_signature: {self.target.signature}",
                )
            )
        if self.syntax_path:
            lines.append("syntax_path: " + " > ".join(self.syntax_path))
        _append_declarations(lines, "local_references", self.referenced_declarations)
        _append_declarations(lines, "preceding", self.preceding_declarations)
        _append_declarations(lines, "following", self.following_declarations)
        lines.append("Lean elaboration and kernel checking determine correctness.")

        rendered = "\n".join(lines)
        if len(rendered) <= max_chars:
            return rendered
        marker = "\n[structural context truncated]"
        return rendered[: max(0, max_chars - len(marker))].rstrip() + marker


@dataclass(frozen=True)
class _ParsedSource:
    source: bytes
    tree: Tree
    declarations: tuple[tuple[Declaration, Node], ...]
    imports: tuple[str, ...]
    error_nodes: tuple[Node, ...]


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def parser_identity(*, grammar_sha256: str = "") -> str:
    """Versioned identity of both parser runtime and grammar bundle."""
    identity = (
        f"tree-sitter/{_package_version('tree-sitter')} "
        f"lean-grammar/tree-sitter-language-pack/{_package_version('tree-sitter-language-pack')}"
    )
    return f"{identity} grammar-sha256/{grammar_sha256}" if grammar_sha256 else identity


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser_from_library(path: Path) -> tuple[Parser, ctypes.CDLL]:
    """Load the pinned Lean grammar shared library without a network cache."""
    from tree_sitter import Language, Parser

    handle = ctypes.CDLL(str(path))
    factory = handle.tree_sitter_lean
    factory.restype = ctypes.c_void_p
    pointer = factory()
    if pointer is None:
        raise OSError(f"Lean grammar returned a null language pointer: {path}")

    capsule_new = ctypes.pythonapi.PyCapsule_New
    capsule_new.restype = ctypes.py_object
    capsule_new.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
    capsule = capsule_new(pointer, b"tree_sitter.Language", None)
    return Parser(Language(capsule)), handle


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _compact(text: str, limit: int = 600) -> str:
    compact = _SPACE.sub(" ", text).strip()
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _walk(node: Node) -> list[Node]:
    result: list[Node] = []
    stack = [node]
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(reversed(current.named_children))
    return result


def _actual_declaration(node: Node) -> Node | None:
    if node.type in _DECLARATION_KINDS:
        return node
    if node.type != "declaration":
        return None
    for child in reversed(node.named_children):
        if child.type in _DECLARATION_KINDS:
            return child
    return None


def _declaration_signature(node: Node, source: bytes) -> str:
    assignment = next((child for child in node.children if child.type == ":="), None)
    end = assignment.start_byte if assignment is not None else node.end_byte
    return _compact(source[node.start_byte : end].decode("utf-8", errors="replace"))


def _inclusive_end_line(node: Node) -> int:
    """Convert Tree-sitter's exclusive end point to an inclusive line."""
    return node.end_point.row + (1 if node.end_point.column else 0)


def _scope_name(node: Node, source: bytes) -> str:
    name = node.child_by_field_name("name")
    return _compact(_node_text(name, source)) if name is not None else ""


def _parse_declarations(root: Node, source: bytes) -> tuple[tuple[Declaration, Node], ...]:
    scopes: list[tuple[str, str]] = []
    declarations: list[tuple[Declaration, Node]] = []

    for command in root.named_children:
        if command.type in {"namespace", "section"}:
            scopes.append((command.type, _scope_name(command, source)))
            continue
        if command.type == "end":
            if scopes:
                scopes.pop()
            continue

        actual = _actual_declaration(command)
        if actual is None:
            continue
        name_node = actual.child_by_field_name("name")
        name = _compact(_node_text(name_node, source)) if name_node is not None else ""
        if not name:
            name = f"<{actual.type}@{actual.start_point.row + 1}>"
        namespaces = [value for kind, value in scopes if kind == "namespace" and value]
        qualified = (
            name.removeprefix("_root_.") if name.startswith("_root_.") else ".".join([*namespaces, name])
        )
        declaration = Declaration(
            kind=actual.type,
            name=name,
            qualified_name=qualified,
            span=SourceSpan(
                actual.start_point.row + 1,
                max(actual.start_point.row + 1, _inclusive_end_line(actual)),
            ),
            signature=_declaration_signature(actual, source),
        )
        declarations.append((declaration, actual))

    return tuple(declarations)


def _error_nodes(root: Node) -> tuple[Node, ...]:
    return tuple(node for node in _walk(root) if node.type == "ERROR" or node.is_missing)


def _contains_point(node: Node, line: int, col: int) -> bool:
    point = (max(0, line - 1), max(0, col))
    start = (node.start_point.row, node.start_point.column)
    end = (node.end_point.row, node.end_point.column)
    return start <= point <= end


def _syntax_path(root: Node, line: int, col: int) -> tuple[str, ...]:
    point = (max(0, line - 1), max(0, col))
    token_end = (point[0], point[1] + len("sorry"))
    leaf = root.named_descendant_for_point_range(point, token_end)
    path: list[str] = []
    node: Node | None = leaf
    while node is not None and node.type != "module":
        if not path or path[-1] != node.type:
            path.append(node.type)
        node = node.parent
    return tuple(reversed(path))


def _declaration_summary(declaration: Declaration) -> str:
    return (
        f"{declaration.kind} {declaration.qualified_name} "
        f"(lines {declaration.span.start_line}-{declaration.span.end_line})"
    )


def _append_declarations(
    lines: list[str],
    label: str,
    declarations: tuple[Declaration, ...],
) -> None:
    if declarations:
        lines.append(label + ": " + "; ".join(_declaration_summary(item) for item in declarations))


def _references(
    target_node: Node,
    target: Declaration,
    declarations: tuple[tuple[Declaration, Node], ...],
    source: bytes,
) -> tuple[Declaration, ...]:
    identifiers = {
        _node_text(node, source) for node in _walk(target_node) if node.type in _REFERENCE_NODE_TYPES
    }
    references: list[Declaration] = []
    seen: set[str] = set()
    for declaration, node in declarations:
        if node.start_byte >= target_node.start_byte:
            break
        names = {declaration.name, declaration.qualified_name}
        if identifiers.isdisjoint(names) or declaration.qualified_name in seen:
            continue
        references.append(declaration)
        seen.add(declaration.qualified_name)
    return tuple(references[-8:])


class LeanStructureProvider:
    """Parse Lean files once per source hash and derive target contexts."""

    def __init__(self, *, cache_entries: int = 32) -> None:
        if cache_entries <= 0:
            raise ValueError("cache_entries must be positive")
        self._cache_entries = cache_entries
        self._cache: OrderedDict[tuple[str, str], _ParsedSource] = OrderedDict()
        self._parser: Parser | None = None
        self._grammar_handle: ctypes.CDLL | None = None
        self._parser_identity = parser_identity()
        self._unavailable_reason = ""

    def _load_parser(self) -> Parser | None:
        if self._parser is not None:
            return self._parser
        if self._unavailable_reason:
            return None
        try:
            library_path = os.environ.get("AUTOLEAN_TREE_SITTER_LEAN_LIBRARY")
            if library_path:
                path = Path(library_path).resolve(strict=True)
                self._parser, self._grammar_handle = _parser_from_library(path)
                self._parser_identity = parser_identity(grammar_sha256=_sha256_file(path))
            else:
                from tree_sitter_language_pack import cache_dir, get_parser

                self._parser = get_parser("lean")
                grammar = next(Path(cache_dir()).joinpath("libs").glob("libtree_sitter_lean.*"), None)
                if grammar is not None:
                    self._parser_identity = parser_identity(grammar_sha256=_sha256_file(grammar))
        except (AttributeError, ImportError, OSError, RuntimeError, ValueError) as error:
            self._unavailable_reason = _compact(str(error), 300) or type(error).__name__
        return self._parser

    def _parse(self, path: Path, source: str) -> _ParsedSource | None:
        source_bytes = source.encode()
        digest = hashlib.sha256(source_bytes).hexdigest()
        key = (str(path.resolve()), digest)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        parser = self._load_parser()
        if parser is None:
            return None
        tree = parser.parse(source_bytes)
        root = tree.root_node
        imports = tuple(
            _compact(_node_text(node, source_bytes).removeprefix("import "))
            for node in root.named_children
            if node.type == "import"
        )
        parsed = _ParsedSource(
            source=source_bytes,
            tree=tree,
            declarations=_parse_declarations(root, source_bytes),
            imports=imports,
            error_nodes=_error_nodes(root),
        )
        self._cache[key] = parsed
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_entries:
            self._cache.popitem(last=False)
        return parsed

    def inspect(
        self,
        path: Path,
        source: str,
        *,
        line: int,
        col: int,
        declaration_name: str = "",
    ) -> StructuralContext:
        """Return structural facts for one one-indexed source position."""
        source_sha256 = hashlib.sha256(source.encode()).hexdigest()
        parsed = self._parse(path, source)
        if parsed is None:
            return StructuralContext(
                source_sha256=source_sha256,
                parser=self._parser_identity,
                quality=ParseQuality.UNAVAILABLE,
                unavailable_reason=self._unavailable_reason or "Lean grammar unavailable",
            )

        root = parsed.tree.root_node
        candidates = [
            item for item in parsed.declarations if item[0].span.start_line <= line <= item[0].span.end_line
        ]
        if not candidates and declaration_name:
            candidates = [
                item
                for item in parsed.declarations
                if item[0].name == declaration_name or item[0].qualified_name == declaration_name
            ]
        target_pair = min(
            candidates,
            key=lambda item: item[1].end_byte - item[1].start_byte,
            default=None,
        )
        target = target_pair[0] if target_pair else None

        quality = ParseQuality.COMPLETE
        if parsed.error_nodes:
            quality = ParseQuality.RECOVERED
            if any(_contains_point(error, line, col) for error in parsed.error_nodes):
                quality = ParseQuality.TARGET_RECOVERED

        error_spans = tuple(
            SourceSpan(
                node.start_point.row + 1,
                max(node.start_point.row + 1, _inclusive_end_line(node)),
            )
            for node in parsed.error_nodes[:16]
        )
        before: tuple[Declaration, ...] = ()
        after: tuple[Declaration, ...] = ()
        references: tuple[Declaration, ...] = ()
        if target_pair is not None:
            target_index = parsed.declarations.index(target_pair)
            before = tuple(item[0] for item in parsed.declarations[max(0, target_index - 3) : target_index])
            after = tuple(item[0] for item in parsed.declarations[target_index + 1 : target_index + 3])
            references = _references(target_pair[1], target_pair[0], parsed.declarations, parsed.source)

        namespace = ""
        if target is not None and "." in target.qualified_name:
            namespace = target.qualified_name.rsplit(".", 1)[0]
        return StructuralContext(
            source_sha256=source_sha256,
            parser=self._parser_identity,
            quality=quality,
            error_spans=error_spans,
            imports=parsed.imports,
            namespace=namespace,
            target=target,
            syntax_path=_syntax_path(root, line, col),
            referenced_declarations=references,
            preceding_declarations=before,
            following_declarations=after,
        )
