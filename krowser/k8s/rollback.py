from krowser.k8s.client import KubeClientManager
from krowser.k8s.status import humanize_age

# kubectl rollout history/undo work for all three of these, but Deployment
# keeps its history as actual (scaled-to-0) ReplicaSets, while
# StatefulSet/DaemonSet keep theirs as ControllerRevision objects instead --
# see _controller_revision_history()/_extract_template() for what that
# actually looks like and why it's still safe to replay.
ROLLBACK_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

_REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
_CHANGE_CAUSE_ANNOTATION = "kubernetes.io/change-cause"

_READ_FUNCS = {
    "Deployment": lambda api: api.read_namespaced_deployment,
    "StatefulSet": lambda api: api.read_namespaced_stateful_set,
    "DaemonSet": lambda api: api.read_namespaced_daemon_set,
}
_PATCH_FUNCS = {
    "Deployment": lambda api: api.patch_namespaced_deployment,
    "StatefulSet": lambda api: api.patch_namespaced_stateful_set,
    "DaemonSet": lambda api: api.patch_namespaced_daemon_set,
}


def _owned(items, owner_uid: str):
    return [item for item in items if any(ref.uid == owner_uid for ref in (item.metadata.owner_references or []))]


def _apply_template_patch(
    mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str, template: dict
) -> None:
    """Replaces spec.template wholesale via a JSON Patch (RFC 6902) "replace"
    op -- not a strategic-merge-patch body like scale.py/restart.py use,
    because a plain merge patch would deep-merge the *target* revision's
    template onto the *current* one rather than replacing it outright:
    containers and a container's env vars are both merge-patch lists keyed
    by `name`, so a normal merge only ever adds/updates entries, silently
    leaving behind any container/env var/volume the current template has
    that the target revision never did. This is the same approach kubectl's
    own DeploymentRollbacker uses for exactly that reason.
    """
    api = mgr.apps_v1(context)
    patch_fn = _PATCH_FUNCS[kind](api)
    patch_fn(name, namespace, [{"op": "replace", "path": "/spec/template", "value": template}])


def _deployment_history(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> list[dict]:
    api = mgr.apps_v1(context)
    deployment = api.read_namespaced_deployment(name, namespace)
    replicasets = _owned(api.list_namespaced_replica_set(namespace).items, deployment.metadata.uid)
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


def _controller_revision_history(
    mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str
) -> list[dict]:
    """Shared by StatefulSet and DaemonSet, which both keep history as
    ControllerRevision objects rather than actual ReplicaSets -- each one's
    own `.revision` int field is the revision number directly (no annotation
    to parse, unlike Deployment)."""
    api = mgr.apps_v1(context)
    owner = _READ_FUNCS[kind](api)(name, namespace)
    revisions = _owned(api.list_namespaced_controller_revision(namespace).items, owner.metadata.uid)

    if kind == "StatefulSet":
        # StatefulSetStatus carries exactly this -- the ControllerRevision
        # name its pods are presently running.
        current_name = owner.status.current_revision
        is_current = lambda rev: rev.metadata.name == current_name  # noqa: E731
    else:
        # DaemonSetStatus has no equivalent field at all -- the highest
        # revision number is the best available signal, and it stays correct
        # after a rollback too: Kubernetes reuses and bumps a matching old
        # ControllerRevision's own revision number rather than always
        # creating a new one (the same behavior observed for Deployment's
        # ReplicaSets when rolling back to a template that already exists).
        max_revision = max((rev.revision for rev in revisions), default=None)
        is_current = lambda rev: rev.revision == max_revision  # noqa: E731

    history = []
    for rev in revisions:
        annotations = rev.metadata.annotations or {}
        age, _ = humanize_age(rev.metadata.creation_timestamp)
        history.append(
            {
                "revision": rev.revision,
                "change_cause": annotations.get(_CHANGE_CAUSE_ANNOTATION, ""),
                "age": age,
                "is_current": is_current(rev),
            }
        )
    history.sort(key=lambda entry: entry["revision"], reverse=True)
    return history


def get_rollout_history(mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str) -> list[dict]:
    """Mirrors `kubectl rollout history` -- there's no separate history API
    to query, so this reads whatever object kind actually holds the history
    (see _deployment_history/_controller_revision_history for which)."""
    if kind == "Deployment":
        return _deployment_history(mgr, context, namespace, name)
    return _controller_revision_history(mgr, context, kind, namespace, name)


def _extract_template(controller_revision_data: dict) -> dict:
    """A ControllerRevision's `data` is the StatefulSet/DaemonSet
    controller's own strategic-merge-patch snapshot --
    `{"spec": {"template": {..., "$patch": "replace"}}}` -- which happens to
    already hold the complete pod template for that revision (confirmed
    against a real cluster; this is internal controller behavior, not a
    documented API contract, but it's the same mechanism `kubectl rollout
    undo` itself relies on). `$patch` is a strategic-merge-patch-only
    directive with no meaning as a real template field, so it's stripped
    before reusing the template as a JSON Patch "replace" value.
    """
    template = dict(controller_revision_data.get("spec", {}).get("template") or {})
    template.pop("$patch", None)
    return template


def rollback_workload(
    mgr: KubeClientManager, context: str | None, kind: str, namespace: str, name: str, revision: int
) -> int:
    """Equivalent to `kubectl rollout undo ... --to-revision=<revision>` for
    any of ROLLBACK_KINDS -- replaces spec.template with the target
    revision's own template; replica count and everything else in the spec
    is left untouched.
    """
    api = mgr.apps_v1(context)
    owner = _READ_FUNCS[kind](api)(name, namespace)

    if kind == "Deployment":
        replicasets = _owned(api.list_namespaced_replica_set(namespace).items, owner.metadata.uid)
        target = next(
            (rs for rs in replicasets if (rs.metadata.annotations or {}).get(_REVISION_ANNOTATION) == str(revision)),
            None,
        )
        if target is None:
            raise ValueError(f"revision {revision} not found for {kind.lower()} {name}")
        template = mgr.api_client_for(context).sanitize_for_serialization(target.spec.template)
    else:
        revisions = _owned(api.list_namespaced_controller_revision(namespace).items, owner.metadata.uid)
        target = next((rev for rev in revisions if rev.revision == revision), None)
        if target is None:
            raise ValueError(f"revision {revision} not found for {kind.lower()} {name}")
        template = _extract_template(target.data)

    _apply_template_patch(mgr, context, kind, namespace, name, template)
    return revision
