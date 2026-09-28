from krowser.k8s.client import KubeClientManager


def terminate_pod(mgr: KubeClientManager, context: str | None, namespace: str, name: str) -> None:
    """Deletes a Pod outright -- e.g. it's stuck, or a specific instance of a
    workload needs to be cycled by hand. A Pod backed by a controller
    (Deployment/StatefulSet/DaemonSet/etc.) is recreated automatically by
    that controller; a bare Pod with no owner is gone for good.
    """
    mgr.core_v1(context).delete_namespaced_pod(name, namespace)
