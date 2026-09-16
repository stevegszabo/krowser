import json
import os
import shutil
import subprocess
import tempfile
import time

from krowser.config import settings
from krowser.scan_errors import ScanFailedError, ScannerUnavailableError

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}

# Small in-memory cache, keyed by the resource identity being scanned --
# rescanning on every pane open would be wasteful given scans take real time.
_cache: dict[tuple[str, str, str, str | None], tuple[float, dict]] = {}


def _cached(key: tuple[str, str, str, str | None]) -> dict | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    fetched_at, result = entry
    if time.monotonic() - fetched_at > settings.kubescape_cache_ttl_seconds:
        return None
    return result


def _extract_error(stderr: str) -> str:
    lines = [line for line in stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("Error:"):
            return line[len("Error:") :].strip()
    return lines[-1] if lines else "kubescape scan failed"


def _run_kubescape(kind: str, namespace: str, name: str, context: str | None) -> dict:
    kubescape_path = shutil.which(settings.kubescape_path)
    if kubescape_path is None:
        raise ScannerUnavailableError(f"scanner binary {settings.kubescape_path!r} not found on PATH")

    fd, tmp_path = tempfile.mkstemp(prefix="krowser-kubescape-", suffix=".json")
    os.close(fd)
    try:
        argv = [
            kubescape_path,
            "scan",
            "workload",
            f"{kind}/{name}",
            "--namespace",
            namespace,
            "--format",
            "json",
            "--output",
            tmp_path,
            "--keep-local",
            "--scan-images=false",
        ]
        if context:
            argv += ["--kube-context", context]

        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=settings.kubescape_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScanFailedError(
                f"scan of {kind}/{name} timed out after {settings.kubescape_timeout_seconds}s"
            ) from exc

        # Kubescape's exit code reflects the compliance verdict (nonzero
        # whenever any control fails, which is the common case), not whether
        # the scan itself succeeded -- a real error instead leaves the
        # --output file empty, which is what we actually check.
        try:
            with open(tmp_path) as f:
                raw_text = f.read()
        except OSError:
            raw_text = ""

        if not raw_text.strip():
            raise ScanFailedError(f"scan of {kind}/{name} failed: {_extract_error(proc.stderr)}")

        try:
            return json.loads(raw_text)
        except ValueError as exc:
            raise ScanFailedError(f"scan of {kind}/{name} returned output that couldn't be parsed") from exc
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _parse_findings(raw: dict) -> list[dict]:
    controls = (raw.get("summaryDetails") or {}).get("controls") or {}
    findings = []
    for control_id, control in controls.items():
        if control.get("status") != "failed":
            continue
        findings.append(
            {
                "id": control.get("controlID", control_id),
                "name": control.get("name", "unknown control"),
                "severity": (control.get("severity") or "unknown").upper(),
                "category": (control.get("category") or {}).get("name", ""),
            }
        )
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 99), f["name"]))
    return findings


def _summarize(findings: list[dict]) -> dict[str, int]:
    summary = {sev: 0 for sev in _SEVERITY_ORDER}
    for finding in findings:
        summary[finding["severity"]] = summary.get(finding["severity"], 0) + 1
    return summary


def scan_workload(kind: str, namespace: str, name: str, context: str | None) -> dict:
    """Scans a workload's configuration for posture/compliance issues via
    Kubescape, returning a compliance score plus a summary count and a flat,
    severity-sorted list of failed controls. Kubescape also computes image
    CVEs as a side effect of this scan, which is deliberately ignored here --
    see krowser.vuln_scan for image scanning (Trivy-based; surfacing a
    second, differently-sourced CVE count for the same image would just be
    confusing). Results are cached per resource identity, see _cache.
    """
    key = (kind, namespace, name, context)
    cached = _cached(key)
    if cached is not None:
        return cached

    raw = _run_kubescape(kind, namespace, name, context)
    findings = _parse_findings(raw)
    score = round((raw.get("summaryDetails") or {}).get("complianceScore", 0), 1)
    result = {"score": score, "summary": _summarize(findings), "findings": findings}
    _cache[key] = (time.monotonic(), result)
    return result
