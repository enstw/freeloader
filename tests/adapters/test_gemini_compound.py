# Compound-provider invariant (PLAN principle #1, gemini_compound
# lesson): one gemini turn can touch multiple sub-models, and the
# canonical UsageDelta must preserve the per-sub-model breakdown.
# A flat single-key dict would discard information the router needs
# for quota tracking (PLAN principle #5).
#
# Verifies the live-observed shape (gemini-2.5-flash-lite +
# gemini-3-flash-preview together in one stats.models block).
from __future__ import annotations

from freeloader.adapters.gemini import map_event
from freeloader.canonical.deltas import FinishDelta, UsageDelta


def test_two_submodels_become_two_usage_entries():
    event = {
        "type": "result",
        "status": "success",
        "stats": {
            "total_tokens": 7821,
            "input_tokens": 7679,
            "output_tokens": 45,
            "models": {
                "gemini-2.5-flash-lite": {
                    "input_tokens": 759,
                    "output_tokens": 44,
                    "cached": 0,
                },
                "gemini-3-flash-preview": {
                    "input_tokens": 6920,
                    "output_tokens": 1,
                    "cached": 0,
                },
            },
        },
    }
    finish, usage = map_event(event)
    assert isinstance(finish, FinishDelta)
    assert isinstance(usage, UsageDelta)
    assert set(usage.models) == {"gemini-2.5-flash-lite", "gemini-3-flash-preview"}

    lite = usage.models["gemini-2.5-flash-lite"]
    pre = usage.models["gemini-3-flash-preview"]
    assert (lite.input_tokens, lite.output_tokens) == (759, 44)
    assert (pre.input_tokens, pre.output_tokens) == (6920, 1)


def test_three_submodels_all_preserved():
    # Defensive: gemini auto-router could pick any number of sub-models.
    event = {
        "type": "result",
        "status": "success",
        "stats": {
            "models": {
                "m-a": {"input_tokens": 1, "output_tokens": 1},
                "m-b": {"input_tokens": 2, "output_tokens": 2},
                "m-c": {"input_tokens": 3, "output_tokens": 3},
            }
        },
    }
    _, usage = map_event(event)
    assert set(usage.models) == {"m-a", "m-b", "m-c"}
    assert sum(m.input_tokens for m in usage.models.values()) == 6
    assert sum(m.output_tokens for m in usage.models.values()) == 6


def test_models_takes_precedence_over_flat_stats():
    # If both stats.models and the flat input_tokens/output_tokens are
    # present, the per-sub-model breakdown wins — the flat totals are
    # derivable from the breakdown but not vice versa.
    event = {
        "type": "result",
        "status": "success",
        "stats": {
            "input_tokens": 99999,  # would be the wrong key if we used flat
            "output_tokens": 99999,
            "models": {
                "real-model": {"input_tokens": 10, "output_tokens": 5},
            },
        },
    }
    _, usage = map_event(event)
    assert set(usage.models) == {"real-model"}
    assert usage.models["real-model"].input_tokens == 10
