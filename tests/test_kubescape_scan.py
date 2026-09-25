import json
import subprocess

import pytest

import krowser.kubescape_scan as kubescape_scan_module
from krowser.kubescape_scan import scan_workload
from krowser.scan_errors import ScanFailedError, ScannerUnavailableError

KUBESCAPE_JSON = {
    "summaryDetails": {
        "complianceScore": 63.456,
        "controls": {
            "C-0004": {
                "controlID": "C-0004",
                "name": "Resources memory limit and request",
                "status": "failed",
                "severity": "High",
                "category": {"name": "Workload", "subCategory": {"name": "Resource management"}},
                "ResourceCounters": {
                    "passedResources": 0,
                    "failedResources": 1,
                    "skippedResources": 4,
                    "excludedResources": 0,
                },
            },
            "C-0013": {
                "controlID": "C-0013",
                "name": "Non-root containers",
                "status": "failed",
                "severity": "Medium",
                "category": {"name": "Workload"},
            },
            "C-0012": {
                "controlID": "C-0012",
                "name": "Applications credentials in configuration files",
                "status": "passed",
                "severity": "High",
                "category": {"name": "Secrets"},
            },
            "C-0257": {
                "controlID": "C-0257",
                "name": "Workload with PVC access",
                "status": "skipped",
                "severity": "Medium",
                "category": {"name": "Workload"},
            },
        },
    }
}


@pytest.fixture(autouse=True)
def _clear_cache():
    kubescape_scan_module._cache.clear()
    yield
    kubescape_scan_module._cache.clear()


def _fake_run(json_content=None, stderr="", returncode=1):
    def fake_run(argv, **kwargs):
        if json_content is not None:
            output_path = argv[argv.index("--output") + 1]
            with open(output_path, "w") as f:
                json.dump(json_content, f)
        # Kubescape's exit code reflects the compliance verdict, not scan
        # success -- a real, successful scan still commonly exits 1.
        return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout="", stderr=stderr)

    return fake_run


def test_scan_workload_parses_only_failed_controls_sorted_by_severity(monkeypatch):
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")
    monkeypatch.setattr(kubescape_scan_module.subprocess, "run", _fake_run(KUBESCAPE_JSON))

    result = scan_workload("Deployment", "ns", "web", None)

    assert result["score"] == 63.5
    assert [f["id"] for f in result["findings"]] == ["C-0004", "C-0013"]
    assert result["summary"] == {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 1, "LOW": 0, "UNKNOWN": 0}
    first = result["findings"][0]
    assert first["name"] == "Resources memory limit and request"
    assert first["severity"] == "HIGH"
    assert first["category"] == "Workload"
    assert first["subcategory"] == "Resource management"
    assert first["failed_resources"] == 1
    assert first["total_resources"] == 5
    assert first["docs_url"] == "https://kubescape.io/docs/controls/c-0004/"

    # C-0013 has no `category.subCategory`/`ResourceCounters` in the fixture
    # -- confirm those degrade to empty/zero instead of KeyError-ing, while
    # docs_url is still constructed from the id alone.
    second = next(f for f in result["findings"] if f["id"] == "C-0013")
    assert second["subcategory"] == ""
    assert second["failed_resources"] == 0
    assert second["total_resources"] == 0
    assert second["docs_url"] == "https://kubescape.io/docs/controls/c-0013/"


def test_scan_workload_treats_nonzero_exit_as_success_when_output_written(monkeypatch):
    # Confirms exit code is correctly ignored -- this is real kubescape
    # behavior: a normal, complete scan still exits 1 whenever any control
    # fails (the common case).
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")
    monkeypatch.setattr(kubescape_scan_module.subprocess, "run", _fake_run(KUBESCAPE_JSON, returncode=1))

    result = scan_workload("Deployment", "ns", "web", None)

    assert len(result["findings"]) == 2


def test_scan_workload_handles_no_failed_controls(monkeypatch):
    clean = {"summaryDetails": {"complianceScore": 100, "controls": {}}}
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")
    monkeypatch.setattr(kubescape_scan_module.subprocess, "run", _fake_run(clean))

    result = scan_workload("Deployment", "ns", "web", None)

    assert result["findings"] == []
    assert result["summary"] == {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}


def test_scan_workload_caches_result_and_skips_second_subprocess_call(monkeypatch):
    calls = []
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _fake_run(KUBESCAPE_JSON)(argv, **kwargs)

    monkeypatch.setattr(kubescape_scan_module.subprocess, "run", fake_run)

    scan_workload("Deployment", "ns", "web", None)
    scan_workload("Deployment", "ns", "web", None)

    assert len(calls) == 1


def test_scan_workload_raises_scanner_unavailable_when_binary_missing(monkeypatch):
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: None)

    with pytest.raises(ScannerUnavailableError):
        scan_workload("Deployment", "ns", "web", None)


def test_scan_workload_raises_scan_failed_when_output_empty(monkeypatch):
    # Simulates "has a parent"/"not found" -- kubescape writes nothing to
    # --output and prints an Error: line to stderr.
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")
    monkeypatch.setattr(
        kubescape_scan_module.subprocess,
        "run",
        _fake_run(json_content=None, stderr="...\nError: resource ns/Pod/web has a parent and cannot be scanned"),
    )

    with pytest.raises(ScanFailedError, match="has a parent"):
        scan_workload("Pod", "ns", "web", None)


def test_scan_workload_raises_scan_failed_on_timeout(monkeypatch):
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="kubescape", timeout=180)

    monkeypatch.setattr(kubescape_scan_module.subprocess, "run", fake_run)

    with pytest.raises(ScanFailedError):
        scan_workload("Deployment", "ns", "web", None)


def test_scan_workload_includes_the_command_executed(monkeypatch):
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")
    monkeypatch.setattr(kubescape_scan_module.subprocess, "run", _fake_run(KUBESCAPE_JSON))

    result = scan_workload("Deployment", "ns", "web", None)

    # --output's value is a nondeterministic temp file path, so this checks
    # the static parts of the command rather than an exact string match.
    assert result["command"].startswith(
        "/usr/local/bin/kubescape scan workload Deployment/web --namespace ns --format json --output"
    )
    assert result["command"].endswith("--keep-local --scan-images=false")


def test_scan_workload_passes_kube_context_when_given(monkeypatch):
    monkeypatch.setattr(kubescape_scan_module.shutil, "which", lambda path: "/usr/local/bin/kubescape")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _fake_run(KUBESCAPE_JSON)(argv, **kwargs)

    monkeypatch.setattr(kubescape_scan_module.subprocess, "run", fake_run)

    scan_workload("Deployment", "ns", "web", "my-context")

    assert "--kube-context" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--kube-context") + 1] == "my-context"
