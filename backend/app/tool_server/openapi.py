from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute


def build_tool_server_openapi(app: FastAPI) -> dict:
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and (
            str(route.path).startswith("/tools/")
            or str(route.path).startswith("/tool-jobs/")
        )
    ]
    return get_openapi(
        title=app.title,
        version=app.version,
        routes=routes,
    )
