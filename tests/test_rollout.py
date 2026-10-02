from krowser.k8s.rollout import ROLLOUT_STATUS_KINDS, get_rollout_status


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


def _daemonset(
    *,
    generation=1,
    observed_generation=1,
    update_strategy_type="RollingUpdate",
    desired_number_scheduled=3,
    updated_number_scheduled=3,
    number_available=3,
):
    return _Obj(
        metadata=_Obj(generation=generation),
        spec=_Obj(update_strategy=_Obj(type=update_strategy_type)),
        status=_Obj(
            observed_generation=observed_generation,
            desired_number_scheduled=desired_number_scheduled,
            updated_number_scheduled=updated_number_scheduled,
            number_available=number_available,
        ),
    )


class _FakeAppsV1Api:
    def __init__(self, obj):
        self.obj = obj

    def read_namespaced_deployment(self, name, namespace):
        return self.obj

    def read_namespaced_stateful_set(self, name, namespace):
        return self.obj

    def read_namespaced_daemon_set(self, name, namespace):
        return self.obj


class _FakeManager:
    def __init__(self, api):
        self._api = api

    def apps_v1(self, context):
        return self._api


def test_rollout_status_kinds_covers_all_three():
    assert set(ROLLOUT_STATUS_KINDS) == {"Deployment", "StatefulSet", "DaemonSet"}


def test_deployment_rollout_status_complete_when_all_caught_up():
    api = _FakeAppsV1Api(_deployment())
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result == {"complete": True, "message": "deployment successfully rolled out"}


def test_deployment_rollout_status_paused():
    api = _FakeAppsV1Api(_deployment(paused=True))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result == {"complete": True, "message": "deployment is paused"}


def test_deployment_rollout_status_waiting_for_spec_observed():
    api = _FakeAppsV1Api(_deployment(generation=2, observed_generation=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "spec update to be observed" in result["message"]


def test_deployment_rollout_status_waiting_for_new_replicas():
    api = _FakeAppsV1Api(_deployment(spec_replicas=5, updated_replicas=2))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "2 out of 5" in result["message"]


def test_deployment_rollout_status_waiting_for_old_replicas_to_terminate():
    api = _FakeAppsV1Api(_deployment(spec_replicas=3, updated_replicas=3, replicas=4))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "pending termination" in result["message"]


def test_deployment_rollout_status_waiting_for_availability():
    api = _FakeAppsV1Api(_deployment(spec_replicas=3, updated_replicas=3, replicas=3, available_replicas=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "Deployment", "ns", "web")

    assert result["complete"] is False
    assert "1 of 3 updated replicas are available" in result["message"]


def test_statefulset_rollout_status_complete():
    api = _FakeAppsV1Api(_statefulset())
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result == {"complete": True, "message": "statefulset rolling update complete"}


def test_statefulset_rollout_status_non_rolling_update_reports_complete():
    api = _FakeAppsV1Api(_statefulset(update_strategy_type="OnDelete"))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result["complete"] is True
    assert "OnDelete" in result["message"]


def test_statefulset_rollout_status_waiting_for_ready_pods():
    api = _FakeAppsV1Api(_statefulset(spec_replicas=3, ready_replicas=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result["complete"] is False
    assert "1 out of 3" in result["message"]


def test_statefulset_rollout_status_waiting_for_revision_to_match():
    api = _FakeAppsV1Api(_statefulset(current_revision="rev-1", update_revision="rev-2"))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "StatefulSet", "ns", "web")

    assert result["complete"] is False
    assert "rolling update to complete" in result["message"]


def test_daemonset_rollout_status_complete():
    api = _FakeAppsV1Api(_daemonset())
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "DaemonSet", "ns", "web")

    assert result == {"complete": True, "message": "daemon set successfully rolled out"}


def test_daemonset_rollout_status_non_rolling_update_reports_complete():
    api = _FakeAppsV1Api(_daemonset(update_strategy_type="OnDelete"))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "DaemonSet", "ns", "web")

    assert result["complete"] is True
    assert "OnDelete" in result["message"]


def test_daemonset_rollout_status_waiting_for_spec_observed():
    api = _FakeAppsV1Api(_daemonset(generation=2, observed_generation=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "DaemonSet", "ns", "web")

    assert result["complete"] is False
    assert "spec update to be observed" in result["message"]


def test_daemonset_rollout_status_waiting_for_updated_pods():
    api = _FakeAppsV1Api(_daemonset(desired_number_scheduled=3, updated_number_scheduled=1))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "DaemonSet", "ns", "web")

    assert result["complete"] is False
    assert "1 out of 3" in result["message"]


def test_daemonset_rollout_status_waiting_for_availability():
    api = _FakeAppsV1Api(_daemonset(desired_number_scheduled=3, updated_number_scheduled=3, number_available=2))
    mgr = _FakeManager(api)

    result = get_rollout_status(mgr, None, "DaemonSet", "ns", "web")

    assert result["complete"] is False
    assert "2 of 3 updated pods are available" in result["message"]
