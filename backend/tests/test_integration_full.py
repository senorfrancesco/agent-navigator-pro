"""
Corrected integration test script for llm-tools-platform.
Starts all microservices and performs health checks.
"""

import subprocess
import time
import requests
import sys
import os
import signal
from typing import Optional, List, Tuple

# ANSI Colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

class IntegrationTester:
    def __init__(self):
        self.processes: List[Tuple[str, subprocess.Popen]] = []
        self.project_dir = os.path.dirname(os.path.abspath(__file__))
    
    def log(self, message: str, level: str = "INFO"):
        colors = {
            "INFO": BLUE,
            "SUCCESS": GREEN,
            "WARNING": YELLOW,
            "ERROR": RED
        }
        color = colors.get(level, BLUE)
        print(f"{color}[{level}]{RESET} {message}")
    
    def run_process(self, name: str, cmd: List[str], cwd_rel: str) -> bool:
        """Starts a process in a specific directory."""
        cwd = os.path.join(self.project_dir, cwd_rel)
        self.log(f"Starting {name} in {cwd}...", "INFO")
        
        try:
            # Using same python environment
            cmd[0] = sys.executable if cmd[0] == "python" else cmd[0]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, # Silence output to keep test clean
                stderr=subprocess.PIPE,
                cwd=cwd,
                preexec_fn=os.setsid # Create new process group
            )
            self.processes.append((name, process))
            return True
        except Exception as e:
            self.log(f"Failed to start {name}: {e}", "ERROR")
            return False

    def check_url(self, name: str, url: str, timeout: int = 15) -> bool:
        """Polls a URL until it returns 200 or timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = requests.get(url, timeout=1)
                if response.status_code == 200:
                    self.log(f"{name} is ONLINE ({url})", "SUCCESS")
                    return True
            except requests.RequestException:
                pass
            time.sleep(1)
        
        self.log(f"{name} failed to respond within {timeout}s", "ERROR")
        return False

    def cleanup(self):
        """Terminates all started processes."""
        self.log("Stopping all services...", "INFO")
        for name, process in self.processes:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except Exception:
                pass
        
        # Wait a bit
        time.sleep(2)
        
        for name, process in self.processes:
            if process.poll() is None:
                 self.log(f"Force killing {name}...", "WARNING")
                 try:
                     os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                 except Exception:
                     pass

    def run(self):
        self.log("=== Starting Integration Test ===", "INFO")
        
        success = True
        
        # 1. Start UMS (Unified Model Server)
        # Assuming UMS is needed by others, but checking health separately is fine.
        # python unified_model_server.py
        if not self.run_process("UMS", ["python", "unified_model_server.py"], "services/model_manager"):
            success = False

        # 2. Start Document Server
        # uvicorn mcp_document_server:app --host 0.0.0.0 --port 8001
        if not self.run_process("DocServer", 
                                [sys.executable, "-m", "uvicorn", "mcp_document_server:app", "--host", "0.0.0.0", "--port", "8001"], 
                                "services/document_server"):
            success = False

        # 3. Start Legal Server
        # uvicorn mcp_legal_server:app --host 0.0.0.0 --port 8002
        if not self.run_process("LegalServer", 
                                [sys.executable, "-m", "uvicorn", "mcp_legal_server:app", "--host", "0.0.0.0", "--port", "8002"], 
                                "services/legal_server"):
            success = False

        # 4. Start Agent API
        # python agent_api.py
        if not self.run_process("AgentAPI", ["python", "agent_api.py"], "orchestrator"):
            success = False

        self.log("Waiting for services to initialize...", "INFO")
        time.sleep(5) # Give them a head start

        # Check Health
        # UMS Health
        if not self.check_url("UMS", "http://localhost:8090/health"):
            success = False
            
        # DocServer Health
        if not self.check_url("DocServer", "http://localhost:8001/health"):
            success = False
            
        # LegalServer Health
        if not self.check_url("LegalServer", "http://localhost:8002/health"):
            success = False
            
        # AgentAPI Status
        if not self.check_url("AgentAPI", "http://localhost:8000/status"):
            success = False

        if success:
            self.log("=== ALL SYSTEMS OPERATIONAL ===", "SUCCESS")
        else:
            self.log("=== SYSTEM TEST FAILED ===", "ERROR")
            
        self.cleanup()
        return success

if __name__ == "__main__":
    tester = IntegrationTester()
    if not tester.run():
        sys.exit(1)
