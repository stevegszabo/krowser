from typing import Any, Callable

from kubernetes.client.rest import ApiException

from krowser.k8s.client import KubeClientManager
from krowser.k8s.fetchers import ResourceAccessError


def _get(kind: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except ApiException as exc:
        raise ResourceAccessError(kind, exc.status, exc.reason or "") from exc


def get_pod(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Pod", mgr.core_v1(context).read_namespaced_pod, name, namespace)


def get_service(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Service", mgr.core_v1(context).read_namespaced_service, name, namespace)


def get_config_map(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("ConfigMap", mgr.core_v1(context).read_namespaced_config_map, name, namespace)


def get_secret(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Secret", mgr.core_v1(context).read_namespaced_secret, name, namespace)


def get_persistent_volume_claim(
    mgr: KubeClientManager, context: str | None, namespace: str, name: str
) -> Any:
    return _get(
        "PersistentVolumeClaim",
        mgr.core_v1(context).read_namespaced_persistent_volume_claim,
        name,
        namespace,
    )


def get_persistent_volume(mgr: KubeClientManager, context: str | None, name: str) -> Any:
    # Cluster-scoped: no namespace variant exists.
    return _get("PersistentVolume", mgr.core_v1(context).read_persistent_volume, name)


def get_node(mgr: KubeClientManager, context: str | None, name: str) -> Any:
    # Cluster-scoped: no namespace variant exists.
    return _get("Node", mgr.core_v1(context).read_node, name)


def get_deployment(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Deployment", mgr.apps_v1(context).read_namespaced_deployment, name, namespace)


def get_stateful_set(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("StatefulSet", mgr.apps_v1(context).read_namespaced_stateful_set, name, namespace)


def get_daemon_set(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("DaemonSet", mgr.apps_v1(context).read_namespaced_daemon_set, name, namespace)


def get_replica_set(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("ReplicaSet", mgr.apps_v1(context).read_namespaced_replica_set, name, namespace)


def get_job(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Job", mgr.batch_v1(context).read_namespaced_job, name, namespace)


def get_cron_job(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("CronJob", mgr.batch_v1(context).read_namespaced_cron_job, name, namespace)


def get_ingress(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Ingress", mgr.networking_v1(context).read_namespaced_ingress, name, namespace)


def get_endpoint_slice(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get(
        "EndpointSlice", mgr.discovery_v1(context).read_namespaced_endpoint_slice, name, namespace
    )


# Kind name -> single-object getter, mirroring FETCHERS_BY_KIND in fetchers.py.
# Takes (mgr, context, namespace, name); PersistentVolume is cluster-scoped so
# it's wrapped to accept (and ignore) a namespace arg for a uniform call signature.
GETTERS_BY_KIND: dict[str, Callable[[KubeClientManager, str | None, str | None, str], Any]] = {
    "Pod": get_pod,
    "Service": get_service,
    "ConfigMap": get_config_map,
    "Secret": get_secret,
    "PersistentVolumeClaim": get_persistent_volume_claim,
    "PersistentVolume": lambda mgr, context, namespace, name: get_persistent_volume(mgr, context, name),
    "Node": lambda mgr, context, namespace, name: get_node(mgr, context, name),
    "Deployment": get_deployment,
    "StatefulSet": get_stateful_set,
    "DaemonSet": get_daemon_set,
    "ReplicaSet": get_replica_set,
    "Job": get_job,
    "CronJob": get_cron_job,
    "Ingress": get_ingress,
    "EndpointSlice": get_endpoint_slice,
}
