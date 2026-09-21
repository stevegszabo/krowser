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


def get_resource_events(
    mgr: KubeClientManager, context: str | None, namespace: str | None, kind: str, name: str
) -> list[Any]:
    # Matches kubectl's own approach: filter by involvedObject fields, not
    # uid, so this can't distinguish a deleted-and-recreated same-named
    # resource's events from the current one -- kubectl describe has this
    # same limitation. Filtering on kind as well as name (not just name, as
    # this used to before generalizing beyond Pods) avoids matching events
    # for an unrelated resource of a different kind that happens to share a
    # name in the same namespace.
    field_selector = f"involvedObject.name={name},involvedObject.kind={kind}"
    api = mgr.core_v1(context)
    if namespace:
        events = _get("Event", api.list_namespaced_event, namespace, field_selector=field_selector)
    else:
        # Cluster-scoped resources (ClusterRole, Node, PersistentVolume, ...)
        # have no namespace of their own to search -- their events can land
        # in any namespace, so this has to search all of them.
        events = _get("Event", api.list_event_for_all_namespaces, field_selector=field_selector)
    return events.items


def get_pod_logs(
    mgr: KubeClientManager, context: str | None, namespace: str, name: str, container: str, tail_lines: int = 100
) -> str:
    return _get(
        "Pod",
        mgr.core_v1(context).read_namespaced_pod_log,
        name,
        namespace,
        container=container,
        tail_lines=tail_lines,
    )


def get_service(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Service", mgr.core_v1(context).read_namespaced_service, name, namespace)


def get_config_map(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("ConfigMap", mgr.core_v1(context).read_namespaced_config_map, name, namespace)


def get_secret(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Secret", mgr.core_v1(context).read_namespaced_secret, name, namespace)


def get_service_account(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("ServiceAccount", mgr.core_v1(context).read_namespaced_service_account, name, namespace)


def get_role(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("Role", mgr.rbac_authorization_v1(context).read_namespaced_role, name, namespace)


def get_role_binding(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get("RoleBinding", mgr.rbac_authorization_v1(context).read_namespaced_role_binding, name, namespace)


def get_cluster_role(mgr: KubeClientManager, context: str | None, name: str) -> Any:
    # Cluster-scoped: no namespace variant exists.
    return _get("ClusterRole", mgr.rbac_authorization_v1(context).read_cluster_role, name)


def get_cluster_role_binding(mgr: KubeClientManager, context: str | None, name: str) -> Any:
    # Cluster-scoped: no namespace variant exists.
    return _get("ClusterRoleBinding", mgr.rbac_authorization_v1(context).read_cluster_role_binding, name)


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


def get_network_policy(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get(
        "NetworkPolicy", mgr.networking_v1(context).read_namespaced_network_policy, name, namespace
    )


def get_endpoint_slice(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> Any:
    return _get(
        "EndpointSlice", mgr.discovery_v1(context).read_namespaced_endpoint_slice, name, namespace
    )


def get_horizontal_pod_autoscaler(
    mgr: KubeClientManager, context: str | None, namespace: str, name: str
) -> Any:
    return _get(
        "HorizontalPodAutoscaler",
        mgr.autoscaling_v2(context).read_namespaced_horizontal_pod_autoscaler,
        name,
        namespace,
    )


# Kind name -> single-object getter, mirroring FETCHERS_BY_KIND in fetchers.py.
# Takes (mgr, context, namespace, name); PersistentVolume is cluster-scoped so
# it's wrapped to accept (and ignore) a namespace arg for a uniform call signature.
GETTERS_BY_KIND: dict[str, Callable[[KubeClientManager, str | None, str | None, str], Any]] = {
    "Pod": get_pod,
    "Service": get_service,
    "ConfigMap": get_config_map,
    "Secret": get_secret,
    "ServiceAccount": get_service_account,
    "Role": get_role,
    "RoleBinding": get_role_binding,
    "ClusterRole": lambda mgr, context, namespace, name: get_cluster_role(mgr, context, name),
    "ClusterRoleBinding": lambda mgr, context, namespace, name: get_cluster_role_binding(mgr, context, name),
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
    "NetworkPolicy": get_network_policy,
    "EndpointSlice": get_endpoint_slice,
    "HorizontalPodAutoscaler": get_horizontal_pod_autoscaler,
}
