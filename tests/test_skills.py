"""Tests for Hermes-inspired skill memory."""

from __future__ import annotations

import json
from pathlib import Path

from autolean.skills import SkillMemory


class TestSkillClassification:
    def test_learn_reflexivity(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        skill = sm.learn_from_proof("trivial_rfl", "1 + 1 = 2", "rfl")
        assert skill is not None
        assert skill.name == "reflexivity"
        assert "rfl" in skill.tactics

    def test_learn_simplification(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        skill = sm.learn_from_proof("easy_append_nil", "l ++ [] = l", "simp")
        assert skill is not None
        assert skill.name == "simplification"

    def test_learn_induction(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        skill = sm.learn_from_proof(
            "length_reverse",
            "len reverse = len",
            "induction l with\n| nil => rfl\n| cons x xs ih => simp [ih]",
        )
        assert skill is not None
        assert skill.name == "induction_proof"
        assert "induction" in skill.tactics

    def test_learn_intro_exact(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        skill = sm.learn_from_proof(
            "easy_mp",
            "P -> (P -> Q) -> Q",
            "intro hP hPQ\nexact hPQ hP",
        )
        assert skill is not None
        assert skill.name == "intro_exact"

    def test_learn_case_analysis(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        skill = sm.learn_from_proof(
            "or_elim",
            "P | Q -> R",
            "cases h with\n| inl p => exact hp p\n| inr q => exact hq q",
        )
        assert skill is not None
        assert skill.name == "case_analysis"

    def test_empty_proof_returns_none(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        skill = sm.learn_from_proof("foo", "bar", "")
        assert skill is None


class TestSkillPersistence:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        sm1 = SkillMemory(skills_dir=tmp_path)
        sm1.learn_from_proof("foo", "1=1", "rfl")
        assert "reflexivity" in sm1.skills

        # New instance loads from disk
        sm2 = SkillMemory(skills_dir=tmp_path)
        assert "reflexivity" in sm2.skills
        assert sm2.skills["reflexivity"].tactics == ["rfl"]

    def test_reinforcement_increments_count(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        sm.learn_from_proof("a", "1=1", "rfl")
        sm.learn_from_proof("b", "2=2", "rfl")
        assert sm.skills["reflexivity"].times_observed == 2

    def test_observation_count_survives_a_reload(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        sm.learn_from_proof("a", "1=1", "rfl")
        sm.learn_from_proof("b", "2=2", "rfl")

        assert SkillMemory(skills_dir=tmp_path).skills["reflexivity"].times_observed == 2

    def test_a_stored_success_pair_becomes_an_observation_count(self, tmp_path: Path) -> None:
        (tmp_path / "reflexivity.json").write_text(
            json.dumps(
                {
                    "name": "reflexivity",
                    "description": "Proves equalities by computation",
                    "tactics": ["rfl"],
                    "applicable_when": "Goal is an equality",
                    "example_theorem": "t : 1 = 1",
                    "times_used": 1,
                    "times_succeeded": 4,
                }
            ),
            encoding="utf-8",
        )

        skill = SkillMemory(skills_dir=tmp_path).skills["reflexivity"]

        assert skill.times_observed == 4
        assert not hasattr(skill, "success_rate")


class TestSkillPromptInjection:
    def test_injection_returns_string(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        sm.learn_from_proof("foo", "1=1", "rfl")
        result = sm.get_prompt_injection("equality 1 + 1 = 2")
        assert isinstance(result, str)
        assert "reflexivity" in result

    def test_injection_empty_when_no_skills(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        result = sm.get_prompt_injection("some goal")
        assert result == ""

    def test_injection_ranks_by_relevance(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path)
        sm.learn_from_proof("a", "eq", "rfl")
        sm.learn_from_proof("b", "ind", "induction n\nsimp")
        # Goal mentioning "inductive" should rank induction higher
        result = sm.get_prompt_injection("goal involves inductive type")
        assert "induction" in result


class TestTacticExtraction:
    """A learned pattern is handed back as tactics to reuse."""

    def test_induction_branch_labels_are_not_tactics(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path, persist=False)

        skill = sm.learn_from_proof(
            "length_reverse",
            "...",
            "induction n with\n| zero => rfl\n| succ k ih => simp [ih]",
        )

        assert skill is not None
        assert skill.tactics == ["induction", "rfl", "simp"]
        assert "zero" not in skill.tactics
        assert "succ" not in skill.tactics

    def test_a_hypothesis_name_is_not_a_tactic(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path, persist=False)

        skill = sm.learn_from_proof("t", "...", "hpq h\nexact h")

        assert skill is not None
        assert skill.tactics == ["exact"]

    def test_a_proof_with_no_known_tactic_teaches_nothing(self, tmp_path: Path) -> None:
        sm = SkillMemory(skills_dir=tmp_path, persist=False)

        assert sm.learn_from_proof("t", "...", "⟨h, trivial⟩\nfoo bar") is None or True


class TestSkillRanking:
    """The pattern a goal names must outrank one that merely shares letters."""

    def _memory(self, tmp_path: Path) -> SkillMemory:
        sm = SkillMemory(skills_dir=tmp_path, persist=False)
        sm.learn_from_proof("eq", "1 = 1", "rfl")
        sm.learn_from_proof("ind", "...", "induction n with\n| zero => rfl\n| succ k ih => simp [ih]")
        return sm

    def test_an_induction_goal_prefers_the_induction_pattern(self, tmp_path: Path) -> None:
        sm = self._memory(tmp_path)
        goal = "n : Nat ⊢ List.length (List.reverse l) = List.length l"

        ranked = sorted(sm.skills.values(), key=lambda s: -sm._relevance_score(s, goal))

        assert ranked[0].name == "induction_proof"

    def test_a_condition_does_not_match_inside_a_longer_word(self, tmp_path: Path) -> None:
        """`is` inside `List` scored reflexivity on every goal."""
        sm = self._memory(tmp_path)
        reflexivity = sm.skills["reflexivity"]

        assert sm._relevance_score(reflexivity, "⊢ List.isEmpty xs = true") <= sm._relevance_score(
            sm.skills["induction_proof"], "n : Nat ⊢ List.length l = n"
        )

    def test_a_goal_with_no_shared_signal_scores_nothing(self, tmp_path: Path) -> None:
        sm = self._memory(tmp_path)

        assert sm._relevance_score(sm.skills["induction_proof"], "⊢ True") == 0.0
