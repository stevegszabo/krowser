import pytest
from kubernetes.client.rest import ApiException

from krowser.k8s.resource_types import (
    RESOURCE_TYPES,
    UnknownResourceTypeError,
    get_all_resource_types,
    get_resource_type,
)


def test_resource_type_order_matches_spec():
    assert [rt.label for rt in RESOURCE_TYPES] == [
        "Nodes",
        "ConfigMaps",
        "Secrets",
        "Ingresses",
        "Services",
        "PersistentVolumes",
        "PersistentVolumeClaims",
        "DaemonSets",
        "Deployments",
        "StatefulSets",
        "CronJobs",
        "Jobs",
        "Pods",
    ]


def test_groups_match_spec():
    groups = {rt.label: rt.group for rt in RESOURCE_TYPES}
    assert groups["Nodes"] == "Cluster"
    assert groups["ConfigMaps"] == "Config"
    assert groups["Secrets"] == "Config"
    assert groups["Ingresses"] == "Network"
    assert groups["Services"] == "Network"
    assert groups["PersistentVolumes"] == "Storage"
    assert groups["PersistentVolumeClaims"] == "Storage"
    assert groups["DaemonSets"] == "Workloads"
    assert groups["Pods"] == "Workloads"


def test_cluster_scoped_resource_types():
    non_namespaced = [rt.id for rt in RESOURCE_TYPES if not rt.namespaced]
    assert non_namespaced == ["cluster/nodes", "storage/persistentvolumes"]


def test_get_resource_type_unknown_raises():
    try:
        get_resource_type("nope")
    except UnknownResourceTypeError:
        pass
    else:
        raise AssertionError("expected UnknownResourceTypeError")


class _FakeCrdManager:
    """Just enough of KubeClientManager for the dynamic CRD-discovery path:
    list_crds()/get_crd() only ever call mgr.apiextensions_v1(context)."""

    def __init__(self, crds):
        self._crds = crds

    def apiextensions_v1(self, context):
        crds = self._crds

        class _Api:
            def list_custom_resource_definition(self):
                return type("_List", (), {"items": crds})()

            def read_custom_resource_definition(self, name):
                for crd in crds:
                    if crd.metadata.name == name:
                        return crd
                raise ApiException(status=404, reason="Not Found")

        return _Api()


def test_get_all_resource_types_appends_crds_under_custom_resources_group(make_crd):
    crd = make_crd("crd-1", "widgets.example.com", group="example.com", plural="widgets", kind="Widget")
    mgr = _FakeCrdManager([crd])

    types = get_all_resource_types(mgr, None)

    assert types[: len(RESOURCE_TYPES)] == RESOURCE_TYPES
    custom = types[len(RESOURCE_TYPES) :]
    assert len(custom) == 1
    rt = custom[0]
    assert rt.id == "customresources/widgets.example.com"
    assert rt.label == "widgets.example.com"
    assert rt.group == "Custom Resources"
    assert rt.kind == "Widget"
    assert rt.namespaced is True
    assert rt.api_group == "example.com"
    assert rt.version == "v1"
    assert rt.plural == "widgets"


def test_get_all_resource_types_sorts_crds_by_label(make_crd):
    crd_b = make_crd("crd-2", "bees.example.com", group="example.com", plural="bees", kind="Bee")
    crd_a = make_crd("crd-1", "ants.example.com", group="example.com", plural="ants", kind="Ant")
    mgr = _FakeCrdManager([crd_b, crd_a])

    types = get_all_resource_types(mgr, None)

    custom_labels = [rt.label for rt in types if rt.group == "Custom Resources"]
    assert custom_labels == ["ants.example.com", "bees.example.com"]


def test_get_resource_type_resolves_dynamic_crd_id(make_crd):
    crd = make_crd("crd-1", "widgets.example.com", group="example.com", plural="widgets", kind="Widget")
    mgr = _FakeCrdManager([crd])

    rt = get_resource_type("customresources/widgets.example.com", mgr, None)

    assert rt.kind == "Widget"
    assert rt.plural == "widgets"


def test_get_resource_type_unknown_dynamic_id_raises():
    mgr = _FakeCrdManager([])
    with pytest.raises(UnknownResourceTypeError):
        get_resource_type("customresources/nope.example.com", mgr, None)
