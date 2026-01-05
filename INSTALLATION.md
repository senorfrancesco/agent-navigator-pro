# Installation Guide

This guide provides step-by-step instructions for setting up the Agent Navigator Pro project.

## Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.11+**: Required for the backend services
- **Node.js 18+**: Required for the frontend
- **NVIDIA GPU with CUDA**: Recommended for optimal performance (RTX 3060 12GB or higher)
- **Git**: For cloning the repository

## Step 1: Clone the Repository

```bash
git clone https://github.com/senorfrancesco/agent-navigator-pro.git
cd agent-navigator-pro
```

## Step 2: Backend Setup

### Create a Virtual Environment

```bash
cd react_agent_prototype
python3.11 -m venv venv
```

### Activate the Virtual Environment

**On Linux/macOS:**
```bash
source venv/bin/activate
```

**On Windows:**
```bash
venv\Scripts\activate
```

### Install Python Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Optional: GPU Support

For NVIDIA GPU monitoring and CUDA support:

```bash
pip install pynvml
```

### Download Models

Place your GGUF model files in the `react_agent_prototype/models/` directory. You can download models from:

- [Hugging Face](https://huggingface.co/)
- [TheBloke's GGUF models](https://huggingface.co/TheBloke)

Example models:
- `Qwen2.5-14B-Instruct-Q4_K_M.gguf`
- `Qwen3-VL-8B-Instruct-Q4_K_M.gguf`
- `LaBSE-Q4_K_M.gguf`

### Set Environment Variables

Create a `.env` file in the `react_agent_prototype` directory:

```bash
MODEL_PATH_QWEN14B="./models/Qwen2.5-14B-Instruct-Q4_K_M.gguf"
MODEL_PATH_QWENVL="./models/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
MODEL_PATH_LABSE="./models/LaBSE-Q4_K_M.gguf"
MMPROJ_PATH="./models/mmproj-Qwen3-VL-8B-Instruct-F16.gguf"
```

## Step 3: Frontend Setup

Navigate to the project root and install Node.js dependencies:

```bash
cd ..  # Return to project root
npm install
```

## Step 4: Running the Application

### Option 1: Manual Startup

Start each service in a separate terminal window.

**Terminal 1: Agent API**
```bash
cd react_agent_prototype/orchestrator
uvicorn agent_api:app --host 0.0.0.0 --port 8000
```

**Terminal 2: Document Server**
```bash
cd react_agent_prototype/services/document_server
uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001
```

**Terminal 3: Legal Server**
```bash
cd react_agent_prototype/services/legal_server
uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002
```

**Terminal 4: Unified Model Server (Optional)**
```bash
cd react_agent_prototype/services/model_manager
python unified_model_server.py
```

**Terminal 5: Frontend**
```bash
npm run dev
```

### Option 2: Automated Startup (Linux/macOS)

Use the provided shell script to start all backend services:

```bash
cd react_agent_prototype
chmod +x run_all.sh
./run_all.sh
```

Then start the frontend in a separate terminal:

```bash
npm run dev
```

## Step 5: Verify Installation

Open your browser and navigate to:

- **Frontend**: `http://localhost:5173` (or the port shown by Vite)
- **Agent API**: `http://localhost:8000/health`

You should see the health status of all connected services.

## Troubleshooting

### Python Version Issues

Ensure you are using Python 3.11 or higher:

```bash
python --version
```

### Missing Dependencies

If you encounter import errors, reinstall dependencies:

```bash
pip install -r requirements.txt --force-reinstall
```

### Port Already in Use

If a port is already in use, you can change it in the startup commands or kill the process using that port:

```bash
# Find the process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>
```

### CUDA Not Detected

Ensure you have the NVIDIA drivers and CUDA toolkit installed. Check CUDA availability:

```bash
nvidia-smi
```

## Next Steps

Once the installation is complete, refer to the main [README.md](README.md) for usage instructions and API documentation.
