from __future__ import annotations

import pytest

from orchestrator.summary_reduce_policy import (
    build_reduce_admission_snapshot,
    resolve_fast_final_merge_enabled,
    resolve_reduce_strategy,
)


def test_build_reduce_admission_snapshot_applies_token_margin_and_reports_fields():
    snapshot = build_reduce_admission_snapshot(
        payload="x" * 900,
        input_char_cap=1200,
        output_token_cap=256,
        reserve_ratio=0.15,
        reserve_tokens_floor=128,
    )

    assert snapshot["joined_payload_tokens_est"] > 0
    assert snapshot["final_prompt_tokens_est"] > 0
    assert snapshot["group_prompt_tokens_est"] > 0
    assert snapshot["budget_tokens"] > 0
    assert snapshot["reserve_tokens"] == 128
    assert snapshot["margin_tokens"] == 128
    assert snapshot["fits_with_margin"] is True


def test_build_reduce_admission_snapshot_uses_ratio_when_it_exceeds_floor():
    snapshot = build_reduce_admission_snapshot(
        payload="x" * 5000,
        input_char_cap=6000,
        output_token_cap=2048,
        reserve_ratio=0.20,
        reserve_tokens_floor=128,
    )

    assert snapshot["reserve_tokens"] > 128
    assert snapshot["margin_tokens"] == snapshot["reserve_tokens"]


def test_resolve_reduce_strategy_prefers_fast_final_merge_only_with_margin():
    snapshot = build_reduce_admission_snapshot(
        payload="x" * 900,
        input_char_cap=1200,
        output_token_cap=256,
        reserve_ratio=0.15,
        reserve_tokens_floor=128,
    )

    result = resolve_reduce_strategy(
        reduce_items_count=3,
        low_vram=False,
        degraded_active=False,
        fast_final_merge_enabled=True,
        admission_snapshot=snapshot,
    )

    assert result["strategy"] == "fast_final_merge"
    assert result["reason"] == "fits_final_budget"
    assert result["fast_path_eligible"] is True
    assert result["admission_snapshot"]["fits_with_margin"] is True


def test_resolve_reduce_strategy_falls_back_when_margin_is_insufficient():
    snapshot = build_reduce_admission_snapshot(
        payload="x" * 9000,
        input_char_cap=1200,
        output_token_cap=256,
        reserve_ratio=0.15,
        reserve_tokens_floor=128,
    )

    result = resolve_reduce_strategy(
        reduce_items_count=3,
        low_vram=False,
        degraded_active=False,
        fast_final_merge_enabled=True,
        admission_snapshot=snapshot,
    )

    assert result["strategy"] == "hierarchical_merge"
    assert result["reason"] == "budget_overflow"
    assert result["fast_path_eligible"] is False
    assert result["admission_snapshot"]["fits_with_margin"] is False


def test_resolve_reduce_strategy_keeps_backward_compatible_legacy_inputs():
    result = resolve_reduce_strategy(
        reduce_items_count=3,
        final_stage_safe=True,
        low_vram=False,
        degraded_active=False,
        fast_final_merge_enabled=True,
    )

    assert result["strategy"] == "fast_final_merge"
    assert result["reason"] == "fits_final_budget"


def test_resolve_fast_final_merge_enabled_prefers_policy_over_env(monkeypatch):
    monkeypatch.setenv("SUMMARY_ENABLE_FAST_FINAL_MERGE", "0")

    assert resolve_fast_final_merge_enabled(
        policy={"enable_fast_final_merge": True},
        env_var="SUMMARY_ENABLE_FAST_FINAL_MERGE",
    ) is True


@pytest.mark.parametrize(
    "kwargs, expected_strategy, expected_reason",
    [
        (
            {
                "reduce_items_count": 3,
                "low_vram": False,
                "degraded_active": False,
                "fast_final_merge_enabled": False,
                "admission_snapshot": build_reduce_admission_snapshot(
                    payload="x" * 900,
                    input_char_cap=1200,
                    output_token_cap=256,
                ),
            },
            "hierarchical_merge",
            "policy_disabled",
        ),
        (
            {
                "reduce_items_count": 3,
                "low_vram": True,
                "degraded_active": False,
                "fast_final_merge_enabled": True,
                "admission_snapshot": build_reduce_admission_snapshot(
                    payload="x" * 900,
                    input_char_cap=1200,
                    output_token_cap=256,
                ),
            },
            "hierarchical_merge",
            "hardware_policy",
        ),
        (
            {
                "reduce_items_count": 3,
                "low_vram": False,
                "degraded_active": True,
                "fast_final_merge_enabled": True,
                "admission_snapshot": build_reduce_admission_snapshot(
                    payload="x" * 900,
                    input_char_cap=1200,
                    output_token_cap=256,
                ),
            },
            "hierarchical_merge",
            "degraded_mode",
        ),
    ],
)
def test_resolve_reduce_strategy_matrix_covers_invariants(kwargs, expected_strategy, expected_reason):
    result = resolve_reduce_strategy(**kwargs)

    assert result["strategy"] == expected_strategy
    assert result["reason"] == expected_reason
    assert result["fast_path_eligible"] is False
    assert "partial_only" not in result.values()
    assert isinstance(result.get("reduce_decisions"), list)
    assert result["reduce_decisions"]
    assert result["reduce_decisions"][0]["strategy_selected"] == expected_strategy
    assert result["reduce_decisions"][0]["items_count"] == kwargs["reduce_items_count"]
    assert "partial_only" not in str(result["reduce_decisions"])


def test_resolve_reduce_strategy_emits_trace_and_early_exit_for_fast_path():
    snapshot = build_reduce_admission_snapshot(
        payload="x" * 900,
        input_char_cap=1200,
        output_token_cap=256,
    )

    result = resolve_reduce_strategy(
        reduce_items_count=4,
        low_vram=False,
        degraded_active=False,
        fast_final_merge_enabled=True,
        admission_snapshot=snapshot,
    )

    assert result["strategy"] == "fast_final_merge"
    assert result["reduce_decisions"][0]["strategy_considered"] == "fast_final_merge"
    assert result["reduce_decisions"][0]["strategy_selected"] == "fast_final_merge"
    assert result["reduce_decisions"][0]["early_exit_after_level"] == 0
    assert result["early_exit_after_level"] == 0


def test_resolve_reduce_strategy_keeps_legacy_inputs_and_trace_shape():
    result = resolve_reduce_strategy(
        reduce_items_count=3,
        final_stage_safe=True,
        low_vram=False,
        degraded_active=False,
        fast_final_merge_enabled=True,
    )

    assert result["strategy"] == "fast_final_merge"
    assert result["reason"] == "fits_final_budget"
    assert result["reduce_decisions"][0]["fits_with_margin"] is True
    assert result["reduce_decisions"][0]["early_exit_after_level"] == 0
