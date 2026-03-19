from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from orchestrator.shared import report_utils


def test_save_report_with_dedup_writes_markdown_and_pdf_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))

    def _fake_write_pdf(report_text: str, filepath: str) -> None:
        Path(filepath).write_bytes(b"%PDF-1.4\nstub\n")

    monkeypatch.setattr(report_utils, "_write_pdf_report", _fake_write_pdf)

    result = report_utils.save_report_with_dedup(
        report_text="# Отчет\n\n| A | B |\n| --- | --- |\n| 1 | 2 |",
        prefix="Report_Test",
        input_names=["contract.pdf"],
        current_metric=2,
        metric_marker="Значение:**",
    )

    assert "Отчет сохранен" in result
    assert len(list(tmp_path.glob("Report_Test_*.pdf"))) == 1
    assert len(list(tmp_path.glob("Report_Test_*.md"))) == 1


def test_save_report_with_dedup_uses_markdown_artifact_for_duplicate_check(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    base = tmp_path / "Report_Test_123"
    base.with_suffix(".md").write_text(
        "# Отчет\n\ncontract.pdf\n\n**Значение:** 3\n",
        encoding="utf-8",
    )
    base.with_suffix(".pdf").write_bytes(b"%PDF-1.4\nstub\n")

    result = report_utils.save_report_with_dedup(
        report_text="# Отчет\n\ncontract.pdf\n\n**Значение:** 2\n",
        prefix="Report_Test",
        input_names=["contract.pdf"],
        current_metric=2,
        metric_marker="Значение:**",
    )

    assert "уже сохранен" in result
    assert len(list(tmp_path.glob("Report_Test_*.pdf"))) == 1
    assert len(list(tmp_path.glob("Report_Test_*.md"))) == 1


def test_write_pdf_report_uses_weasyprint_when_available(tmp_path, monkeypatch):
    target = tmp_path / "styled.pdf"

    fake_markdown = SimpleNamespace(markdown=lambda text, extensions, output_format: "<h1>Styled</h1><p>Body</p>")

    class _FakeHTML:
        def __init__(self, string: str, base_url: str | None = None) -> None:
            self.string = string
            self.base_url = base_url

        def write_pdf(self, filepath: str) -> None:
            Path(filepath).write_text(self.string, encoding="utf-8")

    fake_weasyprint = SimpleNamespace(HTML=_FakeHTML)
    monkeypatch.setitem(sys.modules, "markdown", fake_markdown)
    monkeypatch.setitem(sys.modules, "weasyprint", fake_weasyprint)

    report_utils._write_pdf_report("# Styled\n\nBody", str(target))

    content = target.read_text(encoding="utf-8")
    assert "<h1>Styled</h1>" in content
    assert "<style>" in content


def test_write_pdf_report_falls_back_to_plain_text_renderer(monkeypatch, tmp_path):
    target = tmp_path / "fallback.pdf"
    called: dict[str, bool] = {"plain": False}

    def _fake_weasyprint(report_text: str, filepath: str) -> None:
        raise RuntimeError("missing weasy deps")

    def _fake_plain(report_text: str, filepath: str) -> None:
        called["plain"] = True
        Path(filepath).write_bytes(b"%PDF-1.4\nfallback\n")

    monkeypatch.setattr(report_utils, "_write_weasyprint_pdf_report", _fake_weasyprint)
    monkeypatch.setattr(report_utils, "_write_plain_text_pdf_report", _fake_plain)

    report_utils._write_pdf_report("# Fallback", str(target))

    assert called["plain"] is True
    assert target.exists()
