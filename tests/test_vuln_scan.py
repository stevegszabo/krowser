import json
import subprocess

import pytest

import krowser.vuln_scan as vuln_scan_module
from krowser.vuln_scan import ScanFailedError, ScannerUnavailableError, scan_image

TRIVY_JSON = {
    "Results": [
        {
            "Target": "nginx:1.21 (debian 11.3)",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2023-0001",
                    "PkgName": "libssl1.1",
                    "InstalledVersion": "1.1.1n",
                    "FixedVersion": "1.1.1t",
                    "Severity": "CRITICAL",
                    "Title": "openssl: something bad",
                    "Description": "A longer explanation of the openssl issue.",
                    "CVSS": {"nvd": {"V3Score": 9.8, "V3Vector": "CVSS:3.1/AV:N/AC:L"}},
                    "PublishedDate": "2023-01-15T00:00:00Z",
                    "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2023-0001",
                },
                {
                    "VulnerabilityID": "CVE-2023-0002",
                    "PkgName": "zlib1g",
                    "InstalledVersion": "1.2.11",
                    "Severity": "LOW",
                    "Title": "zlib: minor issue",
                },
            ],
        },
        {
            "Target": "app (gobinary)",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2023-0003",
                    "PkgName": "golang.org/x/net",
                    "InstalledVersion": "v0.1.0",
                    "FixedVersion": "v0.2.0",
                    "Severity": "HIGH",
                    "Title": "net: bad thing",
                },
            ],
        },
    ]
}


@pytest.fixture(autouse=True)
def _clear_cache():
    vuln_scan_module._cache.clear()
    yield
    vuln_scan_module._cache.clear()


def _fake_completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_scan_image_parses_and_sorts_findings_by_severity(monkeypatch):
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: "/usr/local/bin/trivy")
    monkeypatch.setattr(
        vuln_scan_module.subprocess, "run", lambda *a, **k: _fake_completed(stdout=json.dumps(TRIVY_JSON))
    )

    result = scan_image("nginx:1.21")

    assert result["image"] == "nginx:1.21"
    assert [f["id"] for f in result["findings"]] == ["CVE-2023-0001", "CVE-2023-0003", "CVE-2023-0002"]
    assert result["summary"] == {"CRITICAL": 1, "HIGH": 1, "MEDIUM": 0, "LOW": 1, "UNKNOWN": 0}
    first = result["findings"][0]
    assert first["package"] == "libssl1.1"
    assert first["installed_version"] == "1.1.1n"
    assert first["fixed_version"] == "1.1.1t"
    assert first["severity"] == "CRITICAL"
    assert first["description"] == "A longer explanation of the openssl issue."
    assert first["cvss_score"] == 9.8
    assert first["cvss_vector"] == "CVSS:3.1/AV:N/AC:L"
    assert first["published_date"] == "2023-01-15T00:00:00Z"
    assert first["primary_url"] == "https://avd.aquasec.com/nvd/cve-2023-0001"

    # CVE-2023-0002 has no Description/CVSS/PublishedDate/PrimaryURL in the
    # fixture -- confirm those all degrade gracefully instead of KeyError-ing,
    # with description falling back to the (required) Title.
    second = next(f for f in result["findings"] if f["id"] == "CVE-2023-0002")
    assert second["description"] == "zlib: minor issue"
    assert second["cvss_score"] is None
    assert second["cvss_vector"] is None
    assert second["published_date"] is None
    assert second["primary_url"] is None


def test_scan_image_includes_the_command_executed(monkeypatch):
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: "/usr/local/bin/trivy")
    monkeypatch.setattr(
        vuln_scan_module.subprocess, "run", lambda *a, **k: _fake_completed(stdout=json.dumps({"Results": []}))
    )

    result = scan_image("nginx:1.21")

    assert result["command"] == "/usr/local/bin/trivy image --format json --quiet nginx:1.21"


def test_scan_image_handles_clean_image_with_no_vulnerabilities(monkeypatch):
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: "/usr/local/bin/trivy")
    monkeypatch.setattr(
        vuln_scan_module.subprocess, "run", lambda *a, **k: _fake_completed(stdout=json.dumps({"Results": []}))
    )

    result = scan_image("scratch:latest")

    assert result["findings"] == []
    assert result["summary"] == {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}


def test_scan_image_caches_result_and_skips_second_subprocess_call(monkeypatch):
    calls = []
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: "/usr/local/bin/trivy")

    def fake_run(*args, **kwargs):
        calls.append(args)
        return _fake_completed(stdout=json.dumps(TRIVY_JSON))

    monkeypatch.setattr(vuln_scan_module.subprocess, "run", fake_run)

    scan_image("nginx:1.21")
    scan_image("nginx:1.21")

    assert len(calls) == 1


def test_scan_image_raises_scanner_unavailable_when_binary_missing(monkeypatch):
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: None)

    with pytest.raises(ScannerUnavailableError):
        scan_image("nginx:1.21")


def test_scan_image_raises_scan_failed_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: "/usr/local/bin/trivy")
    monkeypatch.setattr(
        vuln_scan_module.subprocess,
        "run",
        lambda *a, **k: _fake_completed(returncode=1, stderr="unable to pull image"),
    )

    with pytest.raises(ScanFailedError):
        scan_image("nginx:1.21")


def test_scan_image_raises_scan_failed_on_timeout(monkeypatch):
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: "/usr/local/bin/trivy")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="trivy", timeout=180)

    monkeypatch.setattr(vuln_scan_module.subprocess, "run", fake_run)

    with pytest.raises(ScanFailedError):
        scan_image("nginx:1.21")


def test_scan_image_raises_scan_failed_on_malformed_json(monkeypatch):
    monkeypatch.setattr(vuln_scan_module.shutil, "which", lambda path: "/usr/local/bin/trivy")
    monkeypatch.setattr(vuln_scan_module.subprocess, "run", lambda *a, **k: _fake_completed(stdout="not json"))

    with pytest.raises(ScanFailedError):
        scan_image("nginx:1.21")
