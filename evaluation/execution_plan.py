"""Shared, deterministic schedule for development and formal evaluation runs.

The schedule is read from the current freeze manifest.  Keeping it in one
module prevents command-line loops from accidentally running three
repetitions of every scenario (24 runs per condition) instead of the frozen
16-run plan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .schemas import PAIRED_CONDITIONS, SCENARIO_IDS, load_json
from .validate_specs import SPEC_DIR


CURRENT_FREEZE_MANIFEST_PATH = SPEC_DIR / "freeze_manifest.v10_2.json"
PENDING_BASELINE_COMMIT = "PENDING_V10_2_REVIEW_COMMIT"


class EvaluationPlanError(RuntimeError):
    """Raised when the checked-in run plan is incomplete or inconsistent."""


def require_finalized_baseline_commit(manifest: dict[str, Any]) -> str:
    """Return C1's hash, or block formal execution until the freeze is finalised."""

    local_commit = (manifest.get("product_baseline") or {}).get(
        "local_baseline_commit"
    )
    if local_commit == PENDING_BASELINE_COMMIT:
        raise EvaluationPlanError(
            "Formal execution is blocked while local_baseline_commit remains pending."
        )
    if not isinstance(local_commit, str) or len(local_commit) != 40 or any(
        character not in "0123456789abcdef" for character in local_commit
    ):
        raise EvaluationPlanError(
            "Formal execution requires a full lowercase 40-character hexadecimal "
            "C1 commit hash."
        )
    return local_commit


@dataclass(frozen=True)
class PairPlan:
    scenario_id: str
    repetition: int
    pair_key: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AttemptPlan:
    scenario_id: str
    repetition: int
    pair_key: str
    condition: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_pair_plan(
    manifest_path: Path = CURRENT_FREEZE_MANIFEST_PATH,
) -> tuple[dict[str, Any], list[PairPlan]]:
    """Return the exact 16-pair schedule declared by the current manifest."""

    manifest = load_json(Path(manifest_path))
    run_plan = manifest.get("run_plan") or {}
    repetitions = run_plan.get("recommended_repetitions_per_condition")
    if not isinstance(repetitions, dict) or tuple(repetitions) != SCENARIO_IDS:
        raise EvaluationPlanError(
            "The run plan must list S1-S8 once and in frozen scenario order."
        )

    pairs: list[PairPlan] = []
    for scenario_id in SCENARIO_IDS:
        count = repetitions.get(scenario_id)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise EvaluationPlanError(
                f"The repetition count for {scenario_id} must be a positive integer."
            )
        for repetition in range(1, count + 1):
            pairs.append(
                PairPlan(
                    scenario_id=scenario_id,
                    repetition=repetition,
                    pair_key=f"{scenario_id}-R{repetition:02d}",
                )
            )

    expected = run_plan.get("recommended_matched_pairs")
    if len(pairs) != expected or expected != 16:
        raise EvaluationPlanError(
            f"The frozen plan must contain exactly 16 pairs; found {len(pairs)}."
        )
    if len({pair.pair_key for pair in pairs}) != len(pairs):
        raise EvaluationPlanError("The frozen run plan contains duplicate pair keys.")
    return manifest, pairs


def load_attempt_plan(
    manifest_path: Path = CURRENT_FREEZE_MANIFEST_PATH,
) -> tuple[dict[str, Any], list[AttemptPlan]]:
    """Return the exact 32-attempt paired-condition schedule."""

    manifest, pairs = load_pair_plan(manifest_path)
    attempts = [
        AttemptPlan(
            scenario_id=pair.scenario_id,
            repetition=pair.repetition,
            pair_key=pair.pair_key,
            condition=condition,
        )
        for pair in pairs
        for condition in PAIRED_CONDITIONS
    ]
    expected = manifest["run_plan"].get("recommended_individual_sessions")
    if len(attempts) != expected or expected != 32:
        raise EvaluationPlanError(
            f"The frozen plan must contain exactly 32 attempts; found {len(attempts)}."
        )
    return manifest, attempts


__all__ = [
    "AttemptPlan",
    "CURRENT_FREEZE_MANIFEST_PATH",
    "EvaluationPlanError",
    "PairPlan",
    "PENDING_BASELINE_COMMIT",
    "load_attempt_plan",
    "load_pair_plan",
    "require_finalized_baseline_commit",
]
