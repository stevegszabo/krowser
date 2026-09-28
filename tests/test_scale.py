import pytest

from krowser.k8s.scale import SCALABLE_KINDS, get_replicas, get_rollout_status, scale_workload


class _FakeScale:
    def __init__(self, replicas):
        self.spec = type("_Spec", (), {"replicas": replicas})()


class _Obj:
    """Minimal stand-in for the kubernetes client's generated models --
    attribute access only, no validation."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _deployment(
    *,
    generation=1,
    observed_generation=1,
    paused=False,
    spec_replicas=3,
    updated_replicas=3,
    replicas=3,
    available_replicas=3,
):
    return _Obj(
        metadata=_Obj(generation=generation),
        spec=_Obj(paused=paused, replicas=spec_replicas),
        status=_Obj(
            observed_generation=observed_generation,
            updated_replicas=updated_replicas,
            replicas=replicas,
            available_replicas=available_replicas,
        ),
    )


def _statefulset(
    *,
    generation=1,
    observed_generation=1,
    update_strategy_type="RollingUpdate",
    spec_replicas=3,
    ready_replicas=3,
    updated_replicas=3,
    current_revision="rev-1",
    update_revision="rev-1",
):
    return _Obj(
        metadata=_Obj(generation=generation),
        spec=_Obj(
            replicas=spec_replicas,
            update_strategy=_Obj(type=update_strategy_type),
        ),
        status=_Obj(
            observed_generation=observed_generation,
            ready_replicas=ready_replicas,
            updated_replicas=updated_replicas,
            current_revision=current_revision,
            update_revision=update_revision,
        ),
    )


class _FakeAppsV1Api:
    def __init__(self, replicas=1, obj=None):
        self.replicas = replicas
        self.patch_calls = []
        self.obj = obj

    def read_namespaced_deployment_scale(self, name, namespace):
        return _FakeScale(self.replicas)

    def read_namespaced_stateful_set_scale(self, name, namespace):
        return _FakeScale(self.replicas)

    def patch_namespaced_deployment_scale(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))
        self.replicas = body["spec"]["replicas"]
        return _FakeScale(self.replicas)

    def patch_namespaced_stateful_set_scale(self, name, namespace, body):
        self.patch_calls.append((name, namespace, body))
        self.replicas = body["spec"]["replicas"]
        return _FakeScale(self.replicas)

    def read_namespaced_deployment(self, name, namespace):
        return self.obj

    def read_namespaced_stateful_set(self, name, namespace):
        return self.obj


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def apps_v1(self, context):
        return self._api


def test_scalable_kinds_is_deployment_and_statefulset_only():
    assert set(SCALABLE_KINDS) == {"Deployment", "StatefulSet"}


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet"])
def test_get_replicas_reads_current_count(kind):
    api = _FakeAppsV1Api(replicas=3)
    mgr = _FakeManager(api)

    assert get_replicas(mgr, None, kind, "ns", "web") == 3


def test_get_replicas_treats_none_as_zero():
    api = _FakeAppsV1Api(replicas=None)
    mgr = _FakeManager(api)

    assert get_replicas(mgr, None, "Deployment", "ns", "web") == 0


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet"])
def test_scale_workload_patches_via_scale_subresource(kind):
    api = _FakeAppsV1Api(replicas=1)
    mgr = _FakeManager(api)

    result = scale_workload(mgr, None, kind, "ns", "web", 5)

    assert result == 5
    assert api.patch_calls == [("web", "ns", {"spec": {"replicas": 5}})]


def test_deployment_rollout_status_complete_when_all_caught_up():
    api = _FakeAppsV1Api(obj=_deployment())
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result == {"complete": True, "message": "deployment successfully rolled out"}


def test_deployment_rollout_status_paused():
    api = _FakeAppsV1Api(obj=_deployment(paused=True))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result == {"complete": True, "message": "deployment is paused"}


def test_deployment_rollout_status_waiting_for_spec_observed():
    api = _FakeAppsV1Api(obj=_deployment(generation=2, observed_generation=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "spec update to be observed" in result["message"]


def test_deployment_rollout_status_waiting_for_new_replicas():
    api = _FakeAppsV1Api(obj=_deployment(spec_replicas=5, updated_replicas=2))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "2 out of 5" in result["message"]


def test_deployment_rollout_status_waiting_for_old_replicas_to_terminate():
    api = _FakeAppsV1Api(obj=_deployment(spec_replicas=3, updated_replicas=3, replicas=4))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "pending termination" in result["message"]


def test_deployment_rollout_status_waiting_for_availability():
    api = _FakeAppsV1Api(obj=_deployment(spec_replicas=3, updated_replicas=3, replicas=3, available_replicas=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "1 of 3 updated replicas are available" in result["message"]


def test_statefulset_rollout_status_complete():
    api = _FakeAppsV1Api(obj=_statefulset())
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result == {"complete": True, "message": "statefulset rolling update complete"}


def test_statefulset_rollout_status_non_rolling_update_reports_complete():
    api = _FakeAppsV1Api(obj=_statefulset(update_strategy_type="OnDelete"))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result["complete"] is True
    assert "OnDelete" in result["message"]


def test_statefulset_rollout_status_waiting_for_ready_pods():
    api = _FakeAppsV1Api(obj=_statefulset(spec_replicas=3, ready_replicas=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result["complete"] is False
    assert "1 out of 3" in result["message"]


def test_statefulset_rollout_status_waiting_for_revision_to_match():
    api = _FakeAppsV1Api(obj=_statefulset(current_revision="rev-1", update_revision="rev-2"))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result["complete"] is False
    assert "rolling update to complete" in result["message"]
