from __future__ import annotations

import os
import sys
from pathlib import Path


if os.environ.get("LLM_TOOLS_PLATFORM_OPENWEBUI_DEEP_JOB_GUARD", "0") == "1":
    backend_path = Path("/app/backend")
    if backend_path.exists():
        backend_str = str(backend_path)
        if backend_str not in sys.path:
            sys.path.insert(0, backend_str)

        from openwebui_deep_job_guard import apply_patches

        apply_patches()
