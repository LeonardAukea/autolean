"""Persistent reusable proof patterns selected by goal relevance."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

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
    times_used: int = 0
    times_succeeded: int = 0

    @property
    def success_rate(self) -> float:
        return self.times_succeeded / self.times_used if self.times_used > 0 else 0.0


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
                skill = Skill(**data)
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
            "times_used": skill.times_used,
            "times_succeeded": skill.times_succeeded,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- Learning from proofs -------------------------------------------------

    def learn_from_proof(
        self,
        theorem_name: str,
        theorem_statement: str,
        proof: str,
    ) -> Skill | None:
        """Extract a skill from a successful proof.

        Analyzes the proof to identify the tactic pattern and
        creates a reusable skill.
        """
        tactics = self._extract_tactics(proof)
        if not tactics:
            return None

        # Classify the proof pattern
        pattern_name, description, applicable = self._classify_pattern(tactics)

        # Check if we already have this skill
        if pattern_name in self.skills:
            existing = self.skills[pattern_name]
            existing.times_succeeded += 1
            self._save_skill(existing)
            log.debug(
                "Skill '%s' reinforced (success %d/%d)",
                pattern_name,
                existing.times_succeeded,
                existing.times_used,
            )
            return existing

        # Create new skill
        skill = Skill(
            name=pattern_name,
            description=description,
            tactics=tactics,
            applicable_when=applicable,
            example_theorem=f"{theorem_name} : {theorem_statement[:100]}",
            times_used=1,
            times_succeeded=1,
        )
        self.skills[pattern_name] = skill
        self._save_skill(skill)
        log.info("Learned new skill: '%s' — %s", pattern_name, description)
        return skill

    # -- Injecting into prompts -----------------------------------------------

    def get_prompt_injection(self, goal_state: str, max_skills: int = 5) -> str:
        """Generate a prompt section with relevant skills for the current goal.

        Ranks skills by relevance to the goal state and returns a
        formatted string for injection into the LLM prompt.
        """
        if not self.skills:
            return ""

        # Rank skills by relevance (simple keyword matching for now)
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
            rate = f"{s.success_rate * 100:.0f}%" if s.times_used > 0 else "new"
            lines.append(
                f"- **{s.name}** ({rate} success): {s.description}\n"
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
        """Score how relevant a skill is to the current goal state."""
        score = 0.0
        goal_lower = goal_state.lower()

        # Keyword matching
        keywords = skill.applicable_when.lower().split()
        for kw in keywords:
            if kw in goal_lower:
                score += 1.0

        # Boost by historical success rate
        score *= 1.0 + skill.success_rate

        # Boost frequently successful skills
        if skill.times_succeeded > 3:
            score *= 1.5

        return score
