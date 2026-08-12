"""Reviewed mappings from paper items to pinned Lean declarations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PaperProfileError(ValueError):
    """A reviewed paper profile cannot bind to an extracted document."""


class PaperScope(StrEnum):
    """The mathematical role of one item in a reviewed paper."""

    BACKGROUND = "background"
    CORE = "core"
    APPLICATION = "application"


class PaperDeclarationKind(StrEnum):
    """The Lean command used to bind a reviewed declaration."""

    DEFINITION = "definition"
    THEOREM = "theorem"


@dataclass(frozen=True)
class PaperDeclaration:
    """One exact declaration in the pinned Lean environment."""

    name: str
    kind: PaperDeclarationKind


@dataclass(frozen=True)
class PaperItem:
    """One numbered paper item and its reviewed Lean witnesses."""

    label: str
    scope: PaperScope
    declarations: tuple[PaperDeclaration, ...]


@dataclass(frozen=True)
class PaperProfile:
    """A provenance-bound inventory for one reviewed paper revision."""

    id: str
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    pdf_sha256: str
    source_archive_sha256: str
    imports: tuple[str, ...]
    items: tuple[PaperItem, ...]

    def __post_init__(self) -> None:
        labels = [item.label for item in self.items]
        if len(labels) != len(set(labels)):
            raise PaperProfileError(f"paper profile has duplicate labels: {self.id}")
        if any(not item.declarations for item in self.items):
            raise PaperProfileError(f"paper profile has an unmapped item: {self.id}")
        if not self.imports or any(not module.strip() for module in self.imports):
            raise PaperProfileError(f"paper profile has an invalid import closure: {self.id}")

    @property
    def item_by_label(self) -> dict[str, PaperItem]:
        """Return the unique item mapping indexed by paper label."""
        return {item.label: item for item in self.items}


def _definition(name: str) -> PaperDeclaration:
    return PaperDeclaration(name, PaperDeclarationKind.DEFINITION)


def _theorem(name: str) -> PaperDeclaration:
    return PaperDeclaration(name, PaperDeclarationKind.THEOREM)


def _item(
    label: str,
    scope: PaperScope,
    *declarations: PaperDeclaration,
) -> PaperItem:
    return PaperItem(label, scope, declarations)


IONESCU_TULCEA_V5 = PaperProfile(
    id="arxiv-2506.18616v5",
    arxiv_id="2506.18616v5",
    title="A Formalization of the Ionescu-Tulcea Theorem in Mathlib",
    authors=("Etienne Marion",),
    pdf_sha256="39db363898dfb4a51c0e344a6154f76dd6c3e8768a414d516853e6cdc12dfe2d",
    source_archive_sha256="c7d40a1c95cfc5c0a14ea30901a2e867a0c31863048ef1e66f47c2f68f49b8ee",
    imports=("Mathlib.Probability.ProductMeasure",),
    items=(
        _item("Definition 2.1", PaperScope.BACKGROUND, _definition("MeasureTheory.IsSetRing")),
        _item("Definition 2.2", PaperScope.BACKGROUND, _definition("MeasureTheory.AddContent")),
        _item(
            "Definition 2.3",
            PaperScope.BACKGROUND,
            _definition("MeasureTheory.AddContent.IsSigmaSubadditive"),
        ),
        _item(
            "Lemma 2.4",
            PaperScope.BACKGROUND,
            _theorem("MeasureTheory.addContent_iUnion_eq_sum_of_tendsto_zero"),
            _theorem("MeasureTheory.isSigmaSubadditive_of_addContent_iUnion_eq_tsum"),
        ),
        _item(
            "Theorem 2.5",
            PaperScope.BACKGROUND,
            _theorem("MeasureTheory.AddContent.measure"),
        ),
        _item(
            "Definition 2.6",
            PaperScope.BACKGROUND,
            _definition("ProbabilityTheory.Kernel"),
            _definition("ProbabilityTheory.IsMarkovKernel"),
        ),
        _item(
            "Definition 2.7",
            PaperScope.BACKGROUND,
            _definition("ProbabilityTheory.Kernel.map"),
        ),
        _item(
            "Definition 2.8",
            PaperScope.BACKGROUND,
            _definition("ProbabilityTheory.Kernel.comp"),
            _definition("MeasureTheory.Measure.bind"),
        ),
        _item("Definition 2.9", PaperScope.BACKGROUND, _definition("Set.preimage")),
        _item(
            "Definition 2.10",
            PaperScope.BACKGROUND,
            _definition("ProbabilityTheory.Kernel.compProd"),
            _definition("MeasureTheory.Measure.compProd"),
        ),
        _item(
            "Theorem 2.11",
            PaperScope.CORE,
            _definition("ProbabilityTheory.Kernel.traj"),
            _theorem("ProbabilityTheory.Kernel.traj_map_frestrictLe"),
            _theorem("ProbabilityTheory.Kernel.eq_traj"),
        ),
        _item(
            "Definition 2.12",
            PaperScope.CORE,
            _definition("MeasureTheory.measurableCylinders"),
        ),
        _item(
            "Definition 2.13",
            PaperScope.CORE,
            _definition("ProbabilityTheory.Kernel.trajContent"),
        ),
        _item(
            "Lemma 2.14",
            PaperScope.CORE,
            _definition("ProbabilityTheory.Kernel.trajContent"),
        ),
        _item(
            "Lemma 2.15",
            PaperScope.CORE,
            _theorem("ProbabilityTheory.Kernel.trajContent_tendsto_zero"),
        ),
        _item(
            "Lemma 3.1",
            PaperScope.CORE,
            _theorem("ProbabilityTheory.Kernel.partialTraj_comp_partialTraj"),
        ),
        _item(
            "Lemma 3.2",
            PaperScope.CORE,
            _theorem("ProbabilityTheory.Kernel.partialTraj_map_frestrictLe₂"),
        ),
        _item(
            "Definition 3.3",
            PaperScope.CORE,
            _definition("MeasureTheory.IsProjectiveMeasureFamily"),
        ),
        _item(
            "Definition 3.4",
            PaperScope.CORE,
            _definition("MeasureTheory.IsProjectiveLimit"),
        ),
        _item(
            "Definition 3.5",
            PaperScope.CORE,
            _definition("MeasureTheory.projectiveFamilyContent"),
        ),
        _item(
            "Proposition 3.6",
            PaperScope.CORE,
            _theorem("ProbabilityTheory.Kernel.traj_map_frestrictLe"),
        ),
        _item(
            "Lemma 3.7",
            PaperScope.CORE,
            _theorem("ProbabilityTheory.Kernel.traj_comp_partialTraj"),
        ),
        _item(
            "Theorem 3.8",
            PaperScope.CORE,
            _theorem("ProbabilityTheory.Kernel.condExp_traj"),
        ),
        _item(
            "Lemma 3.9",
            PaperScope.CORE,
            _theorem("ProbabilityTheory.Kernel.partialTraj_compProd_traj"),
        ),
        _item(
            "Theorem 4.1",
            PaperScope.APPLICATION,
            _definition("MeasureTheory.Measure.infinitePi"),
            _theorem("MeasureTheory.Measure.isProjectiveLimit_infinitePi"),
            _theorem("MeasureTheory.Measure.eq_infinitePi"),
        ),
    ),
)


PAPER_PROFILES = (IONESCU_TULCEA_V5,)


def match_paper_profile(pdf_sha256: str) -> PaperProfile | None:
    """Return the reviewed profile bound to exact PDF bytes."""
    return next((profile for profile in PAPER_PROFILES if profile.pdf_sha256 == pdf_sha256), None)
