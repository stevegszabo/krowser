from fastapi import HTTPException
from kubernetes.client.rest import ApiException

from krowser.k8s.client import KubeConfigError
from krowser.k8s.fetchers import ResourceAccessError
from krowser.scan_errors import ScanFailedError, ScannerUnavailableError


def to_http_exception(exc: Exception) -> HTTPException:
    """Maps internal/Kubernetes errors to an HTTP response instead of a raw 500,
    so e.g. an RBAC-forbidden list call surfaces as 403 with a useful message."""
    if isinstance(exc, ResourceAccessError):
        return HTTPException(status_code=exc.status, detail=f"{exc.kind}: {exc.reason}")
    if isinstance(exc, KubeConfigError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ApiException):
        return HTTPException(status_code=exc.status, detail=exc.reason or "Kubernetes API error")
    if isinstance(exc, ScannerUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ScanFailedError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))
