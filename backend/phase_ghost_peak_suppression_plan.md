# Phase Ghost Peak Suppression Plan

## Goal

Prevent harmonic ghost peaks from producing alerts after a stronger long-period peak has already been accepted, without mutating the Fourier data itself.

## Current workaround

1. Keep the Fourier spectrum unchanged.
2. Store a phase value for each Fourier point alongside its magnitude.
3. Process candidate alert peaks from long period to short period.
4. If a shorter candidate is a harmonic of an accepted longer period and the phase similarity is at least 90%, suppress the shorter candidate so it cannot raise an alert.

## Current switch

1. Phase ghost suppression is disabled by default.
2. Re-enable it later by setting `PHASE_GHOST_SUPPRESSION_ENABLED = True` in `backend/config.py`.

## What still needs to be done later

1. Move the phase-suppression tuning values into a dedicated config surface so they are easy to adjust.
2. Replace the current harmonic/phase heuristic with a smarter model that can distinguish a true independent peak from a ghost peak that happens to line up in phase.
3. Add a direct Fourier evaluation helper for arbitrary periods so harmonic checks can be computed exactly when the needed period was not part of the sampled candidate grid.
4. Add tests that cover the long-to-short suppression order, the 30 -> 15 suppression case, and the case where a real shorter-period peak is preserved when its phase does not match the accepted harmonic.

## Acceptance criteria

1. A stronger peak can suppress its harmonic alerts.
2. Alert selection still works without changing the raw Fourier data.
3. Candidate processing order is deterministic and runs from longer periods to shorter periods.