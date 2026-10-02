from typing import Any

from krowser.k8s.client import KubeClientManager

# Every kind `kubectl rollout status` understands -- a superset of
# krowser.k8s.scale.SCALABLE_KINDS (DaemonSet has no replica count, but it
# does roll its pods out one node at a time and so has a real rollout to
# watch) and exactly krowser.k8s.restart.RESTARTABLE_KINDS.
ROLLOUT_STATUS_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

_READ_FUNCS = {
    "Deployment": lambda api: api.read_namespaced_deployment,
    "StatefulSet": lambda api: api.read_namespaced_stateful_set,
    "DaemonSet": lambda api: api.read_namespaced_daemon_set,
}


def _deployment_rollout_status(obj: Any) -> tuple[bool, str]:
    """Mirrors `kubectl rollout status deployment/...`'s own algorithm."""
    spec = obj.spec
    status = obj.status
    if spec.paused:
        return True, "deployment is paused"

    generation = obj.metadata.generation or 0
    observed_generation = status.observed_generation or 0
    if generation > observed_generation:
        return False, "waiting for deployment spec update to be observed"

    desired = spec.replicas if spec.replicas is not None else 1
    updated = status.updated_replicas or 0
    if updated < desired:
        return False, f"waiting for rollout to finish: {updated} out of {desired} new replicas have been updated"

    total = status.replicas or 0
    if total > updated:
        return False, f"waiting for rollout to finish: {total - updated} old replicas are pending termination"

    available = status.available_replicas or 0
    if available < updated:
        return False, f"waiting for rollout to finish: {available} of {updated} updated replicas are available"

    return True, "deployment successfully rolled out"


def _statefulset_rollout_status(obj: Any) -> tuple[bool, str]:
    """Mirrors `kubectl rollout status statefulset/...`'s own algorithm
    for the common (non-partitioned) RollingUpdate case."""
    spec = obj.spec
    status = obj.status

    update_strategy = "RollingUpdate"
    if spec.update_strategy is not None and spec.update_strategy.type is not None:
        update_strategy = spec.update_strategy.type
    if update_strategy != "RollingUpdate":
        return True, f"rollout status is not tracked for the {update_strategy} update strategy"

    generation = obj.metadata.generation or 0
    observed_generation = status.observed_generation or 0
    if observed_generation == 0 or generation > observed_generation:
        return False, "waiting for statefulset spec update to be observed"

    desired = spec.replicas if spec.replicas is not None else 1
    ready = status.ready_replicas or 0
    if ready < desired:
        return False, f"waiting for pods to be ready: {ready} out of {desired} ready"

    updated = status.updated_replicas or 0
    if updated < desired:
        return False, f"waiting for rollout to finish: {updated} out of {desired} pods have been updated"

    if status.update_revision != status.current_revision:
        return False, "waiting for statefulset rolling update to complete"

    return True, "statefulset rolling update complete"


def _daemonset_rollout_status(obj: Any) -> tuple[bool, str]:
    """Mirrors `kubectl rollout status daemonset/...`'s own algorithm."""
    spec = obj.spec
    status = obj.status

    update_strategy = "RollingUpdate"
    if spec.update_strategy is not None and spec.update_strategy.type is not None:
        update_strategy = spec.update_strategy.type
    if update_strategy != "RollingUpdate":
        return True, f"rollout status is not tracked for the {update_strategy} update strategy"

    generation = obj.metadata.generation or 0
    observed_generation = status.observed_generation or 0
    if generation > observed_generation:
        return False, "waiting for daemon set spec update to be observed"

    desired = status.desired_number_scheduled or 0
    updated = status.updated_number_scheduled or 0
    if updated < desired:
        return False, f"waiting for rollout to finish: {updated} out of {desired} new pods have been updated"

    available = status.number_available or 0
    if available < desired:
        return False, f"waiting for rollout to finish: {available} of {desired} updated pods are available"

    return True, "daemon set successfully rolled out"


_STATUS_FUNCS = {
    "Deployment": _deployment_rollout_status,
    "StatefulSet": _statefulset_rollout_status,
    "DaemonSet": _daemonset_rollout_status,
}


def get_rollout_status(
    mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str
) -> dict:
    read_fn = _READ_FUNCS[kind](mgr.apps_v1(context))
    obj = read_fn(name, namespace)
    complete, message = _STATUS_FUNCS[kind](obj)
    return {"complete": complete, "message": message}
