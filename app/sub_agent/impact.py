"""Impact calculation sub-agent placeholder."""

from typing import Any


def estimate_output_delta(
    baseline: dict[str, Any],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    """Estimate WIP, queue, throughput, or due-date impact from scenario deltas."""
    return {
        "baseline": baseline,
        "scenario": scenario,
        "delta": {},
        "limitations": ["Impact model is a placeholder until SMT2020 schema mapping is finalized."],
    }

