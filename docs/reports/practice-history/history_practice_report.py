#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


AUTHOR_ALIASES: dict[str, str] = {
    "senorfrancesco": "senorfrancesco",
    "sn_franc": "senorfrancesco",
    "snfranc": "senorfrancesco",
    "senor_francesco": "senorfrancesco",
    "aleksandrsego@gmail.com": "senorfrancesco",
    "105391984+senorfrancesco@users.noreply.github.com": "senorfrancesco",
    "manus ai": "Manus AI",
    "manus@ai.bot": "Manus AI",
    "manus@manus.im": "Manus AI",
    "copilot-swe-agent[bot]": "copilot-swe-agent[bot]",
    "198982749+copilot@users.noreply.github.com": "copilot-swe-agent[bot]",
    "gpt-engineer-app[bot]": "gpt-engineer-app[bot]",
    "159125892+gpt-engineer-app[bot]@users.noreply.github.com": "gpt-engineer-app[bot]",
    "anthropic-code-agent[bot]": "anthropic-code-agent[bot]",
    "242468646+claude@users.noreply.github.com": "anthropic-code-agent[bot]",
    "lovable": "Lovable",
    "noreply@lovable.dev": "Lovable",
}

TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("feat", re.compile(r"^(feat|feature)\b", re.IGNORECASE)),
    ("fix", re.compile(r"^fix\b", re.IGNORECASE)),
    ("docs", re.compile(r"^docs?\b", re.IGNORECASE)),
    ("refactor", re.compile(r"^refactor\b", re.IGNORECASE)),
    ("test", re.compile(r"^test\b", re.IGNORECASE)),
    ("chore", re.compile(r"^chore\b", re.IGNORECASE)),
    ("merge", re.compile(r"^merge\b", re.IGNORECASE)),
    ("revert", re.compile(r"^revert\b", re.IGNORECASE)),
]

THEME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openwebui", re.compile(r"openwebui|open webui|workspace tool|tool server|bootstrap", re.IGNORECASE)),
    ("chainlit", re.compile(r"chainlit", re.IGNORECASE)),
    ("rag", re.compile(r"\brag\b|kb\b|knowledge base|retrieval|embedding", re.IGNORECASE)),
    ("compare", re.compile(r"compare|semantic alignment|latency", re.IGNORECASE)),
    ("document", re.compile(r"document_analysis|documents summary|doc question|equipment|parser|ocr|legal", re.IGNORECASE)),
    ("runtime", re.compile(r"runtime|launcher|install|deploy|offline bundle|offline-bundle|preflight", re.IGNORECASE)),
    ("ums", re.compile(r"\bums\b|model|vllm|gpu|llama|registry|telemetry|observability|prometheus|grafana", re.IGNORECASE)),
    ("operator_ui", re.compile(r"operator-ui|operator ui|control plane|shell", re.IGNORECASE)),
    ("migration", re.compile(r"migration|migrate|split plan|reference architecture", re.IGNORECASE)),
    ("docs", re.compile(r"readme|guide|plan|flags|docs", re.IGNORECASE)),
]

THEME_LABELS: dict[str, str] = {
    "openwebui": "миграция и стабилизация `Open WebUI`",
    "chainlit": "поддержка и эволюция `Chainlit`-контура",
    "rag": "развитие `RAG`, хранилища знаний и поиска",
    "compare": "улучшение сценариев сравнения документов",
    "document": "обработка документов, парсинг и профильные workflow",
    "runtime": "рантайм, запуск, офлайн-развёртывание и инсталляция",
    "ums": "модели, `UMS`, распределение ресурсов и наблюдаемость",
    "operator_ui": "операторский интерфейс и управляющий контур",
    "migration": "архитектурная миграция и фиксация целевого контура",
    "docs": "документация и фиксация правил эксплуатации",
    "other": "общая стабилизация и организационные правки",
}

PHASES: list[dict[str, str]] = [
    {
        "slug": "bootstrap",
        "title": "Фаза 1. Сборка основы и ранняя интеграция",
        "start": "2026-01-05",
        "end": "2026-01-09",
        "focus": "Проект собирается из нескольких заготовок в единую систему: складываются backend, UI, потоковый режим, `UMS` и первые пользовательские сценарии.",
    },
    {
        "slug": "v3",
        "title": "Фаза 2. Переход к v3.0 и аппаратно-адаптивной архитектуре",
        "start": "2026-02-24",
        "end": "2026-03-05",
        "focus": "Появляется более зрелый контур `v3.0`: `Chainlit`, `AdaptiveRAGPipeline`, аппаратное профилирование, новые workflow и тестовое покрытие.",
    },
    {
        "slug": "stabilization",
        "title": "Фаза 3. Backend-first усиление и операционализация",
        "start": "2026-03-12",
        "end": "2026-03-31",
        "focus": "Команда переводит проект от набора функций к платформе: усиливаются `orchestrator`, `UMS`, реестр моделей, `operator UI`, офлайн-поставка и наблюдаемость.",
    },
    {
        "slug": "openwebui_first",
        "title": "Фаза 4. Консолидация вокруг Open WebUI-first",
        "start": "2026-04-01",
        "end": "2026-04-12",
        "focus": "Основной вектор смещается к `Open WebUI`: уточняется разделение ответственности между инструментами, bootstrap-процессом, рантаймом и миграционной документацией.",
    },
]

BRANCH_DESCRIPTIONS: dict[str, str] = {
    "dev": "Основная интеграционная ветка, в которой сходятся рабочие срезы перед закреплением следующего состояния проекта.",
    "refactor/openwebui-responsibility-split": "Текущая активная линия, где проект закрепляет `Open WebUI` как основной интерфейс и разводит зоны ответственности между bootstrap, tool-контуром и рантаймом.",
    "release/v0.1": "Исторический релизный срез на этапе начала миграционного контура; полезен как точка сравнения перед дальнейшей консолидацией.",
    "v0.1_history": "Дублирующий исторический срез той же точки, сохранённый как архивная ветка состояния.",
}

HIGHLIGHT_COMMITS: tuple[str, ...] = (
    "2b2010c",
    "d0c788d",
    "ceae15c",
    "85bd9cb",
    "fac57e5",
    "71c9c51",
)

CODE_SPAN_PATTERN = re.compile(r"`([^`]+)`")
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = SCRIPT_DIR.parents[2]


@dataclass(slots=True)
class Commit:
    sha: str
    short_sha: str
    date: str
    author_name: str
    author_email: str
    normalized_author: str
    subject: str
    commit_type: str
    themes: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="История Git проекта -> структурированные данные и TeX-вставки для отчётника по практике."
    )
    parser.add_argument(
        "--repo-root",
        default=str(DEFAULT_REPO_ROOT),
        help="Корень анализируемого Git-репозитория.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(SCRIPT_DIR),
        help="Каталог, куда будут записаны JSON/CSV и TeX-фрагменты.",
    )
    return parser.parse_args(argv)


def run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def normalize_author(name: str, email: str) -> str:
    lowered_name = name.strip().lower()
    lowered_email = email.strip().lower()
    return AUTHOR_ALIASES.get(lowered_email) or AUTHOR_ALIASES.get(lowered_name) or name.strip()


def classify_commit_type(subject: str) -> str:
    for name, pattern in TYPE_PATTERNS:
        if pattern.search(subject):
            return name
    return "other"


def classify_themes(subject: str) -> list[str]:
    themes: list[str] = []
    for theme, pattern in THEME_PATTERNS:
        if pattern.search(subject):
            themes.append(theme)
    return themes or ["other"]


def parse_commits(repo_root: Path) -> list[Commit]:
    raw = run_git(
        repo_root,
        "log",
        "--all",
        "--date=short",
        "--format=%H%x1f%h%x1f%ad%x1f%an%x1f%ae%x1f%s%x1e",
    )
    commits: list[Commit] = []
    for record in raw.strip("\x1e\n").split("\x1e"):
        if not record.strip():
            continue
        sha, short_sha, commit_date, author_name, author_email, subject = record.strip().split("\x1f")
        commits.append(
            Commit(
                sha=sha,
                short_sha=short_sha,
                date=commit_date,
                author_name=author_name,
                author_email=author_email,
                normalized_author=normalize_author(author_name, author_email),
                subject=subject.strip(),
                commit_type=classify_commit_type(subject.strip()),
                themes=classify_themes(subject.strip()),
            )
        )
    commits.sort(key=lambda item: (item.date, item.sha))
    return commits


def detect_working_start_date(commits: list[Commit]) -> str:
    dates = sorted({datetime.strptime(commit.date, "%Y-%m-%d").date() for commit in commits})
    if len(dates) < 2:
        return dates[0].isoformat()
    if (dates[1] - dates[0]).days > 30:
        return dates[1].isoformat()
    return dates[0].isoformat()


def monthly_activity(commits: list[Commit]) -> list[dict[str, object]]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for commit in commits:
        month = commit.date[:7]
        grouped[month]["total"] += 1
        grouped[month][commit.commit_type] += 1
    rows: list[dict[str, object]] = []
    for month in sorted(grouped):
        counter = grouped[month]
        rows.append(
            {
                "month": month,
                "total": counter["total"],
                "feat": counter["feat"],
                "fix": counter["fix"],
                "docs": counter["docs"],
                "refactor": counter["refactor"],
                "test": counter["test"],
                "chore": counter["chore"],
                "other": counter["other"],
            }
        )
    return rows


def top_authors(commits: list[Commit]) -> list[dict[str, object]]:
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    aliases: dict[str, set[str]] = defaultdict(set)
    for commit in commits:
        stats[commit.normalized_author]["commits"] += 1
        aliases[commit.normalized_author].add(commit.author_name)
        aliases[commit.normalized_author].add(commit.author_email)
    rows = []
    for author, counter in stats.items():
        rows.append(
            {
                "name": author,
                "commits": counter["commits"],
                "aliases": sorted(alias for alias in aliases[author] if alias),
            }
        )
    rows.sort(key=lambda item: (-int(item["commits"]), str(item["name"])))
    return rows


def group_by_day(commits: list[Commit]) -> list[dict[str, object]]:
    grouped: dict[str, list[Commit]] = defaultdict(list)
    for commit in commits:
        grouped[commit.date].append(commit)

    days: list[dict[str, object]] = []
    for day in sorted(grouped):
        day_commits = grouped[day]
        type_counter = Counter(commit.commit_type for commit in day_commits)
        theme_counter = Counter(theme for commit in day_commits for theme in commit.themes)
        focus_themes = [theme for theme, _count in theme_counter.most_common(2)]
        days.append(
            {
                "date": day,
                "commit_count": len(day_commits),
                "types": dict(type_counter),
                "themes": dict(theme_counter),
                "focus": humanize_theme_focus(focus_themes),
                "summary": build_day_summary(day, day_commits, focus_themes, type_counter),
                "highlights": [
                    {"sha": commit.short_sha, "subject": commit.subject}
                    for commit in select_highlight_commits(day_commits, limit=3)
                ],
            }
        )
    return days


def humanize_theme_focus(themes: Iterable[str]) -> str:
    theme_list = [THEME_LABELS.get(theme, THEME_LABELS["other"]) for theme in themes if theme]
    if not theme_list:
        return THEME_LABELS["other"]
    if len(theme_list) == 1:
        return theme_list[0]
    return f"{theme_list[0]} и {theme_list[1]}"


def build_day_summary(
    day: str,
    commits: list[Commit],
    focus_themes: list[str],
    type_counter: Counter[str],
) -> str:
    focus = humanize_theme_focus(focus_themes)
    if type_counter["feat"] >= max(type_counter["fix"], type_counter["docs"], type_counter["refactor"]):
        action = "добавлялись новые возможности и связывались крупные части системы"
    elif type_counter["fix"] >= max(type_counter["feat"], type_counter["docs"], type_counter["refactor"]):
        action = "основной упор делался на стабилизацию уже внедрённых решений и снятие регрессий"
    elif type_counter["docs"] >= max(type_counter["feat"], type_counter["fix"], type_counter["refactor"]):
        action = "команда фиксировала архитектурные решения, правила эксплуатации и план следующего шага"
    else:
        action = "параллельно шли структурные улучшения и уточнение технического контура"

    notable = select_highlight_commits(commits, limit=2)
    if notable:
        examples = "; ".join(f"{commit.short_sha} — {commit.subject}" for commit in notable)
        example_text = f" Особенно хорошо это видно по коммитам {examples}."
    else:
        example_text = ""

    return (
        f"{day} работа была сосредоточена на направлении «{focus}». "
        f"По истории коммитов видно, что в этот день {action}.{example_text}"
    )


def select_highlight_commits(commits: list[Commit], *, limit: int) -> list[Commit]:
    scored = sorted(
        commits,
        key=lambda item: (
            item.short_sha not in HIGHLIGHT_COMMITS,
            item.commit_type == "other",
            len(item.themes),
            item.subject,
        ),
        reverse=False,
    )
    return scored[:limit]


def commits_in_range(commits: list[Commit], start: str, end: str) -> list[Commit]:
    return [commit for commit in commits if start <= commit.date <= end]


def build_phases(commits: list[Commit]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for phase in PHASES:
        phase_commits = commits_in_range(commits, phase["start"], phase["end"])
        theme_counter = Counter(theme for commit in phase_commits for theme in commit.themes)
        type_counter = Counter(commit.commit_type for commit in phase_commits)
        highlights = select_highlight_commits(phase_commits, limit=5)
        rows.append(
            {
                "slug": phase["slug"],
                "title": phase["title"],
                "start_date": phase["start"],
                "end_date": phase["end"],
                "focus": phase["focus"],
                "commit_count": len(phase_commits),
                "type_counts": dict(type_counter),
                "top_themes": [theme for theme, _count in theme_counter.most_common(4)],
                "summary": build_phase_summary(phase["focus"], phase_commits, theme_counter, type_counter),
                "highlight_commits": [
                    {"sha": commit.short_sha, "subject": commit.subject, "date": commit.date}
                    for commit in highlights
                ],
            }
        )
    return rows


def build_phase_summary(
    focus_text: str,
    commits: list[Commit],
    theme_counter: Counter[str],
    type_counter: Counter[str],
) -> str:
    if not commits:
        return focus_text
    top_theme_labels = humanize_theme_focus([theme for theme, _count in theme_counter.most_common(2)])
    if type_counter["feat"] >= type_counter["fix"]:
        suffix = "Это был период ускоренного наращивания продукта и архитектуры."
    else:
        suffix = "Это был период шлифовки, когда устойчивость становилась важнее простого прироста функций."
    return f"{focus_text} По коммитам доминируют {top_theme_labels}. {suffix}"


def get_branch_tip(repo_root: Path, branch: str) -> dict[str, str] | None:
    try:
        raw = run_git(repo_root, "log", "--no-merges", "-n", "1", "--date=short", "--format=%h%x1f%ad%x1f%s", branch)
    except RuntimeError:
        return None
    if not raw.strip():
        return None
    short_sha, commit_date, subject = raw.strip().split("\x1f")
    return {"sha": short_sha, "date": commit_date, "subject": subject}


def ahead_behind_vs_dev(repo_root: Path, branch: str) -> dict[str, int]:
    if branch == "dev":
        return {"ahead": 0, "behind": 0}
    try:
        raw = run_git(repo_root, "rev-list", "--left-right", "--count", f"dev...{branch}")
    except RuntimeError:
        return {"ahead": 0, "behind": 0}
    left, right = raw.strip().split()
    return {"behind": int(left), "ahead": int(right)}


def branch_highlights(repo_root: Path, branch: str) -> list[dict[str, str]]:
    try:
        raw = run_git(
            repo_root,
            "log",
            "--no-merges",
            "-n",
            "4",
            "--date=short",
            "--format=%h%x1f%ad%x1f%s",
            branch,
        )
    except RuntimeError:
        return []
    highlights: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        short_sha, commit_date, subject = line.split("\x1f")
        highlights.append({"sha": short_sha, "date": commit_date, "subject": subject})
    return highlights


def build_branches(repo_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for branch_name in BRANCH_DESCRIPTIONS:
        tip = get_branch_tip(repo_root, branch_name)
        if tip is None:
            continue
        rows.append(
            {
                "name": branch_name,
                "description": BRANCH_DESCRIPTIONS[branch_name],
                "status": "active" if branch_name == current_branch(repo_root) else "reference",
                "ahead_behind_vs_dev": ahead_behind_vs_dev(repo_root, branch_name),
                "tip": tip,
                "highlights": branch_highlights(repo_root, branch_name),
            }
        )
    return rows


def supporting_remote_branches(repo_root: Path) -> list[dict[str, str]]:
    raw = run_git(repo_root, "branch", "-a", "--format=%(refname:short)")
    items: list[dict[str, str]] = []
    for branch in sorted(line.strip() for line in raw.splitlines() if line.strip()):
        if not branch.startswith("origin/"):
            continue
        if branch in {f"origin/{name}" for name in BRANCH_DESCRIPTIONS}:
            continue
        description = infer_remote_branch_description(branch)
        items.append({"name": branch, "description": description})
    return items


def infer_remote_branch_description(branch: str) -> str:
    if "compare" in branch:
        return "Точечная ветка для корректировки compare-workflow и снижения расхождений в LLM-обработке."
    if "copilot" in branch:
        return "Служебная ветка для узкой правки зависимостей, очистки или проверки конкретного сценария."
    if "codex" in branch:
        return "Снимок состояния оркестрационного контура в отдельный момент разработки."
    return "Узкая вспомогательная ветка под отдельный инженерный срез."


def current_branch(repo_root: Path) -> str:
    return run_git(repo_root, "branch", "--show-current").strip()


def busiest_days(days: list[dict[str, object]], *, limit: int = 5) -> list[dict[str, object]]:
    ordered = sorted(days, key=lambda item: (-int(item["commit_count"]), str(item["date"])))
    return ordered[:limit]


def make_payload(repo_root: Path) -> dict[str, object]:
    commits = parse_commits(repo_root)
    days = group_by_day(commits)
    phases = build_phases(commits)
    branches = build_branches(repo_root)
    support_branches = supporting_remote_branches(repo_root)
    working_start = detect_working_start_date(commits)
    head_branch = current_branch(repo_root)
    type_counter = Counter(commit.commit_type for commit in commits)

    payload: dict[str, object] = {
        "summary": {
            "total_commits": len(commits),
            "date_range": {"start": commits[0].date, "end": commits[-1].date},
            "working_start_date": working_start,
            "head_branch": head_branch,
            "active_days": len(days),
            "type_counts": dict(type_counter),
            "monthly_activity": monthly_activity(commits),
            "busiest_days": busiest_days(days),
            "top_authors": top_authors(commits),
        },
        "main_phases": phases,
        "branches": branches,
        "supporting_remote_branches": support_branches,
        "days": days,
    }
    return payload


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    escaped = value
    for source, target in replacements.items():
        escaped = escaped.replace(source, target)
    return escaped


def latexify_text(value: str) -> str:
    parts: list[str] = []
    last_index = 0
    for match in CODE_SPAN_PATTERN.finditer(value):
        plain_part = value[last_index:match.start()]
        if plain_part:
            parts.append(latex_escape(plain_part))
        parts.append(r"\texttt{%s}" % latex_escape(match.group(1)))
        last_index = match.end()
    tail = value[last_index:]
    if tail:
        parts.append(latex_escape(tail))
    return "".join(parts)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_timeline_csv(path: Path, days: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["date", "commit_count", "focus", "summary"])
        for day in days:
            writer.writerow([day["date"], day["commit_count"], day["focus"], day["summary"]])


def write_metrics_tex(path: Path, payload: dict[str, object]) -> None:
    summary = payload["summary"]
    top_authors_rows = "\n".join(
        f"{latex_escape(author['name'])} & {author['commits']} \\\\"
        for author in summary["top_authors"][:5]
    )
    busiest_rows = "\n".join(
        f"{day['date']} & {day['commit_count']} \\\\"
        for day in summary["busiest_days"]
    )
    content = rf"""
\begin{{itemize}}
\item Всего коммитов в анализируемой истории: {summary['total_commits']}.
\item Диапазон истории: {summary['date_range']['start']} -- {summary['date_range']['end']}.
\item Рабочая линия проекта начинается с {summary['working_start_date']}, потому что более ранний коммит выглядит как шаблонный стартовый импорт.
\item Текущая рабочая ветка на момент подготовки отчёта: \texttt{{{latex_escape(summary['head_branch'])}}}.
\item Всего активных дат с коммитами: {summary['active_days']}.
\end{{itemize}}

\begin{{table}}[h]
\centering
\begin{{tabular}}{{lr}}
\textbf{{Автор}} & \textbf{{Коммитов}} \\
\hline
{top_authors_rows}
\end{{tabular}}
\caption{{Наиболее активные авторы в истории проекта}}
\end{{table}}

\begin{{table}}[h]
\centering
\begin{{tabular}}{{lr}}
\textbf{{Дата}} & \textbf{{Коммитов}} \\
\hline
{busiest_rows}
\end{{tabular}}
\caption{{Самые насыщенные дни разработки}}
\end{{table}}
""".strip()
    path.write_text(content + "\n", encoding="utf-8")


def write_branches_tex(path: Path, payload: dict[str, object]) -> None:
    branch_rows = []
    for branch in payload["branches"]:
        branch_rows.append(
            "\\texttt{%s} & %s & +%s / -%s \\\\"
            % (
                latex_escape(branch["name"]),
                latexify_text(branch["description"]),
                branch["ahead_behind_vs_dev"]["ahead"],
                branch["ahead_behind_vs_dev"]["behind"],
            )
        )
    supporting = "\n".join(
        f"\\item \\texttt{{{latex_escape(item['name'])}}} --- {latexify_text(item['description'])}"
        for item in payload["supporting_remote_branches"]
    )
    content = rf"""
\begin{{table}}[h]
\centering
\begin{{tabular}}{{p{{0.25\textwidth}} p{{0.5\textwidth}} r}}
\textbf{{Ветка}} & \textbf{{Смысл}} & \textbf{{vs dev}} \\
\hline
{chr(10).join(branch_rows)}
\end{{tabular}}
\caption{{Ключевые ветки, которые использованы в отчёте}}
\end{{table}}

\paragraph{{Точечные удалённые ветки.}} Помимо основных линий, в истории есть короткие вспомогательные ветки под узкие задачи. Они важны как маркеры направления, но не образуют самостоятельную продуктовую линию.
\begin{{itemize}}
{supporting}
\end{{itemize}}
""".strip()
    path.write_text(content + "\n", encoding="utf-8")


def write_phases_tex(path: Path, payload: dict[str, object]) -> None:
    parts: list[str] = []
    for phase in payload["main_phases"]:
        highlights = "\n".join(
            f"\\item \\texttt{{{latex_escape(item['sha'])}}} ({item['date']}) --- {latexify_text(item['subject'])}"
            for item in phase["highlight_commits"]
        )
        parts.append(
            rf"""
\subsection*{{{latex_escape(phase['title'])}}}
\textbf{{Период:}} {phase['start_date']} -- {phase['end_date']} \\
\textbf{{Коммитов в фазе:}} {phase['commit_count']}

{latexify_text(phase['summary'])}

\begin{{itemize}}
{highlights}
\end{{itemize}}
""".strip()
        )
    path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")


def write_timeline_tex(path: Path, payload: dict[str, object]) -> None:
    rows = []
    for day in payload["days"]:
        rows.append(
            r"%s & %s & %s & %s \\"
            % (
                day["date"],
                day["commit_count"],
                latexify_text(day["focus"]),
                latexify_text(day["summary"]),
            )
        )
    content = rf"""
\begingroup
\small
\setlength{{\LTleft}}{{0pt}}
\setlength{{\LTright}}{{0pt}}
\begin{{longtable}}{{p{{0.1\textwidth}} p{{0.08\textwidth}} p{{0.22\textwidth}} p{{0.52\textwidth}}}}
\textbf{{Дата}} & \textbf{{Комм.}} & \textbf{{Фокус дня}} & \textbf{{Что было сделано и зачем}} \\
\hline
\endfirsthead
\textbf{{Дата}} & \textbf{{Комм.}} & \textbf{{Фокус дня}} & \textbf{{Что было сделано и зачем}} \\
\hline
\endhead
{chr(10).join(rows)}
\end{{longtable}}
\endgroup
""".strip()
    path.write_text(content + "\n", encoding="utf-8")


def ensure_output_dirs(output_dir: Path) -> tuple[Path, Path]:
    data_dir = output_dir / "data"
    generated_dir = output_dir / "generated"
    data_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, generated_dir


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    payload = make_payload(repo_root)
    data_dir, generated_dir = ensure_output_dirs(output_dir)

    write_json(data_dir / "practice_history_report.json", payload)
    write_timeline_csv(data_dir / "practice_history_timeline.csv", payload["days"])
    write_metrics_tex(generated_dir / "practice_history_metrics.tex", payload)
    write_branches_tex(generated_dir / "practice_history_branches.tex", payload)
    write_phases_tex(generated_dir / "practice_history_phases.tex", payload)
    write_timeline_tex(generated_dir / "practice_history_timeline.tex", payload)

    print(
        json.dumps(
            {
                "status": "ok",
                "repoRoot": str(repo_root),
                "outputDir": str(output_dir),
                "totalCommits": payload["summary"]["total_commits"],
                "workingStartDate": payload["summary"]["working_start_date"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
