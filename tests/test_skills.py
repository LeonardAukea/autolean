"""Tests for Hermes-inspired skill memory."""

from __future__ import annotations

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
        assert sm.skills["reflexivity"].times_succeeded == 2


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
