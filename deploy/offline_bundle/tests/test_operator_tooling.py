from __future__ import annotations

import json
import subprocess
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def test_launch_tmux_workspace_help_like_contract() -> None:
    script_path = BUNDLE_ROOT / "scripts" / "launch_tmux_workspace.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "btop" in content
    assert "tmux" in content
    assert "monitor" in content


def test_prometheus_scrape_config_mentions_core_services() -> None:
    config_text = (BUNDLE_ROOT / "monitoring" / "prometheus.yml").read_text(encoding="utf-8")

    assert "agent-api:8000" in config_text
    assert "ums:8090" in config_text
    assert "document-server:8001" in config_text
    assert "legal-server:8002" in config_text
    assert "chainlit:3000" in config_text


def test_grafana_dashboard_is_valid_json() -> None:
    payload = json.loads(
        (BUNDLE_ROOT / "monitoring" / "grafana" / "dashboards" / "offline-bundle-overview.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["title"] == "Offline Bundle Overview"
    assert payload["uid"] == "offline-bundle-overview"
    assert len(payload["panels"]) >= 3
