# Practice History Report

Этот каталог содержит воспроизводимый отчёт по практике на основе полной `git`-истории проекта.

## Что лежит внутри

- `report.tex` — основной `LaTeX`-документ.
- `data/practice_history_report.json` — структурированная сводка по коммитам, фазам, веткам и активным датам.
- `data/practice_history_timeline.csv` — дневниковая таблица по активным датам.
- `generated/*.tex` — автоматически собранные вставки для отчёта.

## Как обновить данные

Из корня репозитория:

```bash
python docs/reports/practice-history/history_practice_report.py --repo-root . --output-dir docs/reports/practice-history
```

## Как собрать PDF

Если в системе установлен `pdflatex`:

```bash
cd docs/reports/practice-history
pdflatex report.tex
pdflatex report.tex
```

Если установлен `latexmk`, можно использовать:

```bash
cd docs/reports/practice-history
latexmk -pdf report.tex
```

## Что редактировать вручную

В `report.tex` вынесены макросы:

- `\ReportAuthor`
- `\ReportGroup`
- `\ReportSupervisor`
- `\ReportUniversity`
- `\ReportDepartment`
- `\ReportCity`

Перед финальной сдачей их нужно заполнить реальными реквизитами.
