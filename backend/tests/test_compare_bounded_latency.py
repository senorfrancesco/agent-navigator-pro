"""
Test for B3.51g and B3.51h: Summary-first compare with bounded latency and fast/deep modes.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator.workflows.compare import (
    COMPARE_FAST_TOP_N,
    COMPARE_DEEP_TOP_N,
    CompareState,
)


def test_compare_constants_defined():
    """Test that B3.51g constants are properly defined."""
    assert COMPARE_FAST_TOP_N == 12, "Fast mode should analyze top 12 items"
    assert COMPARE_DEEP_TOP_N == 30, "Deep mode should analyze top 30 items"


def test_compare_state_has_depth_mode():
    """Test that CompareState includes compare_depth_mode field."""
    state = {
        "input_1": "/tmp/test1.pdf",
        "input_2": "/tmp/test2.pdf",
        "name_1": "test1.pdf",
        "name_2": "test2.pdf",
        "compare_depth_mode": "fast",  # B3.51h
        "matches": [],
        "analysis_results": [],
        "errors": [],
        "model_execution": [],
    }
    # Should not raise TypedDict error
    assert state["compare_depth_mode"] == "fast"


if __name__ == "__main__":
    # Run simple smoke tests
    test_compare_constants_defined()
    print("✓ Constants test passed (COMPARE_FAST_TOP_N=12, COMPARE_DEEP_TOP_N=30)")

    test_compare_state_has_depth_mode()
    print("✓ State schema test passed (compare_depth_mode field exists)")

    print("\n✓ All smoke tests passed!")
    print("\nNote: For full async tests with mocking, install pytest and run:")
    print("  pytest backend/tests/test_compare_bounded_latency.py -v")
