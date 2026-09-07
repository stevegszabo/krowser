from functools import lru_cache

from krowser.config import settings
from krowser.k8s.client import KubeClientManager


@lru_cache
def _manager() -> KubeClientManager:
    return KubeClientManager(kubeconfig_path=settings.kubeconfig_path)


def get_kube_client_manager() -> KubeClientManager:
    return _manager()
