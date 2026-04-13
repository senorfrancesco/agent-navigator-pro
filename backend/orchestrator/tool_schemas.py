from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from orchestrator.tool_catalog import RoutingMode, ToolName


class DocumentRef(BaseModel):
    document_id: Optional[str] = Field(default=None, description="Canonical persistent document identifier.")
    upload_id: Optional[str] = Field(default=None, description="Canonical upload identifier.")
    file_id: Optional[str] = Field(default=None, description="Canonical backend file identifier.")
    version_id: Optional[str] = Field(default=None, description="Canonical document version identifier.")
    session_file_ref: Optional[str] = Field(
        default=None,
        description="Eval-only session-scoped file reference. Replace with persistent ids after M2.1.",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Eval-only local file path for dev contour. Replace with persistent ids after M2.1.",
    )
    label: Optional[str] = None

    @model_validator(mode="after")
    def validate_identity(self) -> "DocumentRef":
        if self.resolved_identity():
            return self
        raise ValueError(
            "document ref requires one of document_id, upload_id, file_id, version_id, session_file_ref, file_path"
        )

    def canonical_identity(self) -> Optional[str]:
        for field_name in ("document_id", "upload_id", "file_id", "version_id"):
            value = getattr(self, field_name)
            if value:
                return str(value)
        return None

    def compatibility_identity(self) -> Optional[str]:
        for field_name in ("session_file_ref", "file_path"):
            value = getattr(self, field_name)
            if value:
                return str(value)
        return None

    def resolved_identity(self) -> Optional[str]:
        return self.canonical_identity() or self.compatibility_identity()

    def resolved_identity_kind(self) -> Optional[str]:
        for field_name in (
            "document_id",
            "upload_id",
            "file_id",
            "version_id",
            "session_file_ref",
            "file_path",
        ):
            value = getattr(self, field_name)
            if value:
                return field_name
        return None

    def has_canonical_identity(self) -> bool:
        return self.canonical_identity() is not None


class ToolSource(BaseModel):
    source_id: str
    source_type: Literal["document", "artifact", "knowledge_base", "external"]
    title: Optional[str] = None
    citation_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolArtifact(BaseModel):
    artifact_id: str
    artifact_type: Literal["report", "table", "chart", "download", "embed"]
    url: Optional[str] = None
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolAction(BaseModel):
    action_id: str
    label: str
    action_type: Literal["rerun_tool", "open_url", "download", "view_artifact", "cancel_job"]
    payload: Dict[str, Any] = Field(default_factory=dict)


class ExecutionMetadata(BaseModel):
    requested_tool: Optional[ToolName] = None
    routing_mode: RoutingMode = "explicit"
    execution_mode: Literal["sync", "async"] = "sync"
    trace_id: Optional[str] = None
    runtime_mode: Optional[str] = None
    model_profile: Optional[str] = None
    latency_ms: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)


class BaseToolRequest(BaseModel):
    tool_name: ToolName
    requested_tool: Optional[ToolName] = None
    routing_mode: RoutingMode = Field(
        default="explicit",
        description="Explicit by default for /tools/* endpoints; clients do not need to provide it.",
    )
    thread_id: Optional[str] = None
    session_id: Optional[str] = None
    job_mode: Literal["sync_if_possible", "force_async"] = "sync_if_possible"
    document_refs: List[DocumentRef] = Field(default_factory=list)
    user_inputs: Dict[str, Any] = Field(default_factory=dict)
    ui_hints: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_requested_tool(self) -> "BaseToolRequest":
        if self.requested_tool is None:
            self.requested_tool = self.tool_name
            return self
        if self.requested_tool != self.tool_name:
            raise ValueError("requested_tool must match tool_name for explicit tool requests")
        return self


class AskDocumentRequest(BaseToolRequest):
    tool_name: Literal["ask_document"] = "ask_document"
    question: str
    include_citations: bool = True

    @model_validator(mode="after")
    def require_document_ref(self) -> "AskDocumentRequest":
        if not self.document_refs:
            raise ValueError("ask_document requires at least one document_ref")
        return self


class AnalyzeDocumentFastRequest(BaseToolRequest):
    tool_name: Literal["analyze_document_fast"] = "analyze_document_fast"
    analysis_goal: Optional[str] = None

    @model_validator(mode="after")
    def require_document_ref(self) -> "AnalyzeDocumentFastRequest":
        if not self.document_refs:
            raise ValueError("analyze_document_fast requires at least one document_ref")
        return self


class AnalyzeDocumentDeepRequest(BaseToolRequest):
    tool_name: Literal["analyze_document_deep"] = "analyze_document_deep"
    analysis_goal: Optional[str] = None
    include_report: bool = True

    @model_validator(mode="after")
    def require_document_ref(self) -> "AnalyzeDocumentDeepRequest":
        if not self.document_refs:
            raise ValueError("analyze_document_deep requires at least one document_ref")
        return self


class CompareDocumentsFastRequest(BaseToolRequest):
    tool_name: Literal["compare_documents_fast"] = "compare_documents_fast"
    comparison_goal: Optional[str] = None

    @model_validator(mode="after")
    def require_two_document_refs(self) -> "CompareDocumentsFastRequest":
        if len(self.document_refs) < 2:
            raise ValueError("compare_documents_fast requires at least two document refs")
        return self


class CompareDocumentsDeepRequest(BaseToolRequest):
    tool_name: Literal["compare_documents_deep"] = "compare_documents_deep"
    comparison_goal: Optional[str] = None
    include_report: bool = True

    @model_validator(mode="after")
    def require_two_document_refs(self) -> "CompareDocumentsDeepRequest":
        if len(self.document_refs) < 2:
            raise ValueError("compare_documents_deep requires at least two document refs")
        return self


class AnalyzeEquipmentFastRequest(BaseToolRequest):
    tool_name: Literal["analyze_equipment_fast"] = "analyze_equipment_fast"
    equipment_query: str


class AnalyzeEquipmentDeepRequest(BaseToolRequest):
    tool_name: Literal["analyze_equipment_deep"] = "analyze_equipment_deep"
    equipment_query: str
    include_report: bool = True


ToolRequest = Union[
    AskDocumentRequest,
    AnalyzeDocumentFastRequest,
    AnalyzeDocumentDeepRequest,
    CompareDocumentsFastRequest,
    CompareDocumentsDeepRequest,
    AnalyzeEquipmentFastRequest,
    AnalyzeEquipmentDeepRequest,
]


class CompletedToolResult(BaseModel):
    status: Literal["completed"] = "completed"
    tool_name: ToolName
    assistant_message: str
    structured_result: Dict[str, Any] = Field(default_factory=dict)
    sources: List[ToolSource] = Field(default_factory=list)
    artifacts: List[ToolArtifact] = Field(default_factory=list)
    available_actions: List[ToolAction] = Field(default_factory=list)
    execution_metadata: ExecutionMetadata


class AcceptedToolResult(BaseModel):
    status: Literal["accepted"] = "accepted"
    tool_name: ToolName
    job_id: str
    status_url: str
    submitted_at: str
    result_preview: Optional[str] = None
    available_actions: List[ToolAction] = Field(default_factory=list)
    execution_metadata: ExecutionMetadata


class ToolJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]
    current_stage: Optional[str] = None
    submitted_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result_ref: Optional[str] = None
    error_summary: Optional[str] = None
