from datetime import datetime, timezone

from krowser.k8s.client import KubeClientManager

# Every kind `kubectl rollout restart` supports. DaemonSet has no replica
# count (see scale.py) but does have a pod template to roll, so it can be
# restarted even though it can't be scaled.
RESTARTABLE_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

_PATCH_FUNCS = {
    "Deployment": lambda api: api.patch_namespaced_deployment,
    "StatefulSet": lambda api: api.patch_namespaced_stateful_set,
    "DaemonSet": lambda api: api.patch_namespaced_daemon_set,
}


def restart_workload(mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str) -> None:
    """Equivalent to `kubectl rollout restart` -- strategic-merge-patches the
    pod template's annotations with a restartedAt timestamp. The pod
    template spec changing is all it takes to trigger a new rollout; nothing
    else about the resource's spec needs to change, and a strategic merge
    patch (the SDK's default here) merges into the existing annotations map
    rather than replacing it wholesale.
    """
    patch_fn = _PATCH_FUNCS[kind](mgr.apps_v1(context))
    timestamp = datetime.now(timezone.utc).isoformat()
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"kubectl.kubernetes.io/restartedAt": timestamp},
                },
            },
        },
    }
    patch_fn(name, namespace, body)
