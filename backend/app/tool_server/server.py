from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response

from app.clients.openai_compatible import ConfigurationError
from app.tool_server.handlers import run_analyze_equipment_fast, run_echo_tool
from app.tool_server.jobs import (
    cancel_job,
    create_analyze_equipment_deep_job,
    get_job_result,
    get_job_status,
)
from app.tool_server.openapi import build_tool_server_openapi
from app.tool_server.registry import get_tool_definition
from app.tool_server.schemas import (
    AcceptedToolResult,
    AnalyzeEquipmentDeepRequest,
    AnalyzeEquipmentFastRequest,
    AnalyzeEquipmentFastResponse,
    EchoToolRequest,
    EchoToolResponse,
    ToolJobResult,
    ToolJobStatus,
)


def create_tool_server_app(*, route_prefix: str | None = None) -> FastAPI:
    echo_definition = get_tool_definition("echo")
    equipment_fast_definition = get_tool_definition("analyze_equipment_fast")
    equipment_deep_definition = get_tool_definition("analyze_equipment_deep")
    app = FastAPI(
        title="Agent Navigator Tool Server",
        version="0.1.0",
    )

    @app.get("/tool-server/openapi.json", include_in_schema=False)
    async def tool_server_openapi() -> dict:
        return build_tool_server_openapi(app)

    @app.post(
        "/tools/echo",
        response_model=EchoToolResponse,
        summary=echo_definition.summary,
        description=echo_definition.description,
    )
    async def echo_tool(request: EchoToolRequest) -> EchoToolResponse:
        return await run_echo_tool(request)

    @app.post(
        "/tools/analyze_equipment_fast",
        response_model=AnalyzeEquipmentFastResponse,
        summary=equipment_fast_definition.summary,
        description=equipment_fast_definition.description,
    )
    async def analyze_equipment_fast_tool(
        request: AnalyzeEquipmentFastRequest,
    ) -> AnalyzeEquipmentFastResponse:
        try:
            return await run_analyze_equipment_fast(request)
        except ConfigurationError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "tool_configuration_error",
                    "message": str(exc),
                },
            ) from exc

    @app.post(
        "/tools/analyze_equipment_deep",
        response_model=AcceptedToolResult,
        status_code=202,
        summary=equipment_deep_definition.summary,
        description=equipment_deep_definition.description,
    )
    async def analyze_equipment_deep_tool(
        request: AnalyzeEquipmentDeepRequest,
    ) -> AcceptedToolResult:
        return create_analyze_equipment_deep_job(
            equipment_query=request.equipment_query,
            current_model_id=request.current_model_id,
            ui_locale=request.ui_locale,
            route_prefix=route_prefix,
        )

    @app.get("/tool-jobs/{job_id}", response_model=ToolJobStatus)
    async def tool_job_status(job_id: str) -> ToolJobStatus:
        try:
            return get_job_status(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "tool_job_not_found"}) from exc

    @app.get("/tool-jobs/{job_id}/result", response_model=ToolJobResult)
    async def tool_job_result(job_id: str) -> ToolJobResult:
        try:
            return get_job_result(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "tool_job_not_found"}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "tool_job_not_ready"}) from exc

    @app.post("/tool-jobs/{job_id}/cancel", response_model=ToolJobStatus)
    async def tool_job_cancel(job_id: str, response: Response) -> ToolJobStatus:
        try:
            status = cancel_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "tool_job_not_found"}) from exc
        if status.status == "cancelled":
            response.status_code = 200
        return status

    return app
