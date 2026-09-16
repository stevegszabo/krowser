import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    kubeconfig_path: str | None = os.environ.get("KUBECONFIG")
    max_graph_nodes: int = int(os.environ.get("KROWSER_MAX_GRAPH_NODES", "150"))
    default_poll_ms: int = int(os.environ.get("KROWSER_POLL_MS", "10000"))
    vulnscan_trivy_path: str = os.environ.get("KROWSER_VULNSCAN_TRIVY_PATH", "trivy")
    vulnscan_timeout_seconds: int = int(os.environ.get("KROWSER_VULNSCAN_TIMEOUT_SECONDS", "180"))
    vulnscan_cache_ttl_seconds: int = int(os.environ.get("KROWSER_VULNSCAN_CACHE_TTL_SECONDS", "3600"))
    kubescape_path: str = os.environ.get("KROWSER_KUBESCAPE_PATH", "kubescape")
    kubescape_timeout_seconds: int = int(os.environ.get("KROWSER_KUBESCAPE_TIMEOUT_SECONDS", "180"))
    kubescape_cache_ttl_seconds: int = int(os.environ.get("KROWSER_KUBESCAPE_CACHE_TTL_SECONDS", "3600"))


settings = Settings()
