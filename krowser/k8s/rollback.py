from krowser.k8s.client import KubeClientManager
from krowser.k8s.status import humanize_age

# kubectl rollout history/undo work the same way for Deployment, StatefulSet,
# and DaemonSet, but the latter two keep their history as ControllerRevision
# objects (an opaque patch blob, not a plain pod template) rather than actual
# ReplicaSets -- correctly replaying that format is meaningfully riskier to
# get wrong against a real cluster, so for now this only covers Deployment,
# where a revision is simply a previous ReplicaSet's own pod template.
ROLLBACK_KINDS = ("Deployment",)

_REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
_CHANGE_CAUSE_ANNOTATION = "kubernetes.io/change-cause"


def _owned_replicasets(mgr: KubeClientManager, context: str | None, namespace: str, deployment_uid: str):
    api = mgr.apps_v1(context)
    all_replicasets = api.list_namespaced_replica_set(namespace).items
    return [
        rs
        for rs in all_replicasets
        if any(ref.uid == deployment_uid for ref in (rs.metadata.owner_references or []))
    ]


def get_rollout_history(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> list[dict]:
    """Mirrors `kubectl rollout history deployment/...` -- a Deployment's
    past ReplicaSets are kept around (scaled to 0) specifically so their pod
    template and `deployment.kubernetes.io/revision` annotation can serve as
    rollback targets; there's no separate history API to query.
    """
    api = mgr.apps_v1(context)
    deployment = api.read_namespaced_deployment(name, namespace)
    replicasets = _owned_replicasets(mgr, context, namespace, deployment.metadata.uid)

    current_revision = (deployment.metadata.annotations or {}).get(_REVISION_ANNOTATION)

    history = []
    for rs in replicasets:
        annotations = rs.metadata.annotations or {}
        revision = annotations.get(_REVISION_ANNOTATION)
        if revision is None:
            continue
        age, _ = humanize_age(rs.metadata.creation_timestamp)
        history.append(
            {
                "revision": int(revision),
                "change_cause": annotations.get(_CHANGE_CAUSE_ANNOTATION, ""),
                "age": age,
                "is_current": revision == current_revision,
            }
        )
    history.sort(key=lambda entry: entry["revision"], reverse=True)
    return history


def rollback_deployment(
    mgr: KubeClientManager, context: str | None, namespace: str, name: str, revision: int
) -> int:
    """Equivalent to `kubectl rollout undo deployment/...
    --to-revision=<revision>` -- patches spec.template to match the target
    revision's own ReplicaSet's template, which is all a Deployment rollback
    actually changes; replica count and everything else in the spec is left
    untouched.
    """
    api = mgr.apps_v1(context)
    deployment = api.read_namespaced_deployment(name, namespace)
    replicasets = _owned_replicasets(mgr, context, namespace, deployment.metadata.uid)

    target = None
    for rs in replicasets:
        annotations = rs.metadata.annotations or {}
        if annotations.get(_REVISION_ANNOTATION) == str(revision):
            target = rs
            break
    if target is None:
        raise ValueError(f"revision {revision} not found for deployment {name}")

    template = mgr.api_client_for(context).sanitize_for_serialization(target.spec.template)
    api.patch_namespaced_deployment(name, namespace, {"spec": {"template": template}})
    return revision
