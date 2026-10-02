import pytest

from krowser.k8s.rollback import ROLLBACK_KINDS, get_rollout_history, rollback_deployment


class _Obj:
    """Minimal stand-in for the kubernetes client's generated models --
    attribute access only, no validation."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _owner_ref(uid):
    return _Obj(uid=uid)


def _replicaset(uid, *, owner_uid, revision, change_cause=None, replicas=0, created_at=None):
    annotations = {}
    if revision is not None:
        annotations["deployment.kubernetes.io/revision"] = str(revision)
    if change_cause is not None:
        annotations["kubernetes.io/change-cause"] = change_cause
    return _Obj(
        metadata=_Obj(
            uid=uid,
            annotations=annotations,
            owner_references=[_owner_ref(owner_uid)],
            creation_timestamp=created_at,
        ),
        spec=_Obj(replicas=replicas, template=_Obj(marker=f"template-for-{uid}")),
    )


def _deployment(uid="dep-1", current_revision="3"):
    return _Obj(
        metadata=_Obj(uid=uid, annotations={"deployment.kubernetes.io/revision": current_revision}),
    )


class _FakeApiClient:
    def sanitize_for_serialization(self, obj):
        return {"marker": obj.marker}


class _FakeAppsV1Api:
    def __init__(self, deployment, replicasets):
        self._deployment = deployment
        self._replicasets = replicasets
        self.patch_calls = []

    def read_namespaced_deployment(self, name, namespace):
        return self._deployment

    def list_namespaced_replica_set(self, namespace):
        return _Obj(items=self._replicasets)

    def patch_namespaced_deployment(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def apps_v1(self, context):
        return self._api

    def api_client_for(self, context):
        return _FakeApiClient()


def test_rollback_kinds_is_deployment_only():
    assert set(ROLLBACK_KINDS) == {"Deployment"}


def test_get_rollout_history_lists_revisions_newest_first_with_current_flagged():
    deployment = _deployment(uid="dep-1", current_revision="3")
    replicasets = [
        _replicaset("rs-1", owner_uid="dep-1", revision=1, change_cause="initial deploy"),
        _replicaset("rs-2", owner_uid="dep-1", revision=2),
        _replicaset("rs-3", owner_uid="dep-1", revision=3, replicas=3),
        _replicaset("rs-unrelated", owner_uid="some-other-dep", revision=9),
    ]
    api = _FakeAppsV1Api(deployment, replicasets)
    mgr = _FakeManager(api)

    history = get_rollout_history(mgr, None, "ns", "web")

    assert [h["revision"] for h in history] == [3, 2, 1]
    assert history[0]["is_current"] is True
    assert history[1]["is_current"] is False
    assert history[2]["change_cause"] == "initial deploy"


def test_get_rollout_history_skips_replicasets_without_revision_annotation():
    deployment = _deployment(uid="dep-1", current_revision="1")
    replicasets = [
        _replicaset("rs-1", owner_uid="dep-1", revision=1),
        _replicaset("rs-stray", owner_uid="dep-1", revision=None),
    ]
    api = _FakeAppsV1Api(deployment, replicasets)
    mgr = _FakeManager(api)

    history = get_rollout_history(mgr, None, "ns", "web")

    assert len(history) == 1
    assert history[0]["revision"] == 1


def test_rollback_deployment_patches_template_from_target_revision():
    deployment = _deployment(uid="dep-1", current_revision="3")
    replicasets = [
        _replicaset("rs-1", owner_uid="dep-1", revision=1),
        _replicaset("rs-2", owner_uid="dep-1", revision=2),
        _replicaset("rs-3", owner_uid="dep-1", revision=3, replicas=3),
    ]
    api = _FakeAppsV1Api(deployment, replicasets)
    mgr = _FakeManager(api)

    result = rollback_deployment(mgr, None, "ns", "web", revision=1)

    assert result == 1
    assert len(api.patch_calls) == 1
    name, namespace, body = api.patch_calls[0]
    assert (name, namespace) == ("web", "ns")
    assert body == {"spec": {"template": {"marker": "template-for-rs-1"}}}


def test_rollback_deployment_raises_for_unknown_revision():
    deployment = _deployment(uid="dep-1")
    replicasets = [_replicaset("rs-1", owner_uid="dep-1", revision=1)]
    api = _FakeAppsV1Api(deployment, replicasets)
    mgr = _FakeManager(api)

    with pytest.raises(ValueError):
        rollback_deployment(mgr, None, "ns", "web", revision=99)
