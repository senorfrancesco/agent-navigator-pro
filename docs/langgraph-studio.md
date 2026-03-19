# LangGraph Studio

Канонический конфиг для Studio лежит в [backend/langgraph.json](../backend/langgraph.json).

Зарегистрированные графы:
- `compare` -> `orchestrator/workflows/compare.py:create_compare_graph`
- `equipment` -> `orchestrator/workflows/equipment.py:create_equipment_graph`
- `document_analysis` -> `orchestrator/workflows/document_analysis.py:create_analysis_graph`

Запуск:

```bash
cd backend
langgraph dev
```

Проверка entrypoints без Studio CLI:

```bash
cd /home/seral/HDD/proj/agent-navigator-pro
python - <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), 'backend'))
from orchestrator.workflows.compare import create_compare_graph
from orchestrator.workflows.equipment import create_equipment_graph
from orchestrator.workflows.document_analysis import create_analysis_graph

for name, factory in [
    ("compare", create_compare_graph),
    ("equipment", create_equipment_graph),
    ("document_analysis", create_analysis_graph),
]:
    print(name, type(factory()).__name__)
PY
```

Если `langgraph: command not found`, в текущем Python env установлен пакет `langgraph`, но не установлен CLI. Тогда сначала нужно поставить LangGraph CLI в тот же env, из которого запускается Studio.
