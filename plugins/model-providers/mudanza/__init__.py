"""Mudanza governed model-router provider profile."""

from __future__ import annotations

import os

from providers import register_provider
from providers.base import ProviderProfile


def _base_url() -> str:
    return os.getenv(
        "MUDANZA_ROUTER_BASE_URL",
        "https://tessara-prod-functions.azurewebsites.net/api/hermes/v1",
    ).rstrip("/")


mudanza = ProviderProfile(
    name="mudanza",
    aliases=("mudanza-router", "tessara-router"),
    env_vars=("MUDANZA_FIRM_LEARNING_TOKEN", "MUDANZA_ROUTER_API_KEY"),
    display_name="Mudanza Firm Router",
    description="Firm-governed routing, audit, and learning control plane",
    base_url=_base_url(),
    supports_health_check=False,
    fallback_models=("mudanza-auto",),
    default_aux_model="mudanza-auto",
)

register_provider(mudanza)
