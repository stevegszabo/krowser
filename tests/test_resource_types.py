from krowser.k8s.resource_types import (
    RESOURCE_TYPES,
    UnknownResourceTypeError,
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
