import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    kubeconfig_path: str | None = os.environ.get("KUBECONFIG")
    max_graph_nodes: int = int(os.environ.get("KROWSER_MAX_GRAPH_NODES", "150"))
    default_poll_ms: int = int(os.environ.get("KROWSER_POLL_MS", "10000"))


settings = Settings()
