"""Persistent reusable proof patterns selected by goal relevance."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("autolean")

#: Proof shape for a one-tactic proof: tactic → (pattern, what, when).
SINGLE_TACTIC_PATTERNS: dict[str, tuple[str, str, str]] = {
    "rfl": (
        "reflexivity",
        "Proves equalities by computation",
        "Goal is a = b where both sides reduce to the same term",
    ),
    "simp": (
        "simplification",
        "Closes goals via simplification lemmas",
        "Goal can be simplified to True or a known fact",
    ),
    "omega": (
        "linear_arithmetic",
        "Proves linear arithmetic goals over Nat/Int",
        "Goal involves <, <=, +, - on natural numbers or integers",
    ),
    "ring": (
        "ring_tactic",
        "Proves ring equalities",
        "Goal is an equality in a commutative ring (Nat, Int, etc.)",
    ),
    "trivial": (
        "trivial_proof",
        "Closes trivially true goals",
        "Goal is True or provable by assumption",
    ),
    "decide": (
        "decidability",
        "Proves decidable propositions",
        "Goal is a decidable proposition (finite cases)",
    ),
    "contradiction": (
        "contradiction",
        "Derives False from contradictory hypotheses",
        "Hypotheses contain a contradiction",
    ),
    "assumption": (
        "assumption_closing",
        "Goal matches an existing hypothesis",
        "Goal is exactly one of the hypotheses",
    ),
}

_REWRITING = (
    "rewriting",
    "Proves by rewriting with known equalities",
    "Goal can be transformed by rewriting lemmas",
)

#: Multi-tactic shapes, most specific first. A rule fires when every tactic
#: in its set appears in the proof.
MULTI_TACTIC_PATTERNS: tuple[tuple[frozenset[str], tuple[str, str, str]], ...] = (
    (
        frozenset({"intro", "exact"}),
        (
            "intro_exact",
            "Introduces hypotheses then constructs proof term",
            "Goal is a function type (forall/arrow)",
        ),
    ),
    (
        frozenset({"induction"}),
        (
            "induction_proof",
            "Proves by structural induction",
            "Goal involves an inductive type (Nat, List, etc.)",
        ),
    ),
    (
        frozenset({"cases"}),
        (
            "case_analysis",
            "Proves by case analysis on a hypothesis",
            "Goal has a disjunction or inductive hypothesis to split",
        ),
    ),
    (
        frozenset({"constructor"}),
        (
            "constructor_intro",
            "Proves by applying a constructor",
            "Goal is a conjunction, exists, or inductive type to construct",
        ),
    ),
    (frozenset({"rw"}), _REWRITING),
    (frozenset({"rewrite"}), _REWRITING),
    (
        frozenset({"apply"}),
        (
            "apply_lemma",
            "Applies a lemma to reduce the goal",
            "Goal matches the conclusion of a known lemma",
        ),
    ),
    (
        frozenset({"split"}),
        (
            "split_cases",
            "Splits goal into cases and proves each",
            "Goal has a decidable condition or disjunction",
        ),
    ),
)


@dataclass
class Skill:
    """A reusable proof pattern learned from a successful proof."""

    name: str  # e.g., "reflexivity_proof"
    description: str  # e.g., "Proves equalities that hold by computation"
    tactics: list[str]  # e.g., ["rfl"]
    applicable_when: str  # e.g., "Goal is an equality where both sides reduce"
    example_theorem: str  # e.g., "trivial_rfl : 1 + 1 = 2"
    #: Accepted proofs whose tactic sequence instantiated this pattern. Only
    #: accepted proofs reach this store, and a prompt carries several patterns
    #: at once, so a rejection cannot be charged to one of them. The count
    #: states observed reuse and claims nothing about a success rate.
    times_observed: int = 1


def _skill_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Map one persisted skill record onto the current field set.

    A record on disk may carry the older success pair, whose denominator
    never advanced past one; its success count is the observation count.
    """
    fields = dict(record)
    succeeded = fields.pop("times_succeeded", None)
    fields.pop("times_used", None)
    if "times_observed" not in fields and succeeded is not None:
        fields["times_observed"] = max(int(succeeded), 1)
    return fields


@dataclass
class SkillMemory:
    """Persistent skill store — learns from proofs, injects into prompts."""

    skills_dir: Path
    skills: dict[str, Skill] = field(default_factory=dict)
    persist: bool = True

    def __post_init__(self) -> None:
        self._load_skills()

    def _load_skills(self) -> None:
        """Load skills from JSON files on disk."""
        for path in self.skills_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                skill = Skill(**_skill_fields(data))
                self.skills[skill.name] = skill
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as e:
                log.warning("Failed to load skill %s: %s", path, e)

    def _save_skill(self, skill: Skill) -> None:
        """Persist a skill to disk."""
        if not self.persist:
            return
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        path = self.skills_dir / f"{skill.name}.json"
        data = {
            "name": skill.name,
            "description": skill.description,
            "tactics": skill.tactics,
            "applicable_when": skill.applicable_when,
            "example_theorem": skill.example_theorem,
            "times_observed": skill.times_observed,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- Learning from proofs -------------------------------------------------

    def learn_from_proof(
        self,
        theorem_name: str,
        theorem_statement: str,
        proof: str,
    ) -> Skill | None:
        """Return the skill this proof instantiates.

        Reinforces the stored record when the pattern is already known.
        Returns None when no tactic can be extracted.
        """
        tactics = self._extract_tactics(proof)
        if not tactics:
            return None

        pattern_name, description, applicable = self._classify_pattern(tactics)

        if pattern_name in self.skills:
            existing = self.skills[pattern_name]
            existing.times_observed += 1
            self._save_skill(existing)
            log.debug(
                "Skill '%s' observed again (%d accepted proofs)",
                pattern_name,
                existing.times_observed,
            )
            return existing

        skill = Skill(
            name=pattern_name,
            description=description,
            tactics=tactics,
            applicable_when=applicable,
            example_theorem=f"{theorem_name} : {theorem_statement[:100]}",
        )
        self.skills[pattern_name] = skill
        self._save_skill(skill)
        log.info("Learned new skill: '%s' — %s", pattern_name, description)
        return skill

    # -- Injecting into prompts -----------------------------------------------

    def get_prompt_injection(self, goal_state: str, max_skills: int = 5) -> str:
        """Return the top `max_skills` patterns scored against `goal_state`.

        The result is a formatted prompt section, empty when no pattern
        scores above zero.
        """
        if not self.skills:
            return ""

        scored: list[tuple[float, Skill]] = []
        for skill in self.skills.values():
            score = self._relevance_score(skill, goal_state)
            scored.append((score, skill))

        scored.sort(key=lambda x: -x[0])
        top = [s for _, s in scored[:max_skills] if _ > 0]

        if not top:
            return ""

        lines = ["## Learned Proof Patterns (from previous sessions)"]
        for s in top:
            seen = "1 accepted proof" if s.times_observed == 1 else f"{s.times_observed} accepted proofs"
            lines.append(
                f"- **{s.name}** (from {seen}): {s.description}\n"
                f"  Tactics: `{' ; '.join(s.tactics)}`\n"
                f"  Use when: {s.applicable_when}"
            )
        return "\n".join(lines)

    # -- Pattern classification -----------------------------------------------

    def _extract_tactics(self, proof: str) -> list[str]:
        """Extract individual tactic names from a proof."""
        tactics = []
        for line in proof.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("--") or line.startswith("/-"):
                continue
            # Extract the first word as the tactic name
            match = re.match(r"[|·]?\s*(\w+)", line)
            if match:
                tactics.append(match.group(1))
        return tactics

    def _classify_pattern(self, tactics: list[str]) -> tuple[str, str, str]:
        """Name a proof's shape: (pattern, what it does, when it applies)."""
        if len(tactics) == 1:
            only = tactics[0]
            named = SINGLE_TACTIC_PATTERNS.get(only)
            if named is not None:
                return named
            return f"single_{only}", f"Closes goals with `{only}`", f"Goal is closeable by {only}"

        tactic_set = set(tactics)
        for required, pattern in MULTI_TACTIC_PATTERNS:
            if required <= tactic_set:
                return pattern

        key = "_".join(tactics[:3])
        return (
            f"pattern_{key}",
            f"Multi-tactic pattern: {' → '.join(tactics[:5])}",
            "Similar goal structure",
        )

    def _relevance_score(self, skill: Skill, goal_state: str) -> float:
        """Score how relevant a skill is to the current goal state.

        Goal wording carries the ranking. Repeated observation breaks ties by
        a bounded factor, so an old pattern cannot outrank a pattern the goal
        actually names.
        """
        score = 0.0
        goal_lower = goal_state.lower()

        keywords = skill.applicable_when.lower().split()
        for kw in keywords:
            if kw in goal_lower:
                score += 1.0

        if skill.times_observed > 3:
            score *= 1.5

        return score
