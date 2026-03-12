"""Shared pytest bootstrap for backend test imports.

Keeps single-file and full-suite runs consistent by ensuring the backend
package root is on ``sys.path`` regardless of invocation order.
"""

from __future__ import annotations

import os
import sys


BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)
