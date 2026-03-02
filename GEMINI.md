# GEMINI.md

This file provides guidance to Gemini CLI and AI coding assistants when working with this repository.

## Project Overview

Agent Navigator Pro — microservice-based agentic system for analyzing legal documents and cost estimates (smeta). Python 3.11, FastAPI, LangGraph, local LLMs (Qwen-14B) via llama-server. Branch: `feature/v3.0-agentic-system`.

**Language:** All user-facing text, comments, commit messages, and documentation are in **Russian**. Code identifiers and technical terms remain in English.

**Architecture:**
- **Frontend:** Chainlit (Docker, port 3000) — chat UI, file uploads, `cl.Step` for workflow visualization
- **Backend:** Python microservices on host (ports 8000-8090)
- **File Exchange:** Bind-mount `backend/open_webui_uploads/` → `/app/uploads` in container
- **Orchestration:** LangGraph workflows for document comparison, equipment analysis, single-document analysis
- **RAG:** Adaptive tiered pipeline (BM25 + Dense + RRF, no external LLM calls for classification)
- **Hardware:** Auto GPU/CPU profiling → tier selection → gpu_layers/ctx_size calculation
- **LLM:** Qwen2.5-14B-Instruct (Q4_K_M GGUF) via llama-server, 2x RTX 2070 (tensor-split)
- **Embeddings:** LaBSE (ONNX FP32 export, 1.2-1.4x faster than SentenceTransformers on CPU)

---

## Development Commands

### Environment

```bash
conda activate diploma_llm
# or
source activate_env.sh
```

### System Startup

```bash
./scripts/run_all.sh        # Full startup (tmux + Docker + health-checks + model loading wait)
./scripts/stop_all.sh        # Stop all services
./scripts/restart_all.sh     # Restart
tmux attach-session -t agent-navigator  # Attach to session
```

`run_all.sh` creates tmux session `agent-navigator` with 6 windows:
`chainlit` → `agent-api` → `doc-server` → `legal-server` → `ums` → `monitor`

The script waits for `/health` from each service and Qwen LLM loading via UMS `/status` (up to 3 minutes).

**Docker image is NOT rebuilt on each startup.** To rebuild after code changes:
```bash
docker compose build chainlit
```

### Manual Service Startup

```bash
cd backend/orchestrator && python agent_api.py                                    # port 8000
cd backend/services/document_server && uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001
cd backend/services/legal_server && uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002
cd backend/services/model_manager && python unified_model_server.py               # port 8090
```

### Tests

```bash
cd backend && pytest tests/ -v                                    # All unit tests (~98 tests)
cd backend && pytest tests/test_equipment_workflow.py -v           # Equipment workflow tests
cd backend && pytest tests/test_document_analysis.py -v            # Document analysis tests
cd backend && pytest tests/test_e2e_equipment.py -v -m integration # E2E (requires running services)
./start_system_test.sh                                            # Integration test of all services
```

### Checking GPU / CUDA

```bash
nvidia-smi                                                        # GPU status
python3 -c "import torch; print(torch.cuda.is_available())"       # CUDA availability
curl -s http://localhost:8090/status | python3 -m json.tool        # UMS model status
llama-server --version 2>&1 | head -3                             # Check for CUDA init errors
```

**Known issue:** After kernel update or suspend/resume, CUDA may fail with `ggml_cuda_init: failed to initialize CUDA: unknown error`. Fix: `sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm`, then restart UMS. If that fails, reboot.

---

## Architecture

### Two Input Streams

**Chainlit (primary)** — `chainlit_app.py` runs inside Docker, directly calls LangGraph workflows and `ums_client`. Does NOT proxy through agent_api.

**Agent API (legacy/compatibility)** — `agent_api.py` on host (port 8000), OpenAI-compatible `/v1/chat/completions`. Used by Open WebUI and external clients.

### Request Flow (Chainlit)

1. User → Chainlit (Docker:3000) — chat, file upload
2. `_detect_intent()` → determines intent by keywords + file count
3. Files → HTTP POST to Document Server → parsing + chunking
4. Intent `compare_documents` / `equipment_analysis` / `document_analysis` → LangGraph workflow (each node wrapped in `cl.Step`)
5. Intent `document_question` → `AdaptiveRAGPipeline` (or fallback to naive context stuffing)
6. Intent `general_chat` → `ums_client.async_infer_stream()` → SSE streaming

### Intent Detection (`_detect_intent()`)

Two-level system:
1. **Semantic Router** (EmbeddingIntentClassifier) — if initialized
2. **Keyword fallback** — if classifier unavailable

Double gate for expensive workflows: Semantic Router + keyword confirmation + file count check.

| Intent | Condition | Action |
|--------|-----------|--------|
| `compare_documents` | 2+ files + compare keywords | Compare workflow |
| `equipment_analysis` | 2+ files + equipment keywords | Equipment workflow |
| `document_analysis` | 1 file + analysis keywords | Single-doc analysis workflow |
| `document_question` | Has docs + doc keywords | RAG Q&A |
| `greeting` | Greeting keywords | Simple chat |
| `general_chat` | Default | LLM chat |

### LangGraph Workflows

`backend/orchestrator/workflows/`:

**compare.py** — Document Comparison:
```
load_documents_node → batch_match_node (LaBSE) → deep_compare_node (LLM) → generate_report_node → END
```

**equipment.py** — Equipment/Estimate Analysis (TZ vs Smeta):
```
extract → [route] → match → evaluate → report → END
         ↘ (items empty) → report (error) → END
```
- Two-pass extraction: structural tables (no LLM) + LLM text extraction (map-reduce chunking)
- Semantic matching via Legal Server (LaBSE + Hungarian algorithm)
- Batch LLM evaluation (BATCH_SIZE=5)
- Supports `tz_vs_smeta` and `smeta_vs_smeta` modes

**document_analysis.py** — Single Document Analysis:
```
classify → extract → summarize → report → END
```
- Keyword-based document classification (tz/smeta/kp/legal/other) — no LLM
- Reuses `_extract_tables_from_doc`, `_extract_items_llm`, `_dedup_items` from equipment.py
- Map-reduce summarization with prompts adapted per document type
- `_DOC_TYPE_KEYWORDS`, `_SUMMARY_PROMPTS`, `_DOC_TYPE_LABELS` — configurable dictionaries

State — TypedDict, each node returns dict updates. Errors accumulate in `state["errors"]`.

### Adaptive RAG Pipeline

`backend/orchestrator/rag/`:

| Tier | Mode | Strategy |
|------|------|----------|
| 1 | `simple` | BM25+Dense → RRF → top-5 → Generate |
| 2 | `corrective` | EmbeddingIntentClassifier → Hybrid → Z-score grading → (pass/Rocchio expand) |
| 3 | `agentic` | Iterative reformulation (up to 3 iterations, Rocchio) |
| 4 | `multi-agent` | Fallback to agentic (LangGraph version in backlog) |

- **`retriever.py`** — BM25 for Russian text (built-in stop words), Dense via LaBSE, RRF fusion (k=60), Z-score grading
- **`classifier.py`** — Centroid-based intent classification via embeddings (no LLM)
- **`chunker.py`** — Section-aware chunking for legal documents
- **`pipeline.py`** — Tier selection orchestration

### Microservices

| Service | Port | Key Endpoints |
|---------|------|---------------|
| Agent API | 8000 | `/v1/chat/completions`, `/v1/models`, `/health` |
| Document Server | 8001 | `/load_document`, `/load_pages`, `/extract_tables`, `/extract_tables_docx`, `/extract_tables_excel`, `/smart_chunk`, `/chunk_text`, `/health` |
| Legal Server | 8002 | `/match_batches`, `/batch_match`, `/health` |
| UMS | 8090 | `/infer`, `/v1/embeddings`, `/status`, `/health` |

### Unified Model Server (UMS)

`backend/services/model_manager/unified_model_server.py` — manages model lifecycle:

- **Preloading:** Qwen-14B LLM starts automatically at UMS startup (lifespan)
- **Hardware profiling:** At startup: GPU/CPU detection → TierSelector → VRAMCalculator → computes `gpu_layers` and `ctx_size`
- **Dynamic switching:** One heavy model (GGUF) in memory; on switch, old one unloads
- **Models:** `qwen-14b-llm` (port 8091), `qwen-vl-8b` (port 8092), `labse-embedding` (port 8093)
- **LaBSE:** ONNX FP32 export (1.2-1.4x faster than SentenceTransformers on CPU)

### Chainlit Docker

`Dockerfile.chainlit` — lightweight image (no torch, llama-cpp, onnx). Copies only `backend/orchestrator/` and `ums_client.py`. `PYTHONPATH=/app` for absolute imports.

Auth: `CHAINLIT_ADMIN_USER` / `CHAINLIT_ADMIN_PASSWORD` (env). SQLite persistence: `CHAINLIT_DB_URL`.

---

## File Structure

```
agent-navigator-pro/
├── CLAUDE.md                          # Claude Code instructions
├── GEMINI.md                          # Gemini CLI instructions (this file)
├── docker-compose.yaml                # chainlit (primary) + open-webui (legacy)
├── Dockerfile.chainlit                # Lightweight Python 3.11 image
├── requirements.chainlit.txt          # Docker dependencies (no ML libs)
├── scripts/
│   ├── run_all.sh                     # Full tmux startup (12KB)
│   ├── stop_all.sh                    # Stop all services
│   ├── restart_all.sh                 # Restart
│   ├── setup_ubuntu.sh               # Full environment provisioning (19KB)
│   └── start_system_test.sh           # Integration test runner
├── backend/
│   ├── .env                           # Environment variables
│   ├── pytest.ini                     # Pytest config (asyncio_mode=auto)
│   ├── orchestrator/
│   │   ├── agent_api.py               # Legacy OpenAI-compatible API (port 8000)
│   │   ├── chainlit_app.py            # Chainlit main app (port 3000)
│   │   ├── utils.py                   # parse_json_garbage(), helpers
│   │   ├── rag/
│   │   │   ├── pipeline.py            # AdaptiveRAGPipeline
│   │   │   ├── retriever.py           # BM25 + Dense + RRF
│   │   │   ├── classifier.py          # EmbeddingIntentClassifier
│   │   │   └── chunker.py             # Section-aware legal chunker
│   │   └── workflows/
│   │       ├── compare.py             # Document comparison workflow
│   │       ├── equipment.py           # Equipment/estimate analysis (32KB)
│   │       └── document_analysis.py   # Single-document analysis (18KB)
│   ├── services/
│   │   ├── document_server/
│   │   │   └── mcp_document_server.py # PDF/DOCX/XLSX parsing
│   │   ├── legal_server/
│   │   │   └── mcp_legal_server.py    # LaBSE matching + Hungarian
│   │   ├── model_manager/
│   │   │   ├── unified_model_server.py # UMS (port 8090)
│   │   │   ├── ums_client.py          # HTTP client for UMS
│   │   │   ├── models_config.py       # Model configurations
│   │   │   └── st_server.py           # SentenceTransformers fallback
│   │   ├── hardware/
│   │   │   ├── profiler.py            # GPU/CPU detection
│   │   │   ├── tier_selector.py       # Hardware → RAG tier mapping
│   │   │   └── vram_calculator.py     # gpu_layers/ctx_size calculation
│   │   └── resource_monitor.py        # System resource monitoring
│   ├── tests/
│   │   ├── test_equipment_workflow.py  # Equipment tests (47KB, 50+ tests)
│   │   ├── test_document_analysis.py   # Document analysis tests (26KB, 30 tests)
│   │   ├── test_e2e_equipment.py       # E2E with real PDF (13KB, 4 tests)
│   │   ├── test_rag_pipeline.py        # RAG pipeline tests
│   │   ├── test_chunker.py             # Chunker tests
│   │   ├── test_hybrid_search.py       # Search tests
│   │   ├── test_intent_classifier.py   # Intent classifier tests
│   │   ├── test_onnx_embeddings.py     # ONNX LaBSE tests
│   │   ├── test_hardware_profiler.py   # Hardware profiler tests
│   │   ├── test_tier_selector.py       # Tier selector tests
│   │   ├── test_vram_calculator.py     # VRAM calculator tests
│   │   ├── test_multiturn_prompt.py    # Multi-turn prompt tests
│   │   ├── test_integration_full.py    # Full integration test
│   │   └── test_agent_hallucinations.py # Hallucination guard tests
│   └── models/
│       ├── gguf/                       # GGUF model files (not in git)
│       └── onnx/
│           └── export_labse.py         # LaBSE ONNX export script
└── .gemini/
    └── settings.json                   # Gemini CLI MCP server config
```

---

## Configuration (.env)

**Critical variables:**
- `MODEL_PATH_QWEN14B` — path to GGUF LLM file (required)
- `MODEL_PATH_LABSE` — path to LaBSE model (required for workflows and RAG)
- `MODEL_PATH_QWENVL` / `MMPROJ_PATH` — Vision model (optional)
- `N_GPU_LAYERS_OVERRIDE` — GPU layers override (`-1` = all on GPU)
- `TIER_OVERRIDE` — force specific RAG tier
- `CHAINLIT_ADMIN_USER` / `CHAINLIT_ADMIN_PASSWORD` — Chainlit auth
- `UPLOADS_DIR` — shared file exchange directory

---

## Key Development Patterns

### Adding a Workflow

1. Create `backend/orchestrator/workflows/your_workflow.py` with TypedDict state
2. Implement nodes as async functions returning dict updates of state
3. Build `StateGraph`: `add_node()`, `add_edge()`, `compile()`
4. Register in `chainlit_app.py::_detect_intent()` and add handler
5. Add `NODE_LABELS` dict for `cl.Step` visualization
6. For legacy: add route in `agent_api.py`

### LLM Prompting

Qwen chat template format:
```python
prompt = """<|im_start|>system
You are an expert assistant.<|im_end|>
<|im_start|>user
{user_query}<|im_end|>
<|im_start|>assistant
"""
```

For structured output: request JSON, parse via `orchestrator/utils.py::parse_json_garbage()`.

### Inter-service Communication

All services use `httpx.AsyncClient`:
```python
async with httpx.AsyncClient(timeout=60.0) as client:
    resp = await client.post(f"{SERVICE_URL}/endpoint", json={...})
    resp.raise_for_status()
    result = resp.json()
```

### Table Parsing Pattern (equipment.py)

Multi-page tables in PDFs require `inherited_col_map`:
```python
last_col_map = None
for table_info in data.get("tables", []):
    parsed, last_col_map = _parse_table_rows(
        table_data, page_info=page, inherited_col_map=last_col_map
    )
    items.extend(parsed)
```

### Reusable Functions from equipment.py

These functions are imported by other workflows (e.g., document_analysis.py):
- `_extract_tables_from_doc(client, path)` — extracts tables from PDF/DOCX/XLSX
- `_extract_items_llm(client, path, already_found)` — LLM text extraction with map-reduce chunking
- `_dedup_items(items)` — deduplication preferring `source='table'`
- `_chunk_text(client, text)` — text chunking via Document Server `/smart_chunk`
- `_parse_table_rows(data, page_info, inherited_col_map)` — parses table rows with header detection
- `truncate_text(text, max_chars)` — safe text truncation

---

## What Has Been Completed (v3.0)

### Committed
1. Hardware-Adaptive system — GPU profiler, TierSelector, VRAMCalculator
2. Adaptive RAG Pipeline — 4 tiers, BM25+Dense, RRF fusion, Z-score grading
3. EmbeddingIntentClassifier — centroid-based, no LLM
4. Section-aware legal document chunker
5. ONNX LaBSE export (FP32, 1.2-1.4x CPU speedup)
6. Chainlit UI with `cl.Step` workflow visualization
7. Document comparison workflow (`compare.py`)
8. Chainlit Docker pipeline
9. AdaptiveRAGPipeline integration into Chainlit

### Uncommitted (local only — MUST commit)
10. Equipment workflow refactoring — `inherited_col_map` for multi-page tables, `_safe_cell()` helper
11. Single-document analysis workflow (`document_analysis.py`) — classify, extract, summarize, report
12. Document analysis intent detection + Chainlit handler
13. Test suites: `test_equipment_workflow.py` (50+ tests), `test_document_analysis.py` (30 tests), `test_e2e_equipment.py` (4 E2E tests)
14. `pytest.ini` with `integration` marker
15. Document Server updates for XLSX/DOCX table extraction

---

## Development Roadmap (Remaining Work)

### Priority 1: Critical (must do)

#### P1.1: Commit all uncommitted work
All local changes (85KB+ of new code) are at risk. Must commit:
- `document_analysis.py`, `equipment.py` changes, `chainlit_app.py` changes
- 3 test files, `pytest.ini`
- `mcp_document_server.py` updates

#### P1.2: Fix GPU/CUDA issue
llama-server compiled with CUDA but fails at runtime: `ggml_cuda_init: failed to initialize CUDA: unknown error`. Model falls back to CPU → 30-50x slower inference. Fix: reload `nvidia_uvm` kernel module or reboot after kernel updates.

#### P1.3: Run and pass E2E tests
Current status: `test_extract_tables_from_real_pdf` PASSES, but LLM-dependent tests (`test_extract_llm_chunked_real_pdf`, `test_full_comparison_workflow`, `test_single_doc_analysis_real_pdf`) hang on CPU. After GPU fix, these should pass.

### Priority 2: High (this sprint)

#### P2.1: Document Server — `/smart_chunk` robustness
Current smart_chunk can return oversized chunks when text has no paragraph breaks. Need better fallback splitting strategy.

#### P2.2: Equipment workflow — improved table parsing
- Handle tables with merged cells in PDFs
- Handle tables where headers span two rows (common in Russian TZ documents)
- Better detection of continuation tables (inherited_col_map heuristic)

#### P2.3: Document analysis workflow — expand doc types
Current `classify_doc_type()` uses keyword scoring. Could be improved:
- Add `act` (акт) and `protocol` (протокол) document types
- Consider using LLM for ambiguous cases (hybrid: keywords first, LLM for low-confidence)

#### P2.4: Summarization quality
- `summarize_node` currently uses single LLM call per chunk → can produce inconsistent summaries for long documents
- Implement proper map-reduce: chunk summaries → final consolidation pass
- Add extraction of key entities (organizations, dates, amounts)

### Priority 3: Medium (next sprint)

#### P3.1: Multi-agent RAG (Tier 4)
Current Tier 4 falls back to Tier 3 (agentic). Implement proper LangGraph-based multi-agent RAG:
- Decompose complex queries into sub-queries
- Route sub-queries to specialized retrieval agents
- Merge results

#### P3.2: Vision model integration
`qwen-vl-8b` model config exists but is not integrated into workflows. Use cases:
- Scan/image-based PDF table extraction (when Camelot/pdfplumber fail)
- Diagram/flowchart interpretation from technical documents

#### P3.3: Dynamic port pool for UMS
`unified_model_server.py:141` has TODO: implement port pool for concurrent model instances.

#### P3.4: INT8 LaBSE quantization
`export_labse.py` has TODO: INT8 quantization with calibration dataset for further CPU inference speedup.

#### P3.5: Streaming responses in workflows
Currently workflow nodes run synchronously — user sees nothing until all nodes complete. Implement intermediate streaming:
- Stream `cl.Step` progress updates as nodes process
- Stream LLM tokens during evaluation/summarization nodes

### Priority 4: Low (backlog)

#### P4.1: Open WebUI deprecation
Open WebUI is legacy (`--profile legacy`). Eventually remove:
- `run_openwebui.sh`
- Agent API compatibility layer
- Open WebUI Docker service

#### P4.2: Multi-document analysis
Currently `document_analysis` handles only 1 file. Extend to batch mode:
- Upload 5+ documents → classify all → aggregate report
- Cross-document entity linking (same equipment across TZ + Smeta + Act)

#### P4.3: Report export formats
Currently only Markdown reports. Add:
- PDF export (via weasyprint or similar)
- XLSX export for equipment comparison tables

#### P4.4: Webhook/notification integration
Notify external systems when workflow completes:
- Telegram bot notification
- Email with report attachment

---

## Common Issues

**UMS not loading models:** Check paths in `.env`, GPU memory (`nvidia-smi`), logs in tmux window `ums`.

**Port busy:** `lsof -i :8000` → find process → restart.

**Files not found in workflows:** Check `backend/open_webui_uploads/`, Docker bind mount in `docker-compose.yaml`.

**`ModuleNotFoundError` in Chainlit Docker:** Ensure `ENV PYTHONPATH=/app` in `Dockerfile.chainlit`. Rebuild: `docker compose build chainlit`.

**tmux not starting:** Check conda (`conda env list`), paths in `run_all.sh::find_conda()`.

**CUDA unknown error:** After kernel update: `sudo rmmod nvidia_uvm && sudo modprobe nvidia_uvm`. If fails: `sudo reboot`.

**E2E tests hang:** Likely CUDA not working → LLM on CPU. Fix CUDA first. Real PDF: `/home/seral/HDD/proj/dev_1_conda/documents/f5jsglrkyfe8p3ogd02g405d9q3r9lfn.pdf`.

**Table parsing returns few items:** Check if PDF has multi-page table (common). Ensure `inherited_col_map` is being propagated in `_extract_tables_from_doc()`.

---

## Architectural Principles

- Orchestrator (chainlit_app / agent_api) manages routing and workflows — don't mix with service logic
- MCP servers (document, legal) provide specialized tools — stateless HTTP APIs
- UMS abstracts model backends (llama-server, st_server) — single API for all models
- RAG pipeline does NOT use LLM for classification — embeddings only (EmbeddingIntentClassifier)
- Workflows are independent and composable — each is a self-contained LangGraph
- Reuse extraction functions from `equipment.py` across workflows — don't duplicate
- Errors accumulate in `state["errors"]` — nodes must not crash, must return partial results
- Tests use mocking for LLM/service calls — unit tests must work without running services
- E2E tests marked `@pytest.mark.integration` — require running microservices + real PDF

---

## Testing Conventions

```python
# Unit test pattern (mocked services)
@pytest.mark.asyncio
async def test_something(self, base_state):
    with patch("orchestrator.workflows.equipment.ums_client") as mock_ums:
        mock_ums.async_infer = AsyncMock(return_value={"content": "[...]"})
        result = await some_node(base_state)
    assert result["items"] == expected

# E2E test pattern (real services)
@pytest.mark.integration
@pytest.mark.skipif(not PDF_EXISTS, reason="Real PDF not found")
class TestE2E:
    @pytest.mark.asyncio
    async def test_full_workflow(self):
        workflow = create_equipment_graph()
        async for event in workflow.astream(initial_state):
            ...
```

Run only unit tests: `pytest tests/ -v -m "not integration"`
Run only E2E: `pytest tests/ -v -m integration`
