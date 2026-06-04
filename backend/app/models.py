from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ManualIngestRequest(BaseModel):
    title: str = Field(default="手动导入")
    body: str
    source: str = Field(default="manual")
    conversation: str | None = None
    author: str | None = None


class TaskPatch(BaseModel):
    status: str | None = None
    priority_score: int | None = None
    next_action: str | None = None
    due_at: str | None = None


class LearningPackRequest(BaseModel):
    knowledge_item_ids: list[int]
    title: str | None = None
    use_notebooklm: bool = False


class ApiResponse(BaseModel):
    ok: bool = True
    data: Any | None = None
    message: str = ""

