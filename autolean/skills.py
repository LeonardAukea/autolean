"""Skill memory — Hermes-inspired reusable proof patterns.

After the agent successfully proves a theorem, it extracts the
tactic pattern as a "skill" that gets injected into future prompts.
This creates a self-improving loop: each proof teaches the agent
patterns for future proofs.

Inspired by the Hermes agent framework's autonomous skill creation:
https://hermes-agent.nousresearch.com/docs
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("autolean")


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

    def __post_init__(self) -> None:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._load_skills()

    def _load_skills(self) -> None:
        """Load skills from JSON files on disk."""
        for path in self.skills_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                skill = Skill(**data)
                self.skills[skill.name] = skill
            except Exception as e:
                log.warning("Failed to load skill %s: %s", path, e)

    def _save_skill(self, skill: Skill) -> None:
        """Persist a skill to disk."""
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
        path.write_text(json.dumps(data, indent=2))

    # -- Learning from proofs -------------------------------------------------

    def learn_from_proof(
        self,
        theorem_name: str,
        theorem_statement: str,
        proof: str,
        goal_state: str,
    ) -> Skill | None:
        """Extract a skill from a successful proof.

        Analyzes the proof to identify the tactic pattern and
        creates a reusable skill.
        """
        tactics = self._extract_tactics(proof)
        if not tactics:
            return None

        # Classify the proof pattern
        pattern_name, description, applicable = self._classify_pattern(
            tactics, goal_state, theorem_statement
        )

        # Check if we already have this skill
        if pattern_name in self.skills:
            existing = self.skills[pattern_name]
            existing.times_succeeded += 1
            self._save_skill(existing)
            log.debug("Skill '%s' reinforced (success %d/%d)",
                       pattern_name, existing.times_succeeded, existing.times_used)
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

    def _classify_pattern(
        self,
        tactics: list[str],
        goal_state: str,
        theorem_stmt: str,
    ) -> tuple[str, str, str]:
        """Classify a proof into a named pattern with description."""
        tactic_set = set(tactics)
        goal_lower = goal_state.lower()

        # Single-tactic patterns
        if len(tactics) == 1:
            t = tactics[0]
            if t == "rfl":
                return "reflexivity", "Proves equalities by computation", "Goal is a = b where both sides reduce to the same term"
            if t == "simp":
                return "simplification", "Closes goals via simplification lemmas", "Goal can be simplified to True or a known fact"
            if t == "omega":
                return "linear_arithmetic", "Proves linear arithmetic goals over Nat/Int", "Goal involves <, <=, +, - on natural numbers or integers"
            if t == "ring":
                return "ring_tactic", "Proves ring equalities", "Goal is an equality in a commutative ring (Nat, Int, etc.)"
            if t == "trivial":
                return "trivial_proof", "Closes trivially true goals", "Goal is True or provable by assumption"
            if t == "decide":
                return "decidability", "Proves decidable propositions", "Goal is a decidable proposition (finite cases)"
            if t == "contradiction":
                return "contradiction", "Derives False from contradictory hypotheses", "Hypotheses contain a contradiction"
            if t == "assumption":
                return "assumption_closing", "Goal matches an existing hypothesis", "Goal is exactly one of the hypotheses"
            return f"single_{t}", f"Closes goals with `{t}`", f"Goal is closeable by {t}"

        # Multi-tactic patterns
        if "intro" in tactic_set and "exact" in tactic_set:
            return "intro_exact", "Introduces hypotheses then constructs proof term", "Goal is a function type (forall/arrow)"
        if "induction" in tactic_set:
            return "induction_proof", "Proves by structural induction", "Goal involves an inductive type (Nat, List, etc.)"
        if "cases" in tactic_set:
            return "case_analysis", "Proves by case analysis on a hypothesis", "Goal has a disjunction or inductive hypothesis to split"
        if "constructor" in tactic_set:
            return "constructor_intro", "Proves by applying a constructor", "Goal is a conjunction, exists, or inductive type to construct"
        if "rw" in tactic_set or "rewrite" in tactic_set:
            return "rewriting", "Proves by rewriting with known equalities", "Goal can be transformed by rewriting lemmas"
        if "apply" in tactic_set:
            return "apply_lemma", "Applies a lemma to reduce the goal", "Goal matches the conclusion of a known lemma"
        if "split" in tactic_set:
            return "split_cases", "Splits goal into cases and proves each", "Goal has a decidable condition or disjunction"

        # Default: name from tactic sequence
        key = "_".join(tactics[:3])
        return f"pattern_{key}", f"Multi-tactic pattern: {' → '.join(tactics[:5])}", "Similar goal structure"

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
        score *= (1.0 + skill.success_rate)

        # Boost frequently successful skills
        if skill.times_succeeded > 3:
            score *= 1.5

        return score
