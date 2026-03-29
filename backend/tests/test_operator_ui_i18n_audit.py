from orchestrator.operator_ui_api import build_operator_state


REQUIRED_BILINGUAL_KEYS = {
    "title",
    "description",
    "body",
    "label",
    "name",
    "role",
    "action",
    "note",
    "reason",
    "recommendedReason",
    "summaryTitle",
    "stagesTitle",
    "actionsTitle",
    "artifactsTitle",
    "logsTitle",
    "remediation",
    "pathLabel",
}


def _collect_missing_bilingual_fields(payload, path="root"):
    missing = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in REQUIRED_BILINGUAL_KEYS and isinstance(value, str):
                pair_key = f"{key}En"
                if pair_key not in payload:
                    missing.append((path, key, value))
            missing.extend(_collect_missing_bilingual_fields(value, f"{path}.{key}"))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            missing.extend(_collect_missing_bilingual_fields(item, f"{path}[{index}]"))
    return missing


def test_operator_state_exposes_bilingual_fields_for_semantic_ui_keys():
    state = build_operator_state()
    missing = _collect_missing_bilingual_fields(state)
    assert missing == []
