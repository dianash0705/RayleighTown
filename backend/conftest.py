"""Shared pytest fixtures for backend tests."""

from dataclasses import replace

import pytest

import confidence_scoring

from config import CONFIDENCE_SCORING_CONFIG, HARMONIC_ANALYSIS_CONFIG


@pytest.fixture
def harmonic_config():
    """Return the live harmonic config (read-only reference)."""
    return HARMONIC_ANALYSIS_CONFIG


@pytest.fixture
def override_harmonic_config(monkeypatch):
    """Temporarily replace brain's harmonic config for a single test."""

    def _apply(**overrides):
        import brain
        import fourier

        updated = replace(HARMONIC_ANALYSIS_CONFIG, **overrides)
        monkeypatch.setattr(brain, "HARMONIC_ANALYSIS_CONFIG", updated)
        monkeypatch.setattr(fourier, "HARMONIC_ANALYSIS_CONFIG", updated)
        return updated

    return _apply


@pytest.fixture
def override_confidence_config(monkeypatch):
    """Temporarily replace confidence scoring config for a single test."""

    def _apply(**overrides):
        import brain

        updated = replace(CONFIDENCE_SCORING_CONFIG, **overrides)
        monkeypatch.setattr(confidence_scoring, "CONFIDENCE_SCORING_CONFIG", updated)
        monkeypatch.setattr(brain, "CONFIDENCE_SCORING_CONFIG", updated)
        return updated

    return _apply
