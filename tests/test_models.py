"""Tests for model profiles and the model → LLMConfig resolution."""

from __future__ import annotations

import pytest

from autolean.llm import BACKENDS
from autolean.models import (
    AUTO_PROFILE,
    DEFAULT_PROFILE,
    MAX_PROFILE_BY_BACKEND,
    PROFILES,
    ModelProfile,
    ModelSelectionError,
    detect_default_profile,
    infer_backend,
    maximum_profile_for_backend,
    profile_groups,
    profile_status,
    resolve_llm_config,
    resolve_profile,
)


class TestProfileTable:
    def test_default_profile_is_machine_detection(self) -> None:
        assert DEFAULT_PROFILE == AUTO_PROFILE == "auto"

    @pytest.mark.parametrize("profile", PROFILES.values(), ids=lambda p: p.name)
    def test_every_profile_targets_a_known_backend(self, profile: ModelProfile) -> None:
        assert profile.backend in BACKENDS

    @pytest.mark.parametrize("profile", PROFILES.values(), ids=lambda p: p.name)
    def test_every_profile_names_a_model(self, profile: ModelProfile) -> None:
        assert profile.model

    def test_names_and_aliases_are_unique(self) -> None:
        seen: set[str] = set()
        for profile in PROFILES.values():
            for key in (profile.name, *profile.aliases):
                assert key not in seen, f"duplicate profile key: {key}"
                seen.add(key)

    def test_groups_cover_every_profile(self) -> None:
        grouped = {p.name for _, profiles in profile_groups() for p in profiles}
        assert grouped == set(PROFILES)

    @pytest.mark.parametrize("profile", PROFILES.values(), ids=lambda p: p.name)
    def test_default_escalation_routes_stay_on_one_backend(self, profile: ModelProfile) -> None:
        if profile.escalates_to is None:
            return
        target = PROFILES[profile.escalates_to]
        assert target.backend == profile.backend
        assert target.model != profile.model

    @pytest.mark.parametrize("profile", PROFILES.values(), ids=lambda p: p.name)
    def test_reasoning_profiles_declare_no_temperature(self, profile: ModelProfile) -> None:
        if profile.backend in ("claude_cli", "codex_cli", "anthropic", "openai"):
            assert profile.temperature is None

    def test_current_provider_families_have_named_profiles(self) -> None:
        assert PROFILES["fable-api"].model == "claude-fable-5"
        assert PROFILES["opus-api"].model == "claude-opus-5"
        assert PROFILES["sonnet-api"].model == "claude-sonnet-5"
        assert PROFILES["gpt-api"].model == "gpt-5.6-sol"
        assert PROFILES["gpt-terra-api"].model == "gpt-5.6-terra"
        assert PROFILES["gpt-luna-api"].model == "gpt-5.6-luna"

    @pytest.mark.parametrize(
        ("backend", "profile", "model"),
        [
            ("claude_cli", "fable", "fable"),
            ("codex_cli", "codex", "gpt-5.6-sol"),
            ("anthropic", "fable-api", "claude-fable-5"),
            ("openai", "gpt-api", "gpt-5.6-sol"),
        ],
    )
    def test_provider_maxima_use_maximum_reasoning(
        self,
        backend: str,
        profile: str,
        model: str,
    ) -> None:
        assert MAX_PROFILE_BY_BACKEND[backend] == profile
        resolved = maximum_profile_for_backend(backend)
        assert resolved.model == model
        assert resolved.effort == "max"

    def test_generic_provider_aliases_select_the_maximum_profile(self) -> None:
        assert resolve_profile("claude") is PROFILES["fable"]
        assert resolve_profile("anthropic") is PROFILES["fable-api"]
        assert resolve_profile("openai") is PROFILES["codex"]

    def test_muse_glimmer_profile_is_pinned_and_deterministic(self) -> None:
        profile = PROFILES["muse-glimmer"]

        assert profile.backend == "muse_glimmer"
        assert profile.temperature == pytest.approx(0.0)
        assert profile.seed == 0
        assert profile.revision == "93769bc7ab5ad1e9cd22d857e3138cf5d977ae81"
        assert profile.artifact_sha256 == ("7e9b74b7c8875e9e265695df9613bf6290f2392e479ce740495a129019c488d8")
        assert profile.max_output_tokens == 8192
        assert profile.effort == "low"
        assert PROFILES["muse-glimmer-bf16"].revision == ("f84ecc3a0ea984a4c04542a84269e3d065350a6e")


class TestResolveProfile:
    def test_by_name(self) -> None:
        assert resolve_profile("opus") is PROFILES["opus"]

    def test_by_alias(self) -> None:
        assert resolve_profile("claude") is PROFILES["fable"]

    def test_unknown_name_is_not_a_profile(self) -> None:
        assert resolve_profile("gemma4:26b") is None


class TestInferBackend:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("claude-opus-5", "anthropic"),
            ("claude-sonnet-5", "anthropic"),
            ("gpt-5.6-sol", "openai"),
            ("o3", "openai"),
            ("meta-models/Muse-Glimmer-30B", "muse_glimmer"),
            ("gemma4:26b", "ollama"),
            ("some/local-model", "ollama"),
        ],
    )
    def test_prefix_selects_the_backend(self, model: str, expected: str) -> None:
        assert infer_backend(model) == expected


class TestResolveLLMConfig:
    def test_profile_supplies_backend_and_model(self) -> None:
        cfg = resolve_llm_config("opus")
        assert (cfg.model, cfg.backend) == ("opus", "claude_cli")

    def test_auto_with_explicit_provider_selects_its_maximum(self) -> None:
        cfg = resolve_llm_config(AUTO_PROFILE, backend="codex_cli")
        assert (cfg.model, cfg.backend, cfg.effort) == ("gpt-5.6-sol", "codex_cli", "max")

    def test_explicit_profile_does_not_run_machine_detection(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def unexpected_detection(backend: str | None = None) -> ModelProfile:
            raise AssertionError(f"unexpected automatic detection for {backend}")

        monkeypatch.setattr("autolean.models.detect_default_profile", unexpected_detection)

        assert resolve_llm_config("sonnet").model == "sonnet"

    def test_raw_model_string_infers_its_backend(self) -> None:
        cfg = resolve_llm_config("claude-opus-5")
        assert (cfg.model, cfg.backend) == ("claude-opus-5", "anthropic")

    def test_explicit_backend_overrides_the_profile(self) -> None:
        cfg = resolve_llm_config("opus", backend="ollama")
        assert cfg.backend == "ollama"

    def test_explicit_endpoint_overrides_the_profile(self) -> None:
        cfg = resolve_llm_config("muse-glimmer", base_url="http://127.0.0.1:9000")
        assert cfg.base_url == "http://127.0.0.1:9000"

    def test_explicit_overrides_are_applied(self) -> None:
        cfg = resolve_llm_config("gemma4", temperature=0.9, timeout=42.0, max_output_tokens=128, effort="max")
        assert cfg.temperature == pytest.approx(0.9)
        assert cfg.timeout == pytest.approx(42.0)
        assert cfg.max_output_tokens == 128
        assert cfg.effort == "max"

    def test_profile_opting_out_of_temperature_wins(self) -> None:
        """Reasoning profiles retain their sampling configuration."""
        cfg = resolve_llm_config("opus-api", temperature=0.4)
        assert cfg.temperature is None

    def test_profile_effort_survives_when_not_overridden(self) -> None:
        assert resolve_llm_config("opus-api").effort == "high"

    def test_unset_overrides_leave_profile_defaults(self) -> None:
        cfg = resolve_llm_config("bfs-prover")
        assert cfg.max_output_tokens == PROFILES["bfs-prover"].max_output_tokens

    @pytest.mark.parametrize(
        "overrides",
        [
            {"temperature": -0.1},
            {"temperature": 2.1},
            {"temperature": float("nan")},
            {"timeout": -1.0},
            {"timeout": float("inf")},
            {"max_output_tokens": -1},
            {"base_url": "file:///tmp/model"},
        ],
    )
    def test_invalid_final_overrides_are_rejected(
        self,
        overrides: dict[str, float | int | str],
    ) -> None:
        with pytest.raises(ValueError):
            resolve_llm_config("gemma4", **overrides)  # type: ignore[arg-type]


class TestProfileStatus:
    def test_cli_profile_is_installed_when_binary_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autolean.models.shutil.which", lambda _: "/usr/bin/claude")
        assert "installed" in profile_status(PROFILES["opus"], set())

    def test_cli_profile_reports_the_missing_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autolean.models.shutil.which", lambda _: None)
        assert "claude" in profile_status(PROFILES["opus"], set())

    def test_api_profile_reports_the_missing_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        assert "unverified" in profile_status(PROFILES["opus-api"], set())

    def test_api_profile_reports_a_configured_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert "OPENAI_API_KEY set" in profile_status(PROFILES["gpt-api"], set())

    def test_anthropic_auth_token_is_observed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        assert "ANTHROPIC_AUTH_TOKEN set" in profile_status(PROFILES["opus-api"], set())

    def test_ollama_profile_tracks_pulled_models(self) -> None:
        gemma = PROFILES["gemma4"]
        assert "pulled" in profile_status(gemma, {gemma.model})
        assert "not pulled" in profile_status(gemma, set())


class TestAutomaticProfileDetection:
    def test_authenticated_claude_selects_fable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autolean.llm.subscription import SubscriptionStatus

        monkeypatch.setattr(
            "autolean.llm.subscription.probe_subscription_backend",
            lambda backend: SubscriptionStatus(ready=backend == "claude_cli"),
        )

        assert detect_default_profile() is PROFILES["fable"]

    def test_authenticated_codex_is_used_when_claude_is_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autolean.llm.subscription import SubscriptionStatus

        monkeypatch.setattr(
            "autolean.llm.subscription.probe_subscription_backend",
            lambda backend: SubscriptionStatus(ready=backend == "codex_cli"),
        )

        assert detect_default_profile() is PROFILES["codex"]

    def test_hosted_credential_is_a_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autolean.llm.subscription import SubscriptionStatus

        monkeypatch.setattr(
            "autolean.llm.subscription.probe_subscription_backend",
            lambda backend: SubscriptionStatus(ready=False),
        )
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

        assert detect_default_profile() is PROFILES["gpt-api"]

    def test_missing_provider_fails_with_setup_advice(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autolean.llm.subscription import SubscriptionStatus

        monkeypatch.setattr(
            "autolean.llm.subscription.probe_subscription_backend",
            lambda backend: SubscriptionStatus(ready=False),
        )
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(ModelSelectionError, match=r"claude|codex login"):
            detect_default_profile()

    def test_local_backend_requires_an_explicit_model(self) -> None:
        with pytest.raises(ModelSelectionError, match="explicit model"):
            maximum_profile_for_backend("ollama")


def test_a_pinned_profile_downloads_the_revision_it_records() -> None:
    """The digest beside a revision only describes what the command fetches."""
    pinned = [profile for profile in PROFILES.values() if profile.revision]

    assert pinned, "no profile pins a revision"
    for profile in pinned:
        if "--revision" not in profile.setup_command:
            continue
        assert profile.revision in profile.setup_command, (
            f"{profile.name} fetches a revision other than the one it records"
        )
