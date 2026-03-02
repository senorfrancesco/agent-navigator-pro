#!/usr/bin/env python3
"""
Hook: test-on-edit.py
PostToolUse — срабатывает после Edit/Write.
Если изменён файл в workflows/ или rag/ — запускает релевантный тест.
"""

import json
import os
import subprocess
import sys

# Маппинг путей → тесты
MAPPING = {
    "rag/classifier.py":           ["tests/test_intent_classifier.py"],
    "workflows/equipment.py":      ["tests/test_equipment_workflow.py"],
    "workflows/compare.py":        ["tests/test_e2e_equipment.py"],
    "workflows/document_analysis": ["tests/test_document_analysis.py"],
    "rag/pipeline.py":             ["tests/test_rag_pipeline.py"],
    "rag/retriever.py":            ["tests/test_hybrid_search.py"],
    "rag/chunker.py":              ["tests/test_chunker.py"],
    "chainlit_app.py":             ["tests/test_intent_classifier.py"],
}

def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    file_path = hook_input.get("tool_input", {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    # Ищем релевантные тесты
    matched_tests = []
    for pattern, tests in MAPPING.items():
        if pattern in file_path:
            matched_tests.extend(tests)

    if not matched_tests:
        sys.exit(0)

    backend_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "backend"
    )

    test_args = " ".join(matched_tests)
    print(f"[test-on-edit] Running: pytest {test_args}", file=sys.stderr)

    result = subprocess.run(
        ["python", "-m", "pytest"] + matched_tests + ["--tb=short", "-q"],
        cwd=backend_dir,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"[test-on-edit] ✅ Tests passed", file=sys.stderr)
    else:
        print(f"[test-on-edit] ❌ Tests FAILED:\n{result.stdout[-2000:]}", file=sys.stderr)

if __name__ == "__main__":
    main()
