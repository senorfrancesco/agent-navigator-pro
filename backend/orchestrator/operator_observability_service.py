from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

try:
    from services.observability import render_metrics_text
except ModuleNotFoundError:  # pragma: no cover - direct module import fallback
    from backend.services.observability import render_metrics_text


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
_METRIC_LINE_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>-?[0-9]+(?:\.[0-9]+)?)$")


class OperatorObservabilityService:
    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = Path(repo_root or DEFAULT_REPO_ROOT)
        self.monitoring_root = self.repo_root / "monitoring"
        self.dashboard_path = (
            self.monitoring_root
            / "grafana"
            / "provisioning"
            / "dashboards"
            / "json"
            / "llm-tools-platform-overview.json"
        )
        self.prometheus_path = self.monitoring_root / "prometheus.yml"
        self.grafana_base_url = os.getenv("GRAFANA_URL", "http://127.0.0.1:3002").rstrip("/")
        self.prometheus_base_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")

    def _parse_labels(self, raw: str | None) -> Dict[str, str]:
        if not raw:
            return {}
        labels: Dict[str, str] = {}
        for chunk in raw.split(","):
            if "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            labels[key.strip()] = value.strip().strip('"')
        return labels

    def _parse_metrics(self) -> Dict[str, List[Dict[str, Any]]]:
        payload = render_metrics_text()
        metrics: Dict[str, List[Dict[str, Any]]] = {}
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _METRIC_LINE_RE.match(line)
            if not match:
                continue
            metrics.setdefault(match.group("name"), []).append(
                {
                    "labels": self._parse_labels(match.group("labels")),
                    "value": float(match.group("value")),
                }
            )
        return metrics

    def _sum_metric(self, metrics: Dict[str, List[Dict[str, Any]]], name: str, **filters: str) -> float:
        samples = metrics.get(name, [])
        total = 0.0
        for sample in samples:
            labels = sample["labels"]
            if all(labels.get(key) == value for key, value in filters.items()):
                total += float(sample["value"])
        return total

    def _dashboard_uid(self) -> str:
        if not self.dashboard_path.exists():
            return "llm-tools-platform-overview"
        try:
            payload = json.loads(self.dashboard_path.read_text(encoding="utf-8"))
            return str(payload.get("uid") or "llm-tools-platform-overview")
        except Exception:
            return "llm-tools-platform-overview"

    def get_metrics_summary(self) -> Dict[str, List[Dict[str, str]]]:
        metrics = self._parse_metrics()
        total_http = int(self._sum_metric(metrics, "llm_tools_platform_http_requests_total"))
        inflight = int(self._sum_metric(metrics, "llm_tools_platform_http_requests_in_progress"))
        orchestration = int(self._sum_metric(metrics, "llm_tools_platform_agent_api_orchestration_requests_total"))
        ums_models = int(self._sum_metric(metrics, "llm_tools_platform_ums_running_models", service="ums"))
        heavy_active = self._sum_metric(metrics, "llm_tools_platform_ums_active_heavy_model", service="ums") > 0
        saturation = int(self._sum_metric(metrics, "llm_tools_platform_ums_concurrency_saturation_total"))
        fallbacks = int(self._sum_metric(metrics, "llm_tools_platform_fallback_events_total"))
        overview = [
            {
                "label": "HTTP-событий всего",
                "labelEn": "HTTP Events Total",
                "value": str(total_http),
                "valueEn": str(total_http),
                "note": "Сумма наблюдаемых HTTP-счётчиков из экспорта, совместимого с Prometheus.",
                "noteEn": "Sum of observed HTTP request counters from the Prometheus-compatible export.",
                "tone": "cyan",
            },
            {
                "label": "Оркестраций всего",
                "labelEn": "Orchestrations Total",
                "value": str(orchestration),
                "valueEn": str(orchestration),
                "note": "Число запросов оркестрации, видимых через метрики `agent_api`.",
                "noteEn": "Number of orchestration requests visible through `agent_api` metrics.",
                "tone": "lime" if orchestration else "neutral",
            },
            {
                "label": "Событий деградации",
                "labelEn": "Fallback Events",
                "value": str(fallbacks),
                "valueEn": str(fallbacks),
                "note": "Счётчик событий деградации и резервных путей по среде выполнения и сценариям.",
                "noteEn": "Counter of degraded and fallback events across runtime and workflow.",
                "tone": "orange" if fallbacks else "neutral",
            },
        ]
        services = [
            {
                "label": "Запросов в работе",
                "labelEn": "Requests In Flight",
                "value": str(inflight),
                "valueEn": str(inflight),
                "note": "Текущее число запросов в обработке у `agent_api` и `UMS`.",
                "noteEn": "Current in-flight gauge across `agent_api` and `UMS`.",
                "tone": "cyan" if inflight else "neutral",
            },
            {
                "label": "Моделей в UMS",
                "labelEn": "Models in UMS",
                "value": str(ums_models),
                "valueEn": str(ums_models),
                "note": "Количество процессов моделей, поднятых в UMS.",
                "noteEn": "Gauge of running model processes in UMS.",
                "tone": "lime" if ums_models else "neutral",
            },
            {
                "label": "Активная тяжёлая модель",
                "labelEn": "Active Heavy Model",
                "value": "да" if heavy_active else "нет",
                "valueEn": "yes" if heavy_active else "no",
                "note": "Показывает, видит ли UMS активную тяжёлую модель в среде выполнения.",
                "noteEn": "Shows whether UMS sees an active heavy model in the runtime.",
                "tone": "lime" if heavy_active else "neutral",
            },
        ]
        deploy = [
            {
                "label": "Насыщение UMS",
                "labelEn": "UMS Saturation",
                "value": str(saturation),
                "valueEn": str(saturation),
                "note": "Сколько раз среда выполнения упиралась в предел параллелизма.",
                "noteEn": "How many times the runtime hit concurrency saturation.",
                "tone": "orange" if saturation else "neutral",
            },
            {
                "label": "Статус опроса Prometheus",
                "labelEn": "Prometheus Scrape",
                "value": "настроен" if self.prometheus_path.exists() else "не найден",
                "valueEn": "configured" if self.prometheus_path.exists() else "not found",
                "note": "Берётся из `monitoring/prometheus.yml` и текущего стека наблюдаемости.",
                "noteEn": "Derived from `monitoring/prometheus.yml` and the current observability stack.",
                "tone": "lime" if self.prometheus_path.exists() else "orange",
            },
            {
                "label": "Панель Grafana",
                "labelEn": "Grafana Dashboard",
                "value": "готов" if self.dashboard_path.exists() else "не найден",
                "valueEn": "ready" if self.dashboard_path.exists() else "not found",
                "note": "Провиженится из локального monitoring-layout и доступен как прямая ссылка.",
                "noteEn": "Provisioned from the local monitoring layout and available as a deep link.",
                "tone": "lime" if self.dashboard_path.exists() else "orange",
            },
        ]
        return {
            "overview": overview,
            "services": services,
            "deploy": deploy,
        }

    def get_grafana_links(self) -> Dict[str, Any]:
        uid = self._dashboard_uid()
        overview_url = f"{self.grafana_base_url}/d/{uid}/{uid}"
        explore_url = f"{self.grafana_base_url}/explore"
        return {
            "base_url": self.grafana_base_url,
            "prometheus_url": self.prometheus_base_url,
            "dashboard_uid": uid,
            "links": [
                {
                    "title": "Общая панель",
                    "titleEn": "Overview Dashboard",
                    "description": "Сводный обзор HTTP-потока, задержек и панелей UMS.",
                    "descriptionEn": "Overview of HTTP rate, latency, and UMS runtime panels.",
                    "url": overview_url,
                    "surface": "overview",
                },
                {
                    "title": "Панель Agent API",
                    "titleEn": "Agent API Panel",
                    "description": "Открывает общую панель с фокусом на HTTP-потоке и задержках `agent_api`.",
                    "descriptionEn": "Opens the shared dashboard focused on `agent_api` HTTP rate and latency.",
                    "url": f"{overview_url}?viewPanel=1",
                    "surface": "services",
                },
                {
                    "title": "Панель UMS",
                    "titleEn": "UMS Panel",
                    "description": "Открывает панели по запущенным моделям, тяжёлой модели и насыщению.",
                    "descriptionEn": "Opens panels for running models, the heavy model, and saturation.",
                    "url": f"{overview_url}?viewPanel=5",
                    "surface": "services",
                },
                {
                    "title": "Раздел Explore",
                    "titleEn": "Grafana Explore",
                    "description": "Открывает Explore для ручной проверки меток, fallback-сигналов и задержек без смены панели.",
                    "descriptionEn": "Opens Explore for manual checks of labels, fallback signals, and latency without leaving the dashboard flow.",
                    "url": explore_url,
                    "surface": "services",
                },
                {
                    "title": "Панель деплоя",
                    "titleEn": "Deploy Dashboard",
                    "description": "Общая панель как опорная точка для деплоя, верификации и сигналов насыщения.",
                    "descriptionEn": "Shared dashboard as the anchor view for deploy, verification, and saturation signals.",
                    "url": overview_url,
                    "surface": "deploy",
                },
                {
                    "title": "Цели Prometheus",
                    "titleEn": "Prometheus Targets",
                    "description": "Переход к Prometheus для проверки целей опроса и текущего состояния сбора метрик.",
                    "descriptionEn": "Opens Prometheus targets for scrape checks and current metric collection state.",
                    "url": f"{self.prometheus_base_url}/targets",
                    "surface": "deploy",
                },
            ],
        }
