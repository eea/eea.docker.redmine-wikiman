from pydantic import BaseModel
from typing import Dict, Any, Optional


class AdmissionReviewRequest(BaseModel):
    apiVersion: str
    kind: str
    request: Dict[str, Any]


class AdmissionReviewResponse(BaseModel):
    apiVersion: str
    kind: str
    response: Dict[str, Any]


class ChangeInfo(BaseModel):
    timestamp: str
    operation: str
    user: str
    resource_kind: str
    resource_name: str
    namespace: str
    release: str
    message: Optional[str] = None


class HelmDeploymentInfo(BaseModel):
    chart_name: str
    chart_version: str
    app_version: str
    release_version: str
    repository: str
    repository_url: str
    deploy_command: str
    helm_release_version: Optional[str] = None
    values: Dict[str, Any] = {}


class ResourceMetadata(BaseModel):
    name: str
    namespace: str
    kind: str
    release: Optional[str] = None
