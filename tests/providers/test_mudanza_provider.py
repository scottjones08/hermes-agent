"""Behavior contract for the Mudanza provider profile."""

from providers import get_provider_profile


def test_mudanza_provider_is_discoverable_and_governed():
    profile = get_provider_profile("mudanza")
    assert profile is not None
    assert profile.base_url.endswith("/api/hermes/v1")
    assert profile.supports_health_check is False
    assert "mudanza-auto" in profile.fallback_models
    assert "MUDANZA_FIRM_LEARNING_TOKEN" in profile.env_vars


def test_mudanza_provider_alias_resolves_to_same_profile():
    assert get_provider_profile("mudanza-router") is get_provider_profile("mudanza")
