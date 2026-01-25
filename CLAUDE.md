# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Navigator Pro is a microservices-based agent system for analyzing legal documents and estimates. Built with Python 3.11, FastAPI, LangGraph, and local LLM models (Qwen) for privacy and security.

**Key Architecture:**
- **Frontend:** Open WebUI (Docker container) on port 3000
- **Backend:** Python microservices on host (ports 8000-8090)
- **File Exchange:** Bind-mounted `backend/open_webui_uploads/` directory
- **Orchestration:** LangGraph workflows for document comparison and equipment analysis

## Development Commands

### Environment Setup

```bash
# Activate conda environment (required for all operations)
conda activate diploma_llm

# Or use quick activation script (Linux)
source activate_env.sh

# Windows
activate_env.bat
```

### Running the System

**Complete system startup (Linux/macOS):**
```bash
cd backend
./run_all.sh
```

This launches a tmux session named `agent-navigator` with 6 windows:
- `webui`: Docker compose logs (Open WebUI)
- `agent-api`: Main orchestrator API (port 8000)
- `doc-server`: Document parsing service (port 8001)
- `legal-server`: Legal analysis service (port 8002)
- `ums`: Unified Model Server (port 8090)
- `monitor`: System monitoring (htop/top)

**Windows startup:**
```cmd
start_all_services.bat
```

**Attach to running tmux session:**
```bash
tmux attach-session -t agent-navigator
```

**Stop all services:**
```bash
tmux kill-session -t agent-navigator
```

### Testing

**System integration test:**
```bash
./start_system_test.sh
```

Validates all services are running and responding on correct ports.

**Unit tests:**
```bash
cd backend
pytest tests/
```

**Specific test:**
```bash
pytest tests/test_integration_full.py -v
```

### Manual Service Startup

If you need to run services individually:

```bash
# Agent API (main orchestrator)
cd backend/orchestrator
python agent_api.py
# or: uvicorn agent_api:app --host 0.0.0.0 --port 8000

# Document Server
cd backend/services/document_server
uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001

# Legal Server
cd backend/services/legal_server
uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002

# Unified Model Server
cd backend/services/model_manager
python unified_model_server.py
```

### Docker Operations

```bash
# Start Open WebUI
docker compose up -d

# View logs
docker compose logs -f

# Restart
docker compose restart

# Stop
docker compose down
```

## Architecture Deep Dive

### Request Flow

1. **User → Open WebUI (Docker:3000)** - Chat interface, file uploads
2. **Open WebUI → Agent API (Host:8000)** - OpenAI-compatible `/v1/chat/completions`
3. **Agent API → Workflow Selection** - Routes to LangGraph or direct model
4. **Workflows → Microservices** - Parallel calls to doc/legal/model servers
5. **Response Stream** - SSE back through Open WebUI to user

### LangGraph Workflows

Located in `backend/orchestrator/workflows/`:

**compare.py** - Document comparison workflow:
- `load_documents_node`: Fetch and chunk documents via Document Server
- `batch_match_node`: Semantic matching via Legal Server
- `deep_compare_node`: LLM analysis of matched pairs via UMS
- `generate_report_node`: Final markdown report generation

**equipment.py** - Equipment/estimate analysis workflow:
- Similar structure but specialized for technical specifications vs estimates

**State Management:**
- Uses TypedDict for typed state
- State flows through nodes via `StateGraph`
- Each node returns dict updates merged into state

### Microservices

**Document Server (8001):**
- `/load_document`: Parse PDF/DOCX to text (uses pdfplumber, python-docx)
- `/chunk_text`: Smart chunking by sections
- Handles OCR for scanned documents

**Legal Server (8002):**
- `/batch_match`: Semantic similarity matching between document chunks
- Uses SentenceTransformers (LaBSE, E5-legal, Rubert)
- Returns cosine similarity scores

**Unified Model Server (8090):**
- `/generate`: LLM text generation (Qwen models via llama-cpp-python)
- `/embed`: Generate embeddings
- Manages GGUF model loading and GPU memory

**Agent API (8000):**
- `/v1/chat/completions`: OpenAI-compatible endpoint
- `/v1/models`: List available models
- `/health`: System health check
- Routes to workflows based on query intent and attachments

### Configuration

**Environment variables (.env):**
- Model paths must point to actual GGUF files in `backend/models/gguf/`
- SentenceTransformer paths in `backend/models/st/`
- GPU layer settings: `-1` = all layers on GPU
- Temperature/top_p for generation quality vs creativity

**Critical paths:**
- `MODEL_PATH_QWEN14B`: Main LLM model (required)
- `MODEL_PATH_LABSE`: Embeddings model (required for compare/equipment workflows)
- `BACKEND_MODE`: Default is `llama-cpp-python`

### Workflow Routing Logic

Agent API uses soft ReAct routing:

```python
# From agent_api.py run_workflow_stream()
is_compare_intent = any(kw in query_lower for kw in ["сравни", "различия", "изменения"])
is_equipment_intent = any(kw in query_lower for kw in ["смета", "оборудование", "тз"])
file_count = len(attachments)

# 2+ files + intent → LangGraph workflow
if file_count >= 2 and (is_compare_intent or is_equipment_intent):
    workflow = create_compare_graph() or create_equipment_graph()
```

No files or different intent → Direct LLM chat via UMS.

### Shared Storage Pattern

Open WebUI runs in Docker but backend runs on host. Files uploaded in UI are saved to `backend/open_webui_uploads/` via bind mount:

```yaml
# docker-compose.yaml
volumes:
  - ./backend/open_webui_uploads:/app/backend/data/uploads
```

Agent API reads files from this same directory - critical for file-based workflows.

## Key Development Patterns

### Adding a New Workflow

1. Create `backend/orchestrator/workflows/your_workflow.py`
2. Define state TypedDict with all required fields
3. Implement nodes as async functions returning state updates
4. Build StateGraph: `graph.add_node()`, `graph.add_edge()`, `graph.compile()`
5. Import and register in `agent_api.py` routing logic
6. Add intent keywords to dispatcher

### LLM Prompting

Use Qwen chat template format:
```python
prompt = """<|im_start|>system
You are an expert assistant.<|im_end|>
<|im_start|>user
{user_query}<|im_end|>
<|im_start|>assistant
"""
```

For structured output, request JSON and use `orchestrator/utils.py::parse_json_garbage()` to extract from markdown-wrapped responses.

### Microservice Communication

All services use `httpx.AsyncClient` for async HTTP:

```python
async with httpx.AsyncClient(timeout=60.0) as client:
    resp = await client.post(f"{SERVICE_URL}/endpoint", json={...})
    resp.raise_for_status()
    result = resp.json()
```

Services expose `/health` endpoints for monitoring.

### Error Handling

Workflows accumulate errors in state:
```python
state["errors"].append(f"Service failed: {str(e)}")
```

Final report includes error summary if `state["errors"]` is non-empty.

## Common Issues

**tmux session fails to start:**
- Conda environment not activated: check `conda env list`
- Conda path not found: update paths in `run_all.sh` find_conda()

**Port already in use:**
```bash
# Find process
lsof -i :8000  # Linux/macOS
netstat -ano | findstr :8000  # Windows

# Kill and restart
```

**UMS fails to load models:**
- Check `.env` paths point to actual GGUF files
- Verify GPU memory: `nvidia-smi`
- Check logs in tmux `ums` window

**Files not found in workflows:**
- Verify uploads are in `backend/open_webui_uploads/`
- Check file paths in attachments match actual filenames
- Ensure Docker bind mount is correct in docker-compose.yaml

## Branch: refactor/middleware-agent

This branch includes middleware refactoring with improved MCP (Model Context Protocol) integration. The architecture separates concerns:
- Orchestrator handles routing and workflow execution
- MCP servers (document, legal) provide specialized tools
- UMS abstracts model backend (llama-cpp-python, llama-server, vllm)

When making changes, preserve this separation - don't mix orchestration logic into service layers.
