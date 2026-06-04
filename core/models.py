from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class MREventPayload(BaseModel):
    object_kind: str
    object_attributes: dict
    project: dict
    user: Optional[dict] = None


class AlertPayload(BaseModel):
    timestamp: str
    error_type: str
    severity: str
    service: str
    stack_trace: str


class RollbackApproval(BaseModel):
    token: str
    mr_iid: int


class PendingRollback(BaseModel):
    token: str
    project_id: int
    mr_iid: int
    commit_sha: str
    created_at: datetime
