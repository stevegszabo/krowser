from typing import Any

from krowser.k8s.client import KubeClientManager

# The only two kinds where "scale" has an unambiguous, safe meaning: both
# have a native replicas field and a real /scale subresource. DaemonSet runs
# exactly one pod per matching node (no replica count to set); Job's
# parallelism isn't quite the same concept and can behave confusingly on
# already-running pods, and CronJob has no running instance to scale at all.
SCALABLE_KINDS = ("Deployment", "StatefulSet")

_READ_SCALE_FUNCS = {
    "Deployment": lambda api: api.read_namespaced_deployment_scale,
    "StatefulSet": lambda api: api.read_namespaced_stateful_set_scale,
}
_PATCH_SCALE_FUNCS = {
    "Deployment": lambda api: api.patch_namespaced_deployment_scale,
    "StatefulSet": lambda api: api.patch_namespaced_stateful_set_scale,
}
_READ_FUNCS = {
    "Deployment": lambda api: api.read_namespaced_deployment,
    "StatefulSet": lambda api: api.read_namespaced_stateful_set,
}


def get_replicas(mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str) -> int:
    read_fn = _READ_SCALE_FUNCS[kind](mgr.apps_v1(context))
    scale = read_fn(name, namespace)
    return scale.spec.replicas or 0


def scale_workload(
    mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str, replicas: int
) -> int:
    """Sets a Deployment/StatefulSet's replica count via its dedicated
    /scale subresource -- the same one `kubectl scale` uses -- rather than
    patching the whole resource, so this only ever needs RBAC on the scale
    subresource, not full write access to the resource itself. Returns the
    replica count the API server actually stored, confirming the write
    succeeded rather than trusting the caller's requested value blindly.
    """
    patch_fn = _PATCH_SCALE_FUNCS[kind](mgr.apps_v1(context))
    result = patch_fn(name, namespace, {"spec": {"replicas": replicas}})
    return result.spec.replicas


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


def get_rollout_status(
    mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str
) -> dict:
    read_fn = _READ_FUNCS[kind](mgr.apps_v1(context))
    obj = read_fn(name, namespace)
    if kind == "Deployment":
        complete, message = _deployment_rollout_status(obj)
    else:
        complete, message = _statefulset_rollout_status(obj)
    return {"complete": complete, "message": message}
