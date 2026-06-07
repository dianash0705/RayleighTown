"""Shared pytest fixtures for backend tests."""

from dataclasses import replace

import pytest

from config import HARMONIC_ANALYSIS_CONFIG


@pytest.fixture
def harmonic_config():
    """Return the live harmonic config (read-only reference)."""
    return HARMONIC_ANALYSIS_CONFIG


@pytest.fixture
def override_harmonic_config(monkeypatch):
    """Temporarily replace brain's harmonic config for a single test."""

    def _apply(**overrides):
        import brain

        updated = replace(HARMONIC_ANALYSIS_CONFIG, **overrides)
        monkeypatch.setattr(brain, "HARMONIC_ANALYSIS_CONFIG", updated)
        return updated

    return _apply
