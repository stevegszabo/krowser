import threading
from dataclasses import dataclass

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client import ApiClient
from kubernetes.config.config_exception import ConfigException


@dataclass(frozen=True)
class ContextInfo:
    name: str
    cluster: str
    is_current: bool


class KubeConfigError(RuntimeError):
    """Raised when the kubeconfig can't be loaded or a requested context doesn't exist."""


class KubeClientManager:
    """Loads kubeconfig contexts on demand and caches one ApiClient per context name.

    Context is always passed explicitly per call rather than mutating shared state,
    since a single server process may be serving multiple browser tabs/users at once.
    """

    def __init__(self, kubeconfig_path: str | None = None):
        self._kubeconfig_path = kubeconfig_path
        self._lock = threading.Lock()
        self._api_clients: dict[str, ApiClient] = {}

    def list_contexts(self) -> list[ContextInfo]:
        try:
            contexts, active = k8s_config.list_kube_config_contexts(
                config_file=self._kubeconfig_path
            )
        except ConfigException as exc:
            raise KubeConfigError(str(exc)) from exc

        active_name = active["name"] if active else None
        return [
            ContextInfo(
                name=ctx["name"],
                cluster=ctx["context"].get("cluster", ""),
                is_current=ctx["name"] == active_name,
            )
            for ctx in contexts
        ]

    def current_context_name(self) -> str:
        _, active = k8s_config.list_kube_config_contexts(config_file=self._kubeconfig_path)
        if not active:
            raise KubeConfigError("kubeconfig has no current-context set")
        return active["name"]

    def api_client_for(self, context: str | None) -> ApiClient:
        context_name = context or self.current_context_name()
        with self._lock:
            cached = self._api_clients.get(context_name)
            if cached is not None:
                return cached
            try:
                api_client = k8s_config.new_client_from_config(
                    config_file=self._kubeconfig_path, context=context_name
                )
            except ConfigException as exc:
                raise KubeConfigError(
                    f"unknown kubeconfig context: {context_name!r}"
                ) from exc
            self._api_clients[context_name] = api_client
            return api_client

    def core_v1(self, context: str | None) -> k8s_client.CoreV1Api:
        return k8s_client.CoreV1Api(self.api_client_for(context))

    def apps_v1(self, context: str | None) -> k8s_client.AppsV1Api:
        return k8s_client.AppsV1Api(self.api_client_for(context))

    def batch_v1(self, context: str | None) -> k8s_client.BatchV1Api:
        return k8s_client.BatchV1Api(self.api_client_for(context))

    def networking_v1(self, context: str | None) -> k8s_client.NetworkingV1Api:
        return k8s_client.NetworkingV1Api(self.api_client_for(context))

    def discovery_v1(self, context: str | None) -> k8s_client.DiscoveryV1Api:
        return k8s_client.DiscoveryV1Api(self.api_client_for(context))

    def list_namespaces(self, context: str | None) -> list[str]:
        namespaces = self.core_v1(context).list_namespace()
        return sorted(ns.metadata.name for ns in namespaces.items)
