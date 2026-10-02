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
