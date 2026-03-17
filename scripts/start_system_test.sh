#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"

# 1. Start Docker for Open WebUI
echo "Starting Open WebUI via Docker..."
cd "$PROJECT_ROOT" && docker compose up -d

# 2. Run backend services in tmux (using existing script logic but detached)
echo "Starting Backend Services in tmux..."

SESSION_NAME="agent-navigator"
CONDA_ENV="base" # Hardcoded for test safety, or derive from .env

echo "Stopping existing tmux/runtime/docker processes..."
"$SCRIPT_DIR/stop_all.sh" >/dev/null 2>&1 || true

# Create session	mux new-session -d -s "$SESSION_NAME" -x 200 -y 50

# Commands
ACTIVATE_CMD="eval \"$(conda shell.bash hook)\" && conda activate $CONDA_ENV"

# Window 1: Agent API	mux new-window -t "$SESSION_NAME" -n "agent-api"	mux send-keys -t "$SESSION_NAME:agent-api" "cd $BACKEND_DIR/orchestrator && $ACTIVATE_CMD && python agent_api.py" Enter

# Window 2: Document Server	mux new-window -t "$SESSION_NAME" -n "doc-server"	mux send-keys -t "$SESSION_NAME:doc-server" "cd $BACKEND_DIR/services/document_server && $ACTIVATE_CMD && export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001" Enter

# Window 3: Legal Server	mux new-window -t "$SESSION_NAME" -n "legal-server"	mux send-keys -t "$SESSION_NAME:legal-server" "cd $BACKEND_DIR/services/legal_server && $ACTIVATE_CMD && export PYTHONPATH='$BACKEND_DIR' && uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002" Enter

# Window 4: UMS	mux new-window -t "$SESSION_NAME" -n "ums"	mux send-keys -t "$SESSION_NAME:ums" "cd $BACKEND_DIR && $ACTIVATE_CMD && export PYTHONPATH='$BACKEND_DIR' && python services/model_manager/unified_model_server.py" Enter

echo "Tmux session '$SESSION_NAME' started in background."
echo "Waiting 10 seconds for services to initialize..."
sleep 10

# 3. Validation
echo "=== System Status ==="
echo "Docker Containers:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep open-webui

echo -e "\nBackend Services Check:"
curl -s -o /dev/null -w "Agent API: %{http_code}\n" http://localhost:8000/status || echo "Agent API: FAILED"
curl -s -o /dev/null -w "Doc Server: %{http_code}\n" http://localhost:8001/health || echo "Doc Server: FAILED"
curl -s -o /dev/null -w "Legal Server: %{http_code}\n" http://localhost:8002/health || echo "Legal Server: FAILED"
curl -s -o /dev/null -w "UMS: %{http_code}\n" http://localhost:8090/health || echo "UMS: FAILED"

echo "=== Test Complete ==="
echo "To attach to backend logs: tmux attach -t $SESSION_NAME"
