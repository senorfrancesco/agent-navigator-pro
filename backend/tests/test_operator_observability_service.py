from pathlib import Path

from orchestrator.operator_observability_service import OperatorObservabilityService


def test_operator_observability_service_builds_summary_from_metrics(monkeypatch, tmp_path: Path):
    repo_root = tmp_path
    monitoring = repo_root / "monitoring" / "grafana" / "provisioning" / "dashboards" / "json"
    monitoring.mkdir(parents=True)
    (repo_root / "monitoring" / "prometheus.yml").write_text(
        'scrape_configs:\n  - job_name: "agent-api"\n  - job_name: "ums"\n',
        encoding="utf-8",
    )
    (monitoring / "llm-tools-platform-overview.json").write_text(
        '{"uid":"llm-tools-platform-overview","title":"llm-tools-platform Overview"}',
        encoding="utf-8",
    )

    metrics_payload = """
# HELP llm_tools_platform_http_requests_total Total HTTP requests.
# TYPE llm_tools_platform_http_requests_total counter
llm_tools_platform_http_requests_total{service="agent_api",method="GET",path="/operator/state",status_code="200"} 12
llm_tools_platform_http_requests_total{service="ums",method="POST",path="/infer",status_code="200"} 4
# HELP llm_tools_platform_http_requests_in_progress In-flight.
# TYPE llm_tools_platform_http_requests_in_progress gauge
llm_tools_platform_http_requests_in_progress{service="agent_api",method="GET"} 1
llm_tools_platform_http_requests_in_progress{service="ums",method="POST"} 2
# HELP llm_tools_platform_ums_running_models Running.
# TYPE llm_tools_platform_ums_running_models gauge
llm_tools_platform_ums_running_models{service="ums"} 2
# HELP llm_tools_platform_ums_active_heavy_model Active.
# TYPE llm_tools_platform_ums_active_heavy_model gauge
llm_tools_platform_ums_active_heavy_model{service="ums"} 1
# HELP llm_tools_platform_agent_api_orchestration_requests_total Orchestration.
# TYPE llm_tools_platform_agent_api_orchestration_requests_total counter
llm_tools_platform_agent_api_orchestration_requests_total{endpoint="/execute_orchestration",result="ok"} 7
# HELP llm_tools_platform_ums_concurrency_saturation_total Saturation.
# TYPE llm_tools_platform_ums_concurrency_saturation_total counter
llm_tools_platform_ums_concurrency_saturation_total{kind="heavy",mode="gpu"} 3
# HELP llm_tools_platform_fallback_events_total Fallbacks.
# TYPE llm_tools_platform_fallback_events_total counter
llm_tools_platform_fallback_events_total{component="document_analysis",fallback="llm_extract_failed"} 5
""".strip()

    monkeypatch.setattr(
        "orchestrator.operator_observability_service.render_metrics_text",
        lambda: metrics_payload,
    )

    service = OperatorObservabilityService(repo_root)
    summary = service.get_metrics_summary()

    assert summary["overview"][0]["value"] == "16"
    assert summary["overview"][0]["labelEn"] == "HTTP Events Total"
    assert summary["overview"][0]["noteEn"]
    assert any(card["label"] == "Запросов в работе" and card["value"] == "3" for card in summary["services"])
    assert any(card["labelEn"] == "Requests In Flight" and card["valueEn"] == "3" for card in summary["services"])
    assert any(card["label"] == "Активная тяжёлая модель" and card["value"] == "да" for card in summary["services"])
    assert any(card["label"] == "Насыщение UMS" and card["value"] == "3" for card in summary["deploy"])


def test_operator_observability_service_exposes_grafana_links(tmp_path: Path):
    repo_root = tmp_path
    dashboard_dir = repo_root / "monitoring" / "grafana" / "provisioning" / "dashboards" / "json"
    dashboard_dir.mkdir(parents=True)
    (dashboard_dir / "llm-tools-platform-overview.json").write_text(
        '{"uid":"llm-tools-platform-overview","title":"llm-tools-platform Overview"}',
        encoding="utf-8",
    )
    (repo_root / "monitoring" / "prometheus.yml").write_text("scrape_configs: []\n", encoding="utf-8")

    service = OperatorObservabilityService(repo_root)
    payload = service.get_grafana_links()

    assert payload["base_url"] == "http://127.0.0.1:3002"
    assert any(link["title"] == "Общая панель" for link in payload["links"])
    assert any(link["titleEn"] == "Overview Dashboard" for link in payload["links"])
    assert any(link["title"] == "Раздел Explore" for link in payload["links"])
    assert any(link["title"] == "Панель деплоя" for link in payload["links"])
    assert any("llm-tools-platform-overview" in link["url"] for link in payload["links"])


def test_operator_observability_service_handles_empty_metrics_and_missing_dashboard(monkeypatch, tmp_path: Path):
    repo_root = tmp_path
    (repo_root / "monitoring").mkdir(parents=True)
    monkeypatch.setattr(
        "orchestrator.operator_observability_service.render_metrics_text",
        lambda: "",
    )

    service = OperatorObservabilityService(repo_root)
    summary = service.get_metrics_summary()
    links = service.get_grafana_links()

    assert any(card["label"] == "HTTP-событий всего" and card["value"] == "0" for card in summary["overview"])
    assert any(card["labelEn"] == "Prometheus Scrape" and card["valueEn"] == "not found" for card in summary["deploy"])
    assert any(card["label"] == "Статус опроса Prometheus" and card["value"] == "не найден" for card in summary["deploy"])
    assert links["dashboard_uid"] == "llm-tools-platform-overview"
