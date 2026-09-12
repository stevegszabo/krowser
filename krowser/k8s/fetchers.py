import json
from typing import Any, Callable

from kubernetes.client.rest import ApiException

from krowser.k8s.client import KubeClientManager


class ResourceAccessError(RuntimeError):
    """Wraps a Kubernetes API error for one resource kind (e.g. RBAC 403)."""

    def __init__(self, kind: str, status: int, reason: str):
        self.kind = kind
        self.status = status
        self.reason = reason
        super().__init__(f"{kind}: {status} {reason}")


def _call(kind: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> list[Any]:
    try:
        return fn(*args, **kwargs).items
    except ApiException as exc:
        raise ResourceAccessError(kind, exc.status, exc.reason or "") from exc


def list_pods(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.core_v1(context)
    if namespace:
        return _call("Pod", api.list_namespaced_pod, namespace)
    return _call("Pod", api.list_pod_for_all_namespaces)


def list_services(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.core_v1(context)
    if namespace:
        return _call("Service", api.list_namespaced_service, namespace)
    return _call("Service", api.list_service_for_all_namespaces)


def list_config_maps(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.core_v1(context)
    if namespace:
        return _call("ConfigMap", api.list_namespaced_config_map, namespace)
    return _call("ConfigMap", api.list_config_map_for_all_namespaces)


def list_secrets(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.core_v1(context)
    if namespace:
        return _call("Secret", api.list_namespaced_secret, namespace)
    return _call("Secret", api.list_secret_for_all_namespaces)


def list_service_accounts(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.core_v1(context)
    if namespace:
        return _call("ServiceAccount", api.list_namespaced_service_account, namespace)
    return _call("ServiceAccount", api.list_service_account_for_all_namespaces)


def list_persistent_volume_claims(
    mgr: KubeClientManager, context: str | None, namespace: str | None
) -> list[Any]:
    api = mgr.core_v1(context)
    if namespace:
        return _call(
            "PersistentVolumeClaim", api.list_namespaced_persistent_volume_claim, namespace
        )
    return _call(
        "PersistentVolumeClaim", api.list_persistent_volume_claim_for_all_namespaces
    )


def list_persistent_volumes(mgr: KubeClientManager, context: str | None) -> list[Any]:
    # Cluster-scoped: no namespace variant exists.
    api = mgr.core_v1(context)
    return _call("PersistentVolume", api.list_persistent_volume)


def list_nodes(mgr: KubeClientManager, context: str | None) -> list[Any]:
    # Cluster-scoped: no namespace variant exists.
    api = mgr.core_v1(context)
    return _call("Node", api.list_node)


def list_cluster_roles(mgr: KubeClientManager, context: str | None) -> list[Any]:
    # Cluster-scoped: no namespace variant exists.
    api = mgr.rbac_authorization_v1(context)
    return _call("ClusterRole", api.list_cluster_role)


def list_cluster_role_bindings(mgr: KubeClientManager, context: str | None) -> list[Any]:
    # Cluster-scoped: no namespace variant exists.
    api = mgr.rbac_authorization_v1(context)
    return _call("ClusterRoleBinding", api.list_cluster_role_binding)


def list_crds(mgr: KubeClientManager, context: str | None) -> list[Any]:
    # Cluster-scoped: no namespace variant exists. Used directly by
    # krowser.k8s.resource_types to discover CRDs for the dynamic "Custom
    # Resources" type list -- CRD *objects* themselves are never rendered as
    # graph nodes, so this has no FETCHERS_BY_KIND entry.
    api = mgr.apiextensions_v1(context)
    return _call("CustomResourceDefinition", api.list_custom_resource_definition)


def list_deployments(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.apps_v1(context)
    if namespace:
        return _call("Deployment", api.list_namespaced_deployment, namespace)
    return _call("Deployment", api.list_deployment_for_all_namespaces)


def list_stateful_sets(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.apps_v1(context)
    if namespace:
        return _call("StatefulSet", api.list_namespaced_stateful_set, namespace)
    return _call("StatefulSet", api.list_stateful_set_for_all_namespaces)


def list_daemon_sets(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.apps_v1(context)
    if namespace:
        return _call("DaemonSet", api.list_namespaced_daemon_set, namespace)
    return _call("DaemonSet", api.list_daemon_set_for_all_namespaces)


def list_replica_sets(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.apps_v1(context)
    if namespace:
        return _call("ReplicaSet", api.list_namespaced_replica_set, namespace)
    return _call("ReplicaSet", api.list_replica_set_for_all_namespaces)


def list_jobs(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.batch_v1(context)
    if namespace:
        return _call("Job", api.list_namespaced_job, namespace)
    return _call("Job", api.list_job_for_all_namespaces)


def list_cron_jobs(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.batch_v1(context)
    if namespace:
        return _call("CronJob", api.list_namespaced_cron_job, namespace)
    return _call("CronJob", api.list_cron_job_for_all_namespaces)


def list_ingresses(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.networking_v1(context)
    if namespace:
        return _call("Ingress", api.list_namespaced_ingress, namespace)
    return _call("Ingress", api.list_ingress_for_all_namespaces)


class _RawJsonResponse:
    """Minimal stand-in for a urllib3 HTTPResponse: ApiClient.deserialize only
    ever reads `.data`."""

    def __init__(self, data: str):
        self.data = data


def _list_endpoint_slices_tolerant(api: Any, list_fn: Callable[..., Any], *args: Any) -> list[Any]:
    """Some clusters (observed here for Services with zero current backend
    endpoints) return EndpointSlice objects with `endpoints` entirely omitted
    rather than an empty list. The generated kubernetes client model declares
    `endpoints` non-nullable and raises ValueError during deserialization,
    which crashes the whole list call -- so we fetch the raw response,
    default any missing/null `endpoints` to `[]`, and deserialize that."""
    try:
        raw_response = list_fn(*args, _preload_content=False)
    except ApiException as exc:
        raise ResourceAccessError("EndpointSlice", exc.status, exc.reason or "") from exc

    payload = json.loads(raw_response.data)
    for item in payload.get("items", []):
        if item.get("endpoints") is None:
            item["endpoints"] = []

    result = api.api_client.deserialize(_RawJsonResponse(json.dumps(payload)), "V1EndpointSliceList")
    return result.items


def list_endpoint_slices(mgr: KubeClientManager, context: str | None, namespace: str | None) -> list[Any]:
    api = mgr.discovery_v1(context)
    if namespace:
        return _list_endpoint_slices_tolerant(api, api.list_namespaced_endpoint_slice, namespace)
    return _list_endpoint_slices_tolerant(api, api.list_endpoint_slice_for_all_namespaces)


# Kind name -> fetcher. Fetchers taking (mgr, context, namespace); PersistentVolume
# is cluster-scoped so it's wrapped to accept (and ignore) a namespace arg here for a
# uniform call signature in graph/builder.py.
FETCHERS_BY_KIND: dict[str, Callable[[KubeClientManager, str | None, str | None], list[Any]]] = {
    "Pod": list_pods,
    "Service": list_services,
    "ConfigMap": list_config_maps,
    "Secret": list_secrets,
    "ServiceAccount": list_service_accounts,
    "PersistentVolumeClaim": list_persistent_volume_claims,
    "PersistentVolume": lambda mgr, context, namespace: list_persistent_volumes(mgr, context),
    "Node": lambda mgr, context, namespace: list_nodes(mgr, context),
    "ClusterRole": lambda mgr, context, namespace: list_cluster_roles(mgr, context),
    "ClusterRoleBinding": lambda mgr, context, namespace: list_cluster_role_bindings(mgr, context),
    "Deployment": list_deployments,
    "StatefulSet": list_stateful_sets,
    "DaemonSet": list_daemon_sets,
    "ReplicaSet": list_replica_sets,
    "Job": list_jobs,
    "CronJob": list_cron_jobs,
    "Ingress": list_ingresses,
    "EndpointSlice": list_endpoint_slices,
}
