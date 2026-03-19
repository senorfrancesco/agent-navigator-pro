"""Shared adaptive reduce policy for summary workflows.

Invariants:
- `fast_final_merge` is only selected when the final payload fits with safety margin.
- `degraded_active=True` blocks fast path.
- `low_vram=True` blocks fast path unless explicitly overridden by config.
- This helper never emits `partial_only`; that remains a runtime fallback state.
- Returned decision traces are diagnostic-only and do not mutate execution behavior.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def resolve_fast_final_merge_enabled(*, policy: Dict[str, Any] | None, env_var: str) -> bool:
    policy = policy or {}
    raw = policy.get("enable_fast_final_merge")
    if raw is None:
        return _env_flag(env_var, True)
    return bool(raw)


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def _build_reduce_decisions(
    *,
    reduce_items_count: int,
    strategy: str,
    reason: str,
    admission_snapshot: Dict[str, Any],
) -> list[Dict[str, Any]]:
    strategy_considered = strategy
    if strategy not in {"single_item", "fast_final_merge"}:
        strategy_considered = "hierarchical_merge"
    return [
        {
            "items_count": int(reduce_items_count),
            "chars": int(admission_snapshot.get("joined_payload_chars", 0) or 0),
            "tokens_est": int(admission_snapshot.get("joined_payload_tokens_est", 0) or 0),
            "strategy_considered": strategy_considered,
            "strategy_selected": strategy,
            "reason": reason,
            "fits_with_margin": bool(admission_snapshot.get("fits_with_margin")),
            "early_exit_after_level": 0 if strategy == "fast_final_merge" else None,
        }
    ]


def build_reduce_admission_snapshot(
    *,
    payload: str,
    input_char_cap: int,
    output_token_cap: int,
    reserve_ratio: float = 0.15,
    reserve_tokens_floor: int = 128,
) -> Dict[str, Any]:
    joined_payload = str(payload or "")
    joined_payload_chars = len(joined_payload)
    joined_payload_tokens_est = _estimate_tokens(joined_payload)
    prompt_overhead_tokens = max(16, int(input_char_cap) // 64)
    final_prompt_tokens_est = joined_payload_tokens_est + prompt_overhead_tokens
    group_prompt_tokens_est = joined_payload_tokens_est + prompt_overhead_tokens + max(
        8, prompt_overhead_tokens // 2
    )
    budget_tokens = max(1, _estimate_tokens("x" * int(input_char_cap)) + int(output_token_cap))
    reserve_tokens = max(
        int(reserve_tokens_floor),
        int(round(budget_tokens * float(reserve_ratio))),
    )
    margin_tokens = reserve_tokens
    fits_without_margin = final_prompt_tokens_est <= budget_tokens
    fits_with_margin = (final_prompt_tokens_est + margin_tokens) <= budget_tokens
    return {
        "joined_payload_chars": joined_payload_chars,
        "joined_payload_tokens_est": joined_payload_tokens_est,
        "final_prompt_tokens_est": final_prompt_tokens_est,
        "group_prompt_tokens_est": group_prompt_tokens_est,
        "budget_tokens": budget_tokens,
        "reserve_tokens": reserve_tokens,
        "margin_tokens": margin_tokens,
        "fits_without_margin": fits_without_margin,
        "fits_with_margin": fits_with_margin,
    }


def resolve_reduce_strategy(
    *,
    reduce_items_count: int,
    low_vram: bool,
    degraded_active: bool,
    fast_final_merge_enabled: bool,
    admission_snapshot: Optional[Dict[str, Any]] = None,
    final_stage_safe: Optional[bool] = None,
    allow_fast_final_merge_on_low_vram: bool = False,
) -> Dict[str, Any]:
    """Resolve the adaptive reduce strategy and return a traceable decision payload.

    The helper is intentionally side-effect free. It returns the selected strategy,
    a small decision trace, and the admission snapshot used to reach the decision.
    """
    snapshot = dict(admission_snapshot or {})
    if not snapshot:
        snapshot = {
            "joined_payload_chars": 0,
            "joined_payload_tokens_est": 0,
            "final_prompt_tokens_est": 0,
            "group_prompt_tokens_est": 0,
            "budget_tokens": 0,
            "reserve_tokens": 0,
            "margin_tokens": 0,
            "fits_without_margin": bool(final_stage_safe),
            "fits_with_margin": bool(final_stage_safe),
        }
    fits_with_margin = bool(snapshot.get("fits_with_margin"))
    if final_stage_safe is not None and not snapshot.get("final_prompt_tokens_est"):
        fits_with_margin = bool(final_stage_safe)
        snapshot["fits_with_margin"] = fits_with_margin
    if reduce_items_count <= 1:
        decision = {
            "strategy": "single_item",
            "reason": "not_needed",
            "fast_path_eligible": False,
            "admission_snapshot": snapshot,
        }
        decision["reduce_decisions"] = _build_reduce_decisions(
            reduce_items_count=reduce_items_count,
            strategy=decision["strategy"],
            reason=decision["reason"],
            admission_snapshot=snapshot,
        )
        decision["early_exit_after_level"] = 0
        return decision
    if not fast_final_merge_enabled:
        decision = {
            "strategy": "hierarchical_merge",
            "reason": "policy_disabled",
            "fast_path_eligible": False,
            "admission_snapshot": snapshot,
        }
        decision["reduce_decisions"] = _build_reduce_decisions(
            reduce_items_count=reduce_items_count,
            strategy=decision["strategy"],
            reason=decision["reason"],
            admission_snapshot=snapshot,
        )
        decision["early_exit_after_level"] = None
        return decision
    if low_vram and not allow_fast_final_merge_on_low_vram:
        decision = {
            "strategy": "hierarchical_merge",
            "reason": "hardware_policy",
            "fast_path_eligible": False,
            "admission_snapshot": snapshot,
        }
        decision["reduce_decisions"] = _build_reduce_decisions(
            reduce_items_count=reduce_items_count,
            strategy=decision["strategy"],
            reason=decision["reason"],
            admission_snapshot=snapshot,
        )
        decision["early_exit_after_level"] = None
        return decision
    if degraded_active:
        decision = {
            "strategy": "hierarchical_merge",
            "reason": "degraded_mode",
            "fast_path_eligible": False,
            "admission_snapshot": snapshot,
        }
        decision["reduce_decisions"] = _build_reduce_decisions(
            reduce_items_count=reduce_items_count,
            strategy=decision["strategy"],
            reason=decision["reason"],
            admission_snapshot=snapshot,
        )
        decision["early_exit_after_level"] = None
        return decision
    if fits_with_margin:
        decision = {
            "strategy": "fast_final_merge",
            "reason": "fits_final_budget",
            "fast_path_eligible": True,
            "admission_snapshot": snapshot,
        }
        decision["reduce_decisions"] = _build_reduce_decisions(
            reduce_items_count=reduce_items_count,
            strategy=decision["strategy"],
            reason=decision["reason"],
            admission_snapshot=snapshot,
        )
        decision["early_exit_after_level"] = 0
        return decision
    decision = {
        "strategy": "hierarchical_merge",
        "reason": "budget_overflow",
        "fast_path_eligible": False,
        "admission_snapshot": snapshot,
    }
    decision["reduce_decisions"] = _build_reduce_decisions(
        reduce_items_count=reduce_items_count,
        strategy=decision["strategy"],
        reason=decision["reason"],
        admission_snapshot=snapshot,
    )
    decision["early_exit_after_level"] = None
    return decision
