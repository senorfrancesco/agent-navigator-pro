from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class EchoToolRequest(BaseModel):
    message: str
    current_model_id: Optional[str] = None


class EchoToolResponse(BaseModel):
    status: Literal["completed"] = "completed"
    tool_name: Literal["echo"] = "echo"
    assistant_message: str
    model_id: str


class AnalyzeEquipmentFastRequest(BaseModel):
    equipment_query: str
    current_model_id: Optional[str] = None
    ui_locale: Optional[str] = None


class AnalyzeEquipmentFastResponse(BaseModel):
    status: Literal["completed"] = "completed"
    tool_name: Literal["analyze_equipment_fast"] = "analyze_equipment_fast"
    assistant_message: str
    model_id: str


class AnalyzeEquipmentDeepRequest(BaseModel):
    equipment_query: str
    current_model_id: Optional[str] = None
    ui_locale: Optional[str] = None


class AcceptedToolResult(BaseModel):
    status: Literal["accepted"] = "accepted"
    tool_name: str
    job_id: str
    status_url: str
    result_ref: str


class ToolJobStatus(BaseModel):
    job_id: str
    status: str
    tool_name: str
    status_url: str
    result_ref: str
    status_text: Optional[str] = None
    result_preview: Optional[str] = None


class ToolJobResult(BaseModel):
    assistant_message: str
    payload: Dict[str, Any] = Field(default_factory=dict)
