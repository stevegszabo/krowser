from datetime import datetime, timedelta, timezone

import pytest
from kubernetes import client as k8s


def _meta(uid, name, namespace="ns", owner_refs=None, labels=None):
    return k8s.V1ObjectMeta(
        uid=uid,
        name=name,
        namespace=namespace,
        owner_references=owner_refs,
        labels=labels or {},
        creation_timestamp=datetime.now(timezone.utc) - timedelta(minutes=5),
    )


def make_owner_ref(kind, name, uid):
    return k8s.V1OwnerReference(api_version="v1", kind=kind, name=name, uid=uid, controller=True)


@pytest.fixture
def make_pod():
    def _make(
        uid,
        name,
        namespace="ns",
        owner_refs=None,
        labels=None,
        phase="Running",
        volumes=None,
        containers=None,
        image_pull_secrets=None,
    ):
        return k8s.V1Pod(
            metadata=_meta(uid, name, namespace, owner_refs, labels),
            spec=k8s.V1PodSpec(
                containers=containers or [k8s.V1Container(name="app", image="nginx")],
                volumes=volumes or [],
                image_pull_secrets=image_pull_secrets,
            ),
            status=k8s.V1PodStatus(
                phase=phase,
                container_statuses=[
                    k8s.V1ContainerStatus(
                        name="app",
                        ready=True,
                        restart_count=0,
                        image="nginx",
                        image_id="",
                        state=k8s.V1ContainerState(running=k8s.V1ContainerStateRunning()),
                    )
                ],
            ),
        )

    return _make


@pytest.fixture
def make_deployment():
    def _make(uid, name, namespace="ns", replicas=1, ready_replicas=1):
        return k8s.V1Deployment(
            metadata=_meta(uid, name, namespace),
            spec=k8s.V1DeploymentSpec(
                replicas=replicas, selector=k8s.V1LabelSelector(), template=k8s.V1PodTemplateSpec()
            ),
            status=k8s.V1DeploymentStatus(ready_replicas=ready_replicas),
        )

    return _make


@pytest.fixture
def make_replica_set():
    def _make(uid, name, namespace="ns", owner_refs=None, replicas=1, ready_replicas=1):
        return k8s.V1ReplicaSet(
            metadata=_meta(uid, name, namespace, owner_refs),
            spec=k8s.V1ReplicaSetSpec(replicas=replicas, selector=k8s.V1LabelSelector()),
            status=k8s.V1ReplicaSetStatus(replicas=replicas, ready_replicas=ready_replicas),
        )

    return _make


@pytest.fixture
def make_service():
    def _make(uid, name, namespace="ns", selector=None, svc_type="ClusterIP", lb_ingress=None):
        status = k8s.V1ServiceStatus()
        if lb_ingress is not None:
            status.load_balancer = k8s.V1LoadBalancerStatus(ingress=lb_ingress)
        return k8s.V1Service(
            metadata=_meta(uid, name, namespace),
            spec=k8s.V1ServiceSpec(selector=selector, type=svc_type, cluster_ip="10.0.0.1"),
            status=status,
        )

    return _make


@pytest.fixture
def make_ingress():
    def _make(uid, name, namespace="ns", backend_service_names=(), lb_ingress=None):
        paths = [
            k8s.V1HTTPIngressPath(
                path="/",
                path_type="Prefix",
                backend=k8s.V1IngressBackend(
                    service=k8s.V1IngressServiceBackend(
                        name=svc_name, port=k8s.V1ServiceBackendPort(number=80)
                    )
                ),
            )
            for svc_name in backend_service_names
        ]
        status = k8s.V1IngressStatus()
        if lb_ingress is not None:
            status.load_balancer = k8s.V1LoadBalancerStatus(ingress=lb_ingress)
        return k8s.V1Ingress(
            metadata=_meta(uid, name, namespace),
            spec=k8s.V1IngressSpec(
                rules=[k8s.V1IngressRule(host="example.com", http=k8s.V1HTTPIngressRuleValue(paths=paths))]
            ),
            status=status,
        )

    return _make


@pytest.fixture
def make_pvc():
    def _make(uid, name, namespace="ns", phase="Bound", volume_name=None, storage_class=None):
        return k8s.V1PersistentVolumeClaim(
            metadata=_meta(uid, name, namespace),
            spec=k8s.V1PersistentVolumeClaimSpec(
                volume_name=volume_name, storage_class_name=storage_class
            ),
            status=k8s.V1PersistentVolumeClaimStatus(phase=phase, capacity={"storage": "1Gi"}),
        )

    return _make


@pytest.fixture
def make_pv():
    def _make(uid, name, phase="Bound", claim_ref_namespace=None, claim_ref_name=None):
        claim_ref = None
        if claim_ref_namespace:
            claim_ref = k8s.V1ObjectReference(namespace=claim_ref_namespace, name=claim_ref_name)
        return k8s.V1PersistentVolume(
            metadata=_meta(uid, name, namespace=None),
            spec=k8s.V1PersistentVolumeSpec(
                capacity={"storage": "1Gi"},
                claim_ref=claim_ref,
                persistent_volume_reclaim_policy="Delete",
            ),
            status=k8s.V1PersistentVolumeStatus(phase=phase),
        )

    return _make


@pytest.fixture
def make_config_map():
    def _make(uid, name, namespace="ns", data=None):
        return k8s.V1ConfigMap(
            metadata=_meta(uid, name, namespace),
            data=data or {"key": "value"},
        )

    return _make


@pytest.fixture
def make_secret():
    def _make(uid, name, namespace="ns", secret_type="Opaque"):
        return k8s.V1Secret(
            metadata=_meta(uid, name, namespace),
            data={"key": "dmFsdWU="},
            type=secret_type,
        )

    return _make


@pytest.fixture
def make_endpoint_slice():
    def _make(uid, name, namespace="ns", service_name=None, pod_targets=(), owner_refs=None):
        labels = {"kubernetes.io/service-name": service_name} if service_name else {}
        endpoints = [
            k8s.V1Endpoint(
                addresses=["10.0.0.1"],
                target_ref=k8s.V1ObjectReference(kind="Pod", name=pod_name, namespace=namespace, uid=pod_uid),
                conditions=k8s.V1EndpointConditions(ready=ready, serving=ready, terminating=False),
            )
            for pod_name, pod_uid, ready in pod_targets
        ]
        return k8s.V1EndpointSlice(
            metadata=_meta(uid, name, namespace, owner_refs, labels),
            address_type="IPv4",
            endpoints=endpoints,
            ports=[],
        )

    return _make


@pytest.fixture
def make_job():
    def _make(uid, name, namespace="ns", owner_refs=None, completions=1, succeeded=0, failed=0):
        return k8s.V1Job(
            metadata=_meta(uid, name, namespace, owner_refs),
            spec=k8s.V1JobSpec(
                completions=completions, template=k8s.V1PodTemplateSpec(), backoff_limit=6
            ),
            status=k8s.V1JobStatus(succeeded=succeeded, failed=failed),
        )

    return _make


@pytest.fixture
def make_cron_job():
    def _make(uid, name, namespace="ns", suspend=False, schedule="*/5 * * * *"):
        return k8s.V1CronJob(
            metadata=_meta(uid, name, namespace),
            spec=k8s.V1CronJobSpec(
                schedule=schedule, suspend=suspend, job_template=k8s.V1JobTemplateSpec()
            ),
            status=k8s.V1CronJobStatus(),
        )

    return _make
