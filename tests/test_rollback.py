from krowser.k8s.rollback import ROLLBACK_KINDS, get_rollout_history, rollback_workload


class _Obj:
    """Minimal stand-in for the kubernetes client's generated models --
    attribute access only, no validation."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _owner_ref(uid):
    return _Obj(uid=uid)


def _replicaset(uid, *, owner_uid, revision, change_cause=None, created_at=None):
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
        spec=_Obj(template=_Obj(marker=f"template-for-{uid}")),
    )


def _controller_revision(uid, *, owner_uid, revision, data, change_cause=None, created_at=None, name=None):
    annotations = {}
    if change_cause is not None:
        annotations["kubernetes.io/change-cause"] = change_cause
    return _Obj(
        metadata=_Obj(
            uid=uid,
            name=name or uid,
            annotations=annotations,
            owner_references=[_owner_ref(owner_uid)],
            creation_timestamp=created_at,
        ),
        revision=revision,
        data=data,
    )


def _template_data(marker):
    # Mirrors a real ControllerRevision's .data shape -- see
    # rollback.py's _extract_template() docstring.
    return {"spec": {"template": {"$patch": "replace", "marker": marker}}}


def _deployment(uid="dep-1", current_revision="3"):
    return _Obj(metadata=_Obj(uid=uid, annotations={"deployment.kubernetes.io/revision": current_revision}))


def _statefulset(uid="sts-1", current_revision_name=None):
    return _Obj(metadata=_Obj(uid=uid), status=_Obj(current_revision=current_revision_name))


def _daemonset(uid="ds-1"):
    return _Obj(metadata=_Obj(uid=uid))


class _FakeApiClient:
    def sanitize_for_serialization(self, obj):
        return {"marker": obj.marker}


class _FakeAppsV1Api:
    def __init__(self, owner=None, replicasets=(), controller_revisions=()):
        self.owner = owner
        self._replicasets = list(replicasets)
        self._controller_revisions = list(controller_revisions)
        self.patch_calls = []

    def read_namespaced_deployment(self, name, namespace):
        return self.owner

    def read_namespaced_stateful_set(self, name, namespace):
        return self.owner

    def read_namespaced_daemon_set(self, name, namespace):
        return self.owner

    def list_namespaced_replica_set(self, namespace):
        return _Obj(items=self._replicasets)

    def list_namespaced_controller_revision(self, namespace):
        return _Obj(items=self._controller_revisions)

    def patch_namespaced_deployment(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))

    def patch_namespaced_stateful_set(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))

    def patch_namespaced_daemon_set(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def apps_v1(self, context):
        return self._api

    def api_client_for(self, context):
        return _FakeApiClient()


def test_rollback_kinds_covers_all_three():
    assert set(ROLLBACK_KINDS) == {"Deployment", "StatefulSet", "DaemonSet"}


# -- Deployment (ReplicaSet-based history) -----------------------------------


def test_deployment_history_lists_revisions_newest_first_with_current_flagged():
    owner = _deployment(uid="dep-1", current_revision="3")
    replicasets = [
        _replicaset("rs-1", owner_uid="dep-1", revision=1, change_cause="initial deploy"),
        _replicaset("rs-2", owner_uid="dep-1", revision=2),
        _replicaset("rs-3", owner_uid="dep-1", revision=3),
        _replicaset("rs-unrelated", owner_uid="some-other-dep", revision=9),
    ]
    api = _FakeAppsV1Api(owner=owner, replicasets=replicasets)
    mgr = _FakeManager(api)

    history = get_rollout_history(mgr, None, "Deployment", "ns", "web")

    assert [h["revision"] for h in history] == [3, 2, 1]
    assert history[0]["is_current"] is True
    assert history[1]["is_current"] is False
    assert history[2]["change_cause"] == "initial deploy"


def test_deployment_history_skips_replicasets_without_revision_annotation():
    owner = _deployment(uid="dep-1", current_revision="1")
    replicasets = [
        _replicaset("rs-1", owner_uid="dep-1", revision=1),
        _replicaset("rs-stray", owner_uid="dep-1", revision=None),
    ]
    api = _FakeAppsV1Api(owner=owner, replicasets=replicasets)
    mgr = _FakeManager(api)

    history = get_rollout_history(mgr, None, "Deployment", "ns", "web")

    assert len(history) == 1
    assert history[0]["revision"] == 1


def test_rollback_deployment_replaces_template_via_json_patch():
    owner = _deployment(uid="dep-1", current_revision="3")
    replicasets = [
        _replicaset("rs-1", owner_uid="dep-1", revision=1),
        _replicaset("rs-3", owner_uid="dep-1", revision=3),
    ]
    api = _FakeAppsV1Api(owner=owner, replicasets=replicasets)
    mgr = _FakeManager(api)

    result = rollback_workload(mgr, None, "Deployment", "ns", "web", revision=1)

    assert result == 1
    assert len(api.patch_calls) == 1
    name, namespace, body = api.patch_calls[0]
    assert (name, namespace) == ("web", "ns")
    # A JSON Patch (RFC 6902) "replace" op, not a strategic-merge-patch dict
    # -- see _apply_template_patch()'s docstring for why that matters.
    assert body == [{"op": "replace", "path": "/spec/template", "value": {"marker": "template-for-rs-1"}}]


def test_rollback_deployment_raises_for_unknown_revision():
    owner = _deployment(uid="dep-1")
    api = _FakeAppsV1Api(owner=owner, replicasets=[_replicaset("rs-1", owner_uid="dep-1", revision=1)])
    mgr = _FakeManager(api)

    try:
        rollback_workload(mgr, None, "Deployment", "ns", "web", revision=99)
        assert False, "expected ValueError"
    except ValueError:
        pass


# -- StatefulSet / DaemonSet (ControllerRevision-based history) --------------


def test_statefulset_history_flags_current_by_revision_name():
    owner = _statefulset(uid="sts-1", current_revision_name="web-abc123")
    revisions = [
        _controller_revision("cr-1", owner_uid="sts-1", revision=1, data=_template_data("old"), name="web-old"),
        _controller_revision("cr-2", owner_uid="sts-1", revision=2, data=_template_data("new"), name="web-abc123"),
        _controller_revision("cr-unrelated", owner_uid="other", revision=5, data=_template_data("x")),
    ]
    api = _FakeAppsV1Api(owner=owner, controller_revisions=revisions)
    mgr = _FakeManager(api)

    history = get_rollout_history(mgr, None, "StatefulSet", "ns", "web")

    assert [h["revision"] for h in history] == [2, 1]
    assert history[0]["is_current"] is True
    assert history[1]["is_current"] is False


def test_rollback_statefulset_extracts_template_and_strips_patch_marker():
    owner = _statefulset(uid="sts-1", current_revision_name="web-new")
    revisions = [
        _controller_revision("cr-1", owner_uid="sts-1", revision=1, data=_template_data("old"), name="web-old"),
        _controller_revision("cr-2", owner_uid="sts-1", revision=2, data=_template_data("new"), name="web-new"),
    ]
    api = _FakeAppsV1Api(owner=owner, controller_revisions=revisions)
    mgr = _FakeManager(api)

    result = rollback_workload(mgr, None, "StatefulSet", "ns", "web", revision=1)

    assert result == 1
    name, namespace, body = api.patch_calls[0]
    assert (name, namespace) == ("web", "ns")
    assert body == [{"op": "replace", "path": "/spec/template", "value": {"marker": "old"}}]


def test_rollback_statefulset_raises_for_unknown_revision():
    owner = _statefulset(uid="sts-1")
    api = _FakeAppsV1Api(
        owner=owner,
        controller_revisions=[_controller_revision("cr-1", owner_uid="sts-1", revision=1, data=_template_data("x"))],
    )
    mgr = _FakeManager(api)

    try:
        rollback_workload(mgr, None, "StatefulSet", "ns", "web", revision=99)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_daemonset_history_flags_current_as_highest_revision():
    owner = _daemonset(uid="ds-1")
    revisions = [
        _controller_revision("cr-1", owner_uid="ds-1", revision=1, data=_template_data("old")),
        _controller_revision("cr-2", owner_uid="ds-1", revision=2, data=_template_data("new")),
    ]
    api = _FakeAppsV1Api(owner=owner, controller_revisions=revisions)
    mgr = _FakeManager(api)

    history = get_rollout_history(mgr, None, "DaemonSet", "ns", "web")

    assert [h["revision"] for h in history] == [2, 1]
    assert history[0]["is_current"] is True
    assert history[1]["is_current"] is False


def test_rollback_daemonset_replaces_template():
    owner = _daemonset(uid="ds-1")
    revisions = [
        _controller_revision("cr-1", owner_uid="ds-1", revision=1, data=_template_data("old")),
        _controller_revision("cr-2", owner_uid="ds-1", revision=2, data=_template_data("new")),
    ]
    api = _FakeAppsV1Api(owner=owner, controller_revisions=revisions)
    mgr = _FakeManager(api)

    result = rollback_workload(mgr, None, "DaemonSet", "ns", "web", revision=1)

    assert result == 1
    name, namespace, body = api.patch_calls[0]
    assert (name, namespace) == ("web", "ns")
    assert body == [{"op": "replace", "path": "/spec/template", "value": {"marker": "old"}}]
