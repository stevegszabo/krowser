import json
import shlex
import shutil
import subprocess
import time

from krowser.config import settings
from krowser.scan_errors import ScanFailedError, ScannerUnavailableError

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}


# Small in-memory cache -- many pods commonly share a base image, so
# re-running a multi-second (or first-run multi-minute, while Trivy's own
# vulnerability DB downloads) scan on every request would be wasteful.
_cache: dict[str, tuple[float, dict]] = {}


def _cached(image: str) -> dict | None:
    entry = _cache.get(image)
    if entry is None:
        return None
    fetched_at, result = entry
    if time.monotonic() - fetched_at > settings.vulnscan_cache_ttl_seconds:
        return None
    return result


def _run_trivy(image: str) -> tuple[dict, str]:
    trivy_path = shutil.which(settings.vulnscan_trivy_path)
    if trivy_path is None:
        raise ScannerUnavailableError(
            f"scanner binary {settings.vulnscan_trivy_path!r} not found on PATH"
        )

    argv = [trivy_path, "image", "--format", "json", "--quiet", image]
    command = shlex.join(argv)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=settings.vulnscan_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScanFailedError(f"scan of {image!r} timed out after {settings.vulnscan_timeout_seconds}s") from exc

    if proc.returncode != 0:
        raise ScanFailedError(f"scan of {image!r} failed: {proc.stderr.strip() or 'unknown error'}")

    try:
        return json.loads(proc.stdout), command
    except ValueError as exc:
        raise ScanFailedError(f"scan of {image!r} returned output that couldn't be parsed") from exc


_CVSS_VENDOR_PRIORITY = ("nvd", "redhat", "ghsa")


def _cvss(vuln: dict) -> tuple[float | None, str | None]:
    """Picks a single representative CVSS score/vector out of Trivy's
    per-vendor CVSS map, preferring the more authoritative vendors and V3
    over V2 -- vulnerabilities can carry scores from several sources
    (nvd, redhat, ghsa, ...) that don't always agree.
    """
    cvss = vuln.get("CVSS") or {}
    candidates = [cvss[v] for v in _CVSS_VENDOR_PRIORITY if v in cvss]
    candidates += [entry for vendor, entry in cvss.items() if vendor not in _CVSS_VENDOR_PRIORITY]
    for entry in candidates:
        score = entry.get("V3Score", entry.get("V2Score"))
        if score is not None:
            return score, entry.get("V3Vector", entry.get("V2Vector"))
    return None, None


def _parse_findings(raw: dict) -> list[dict]:
    findings = []
    for result in raw.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            cvss_score, cvss_vector = _cvss(vuln)
            findings.append(
                {
                    "id": vuln.get("VulnerabilityID", "UNKNOWN"),
                    "package": vuln.get("PkgName", "unknown"),
                    "installed_version": vuln.get("InstalledVersion"),
                    "fixed_version": vuln.get("FixedVersion"),
                    "severity": vuln.get("Severity", "UNKNOWN"),
                    "title": vuln.get("Title") or vuln.get("Description", ""),
                    "description": vuln.get("Description") or vuln.get("Title") or "",
                    "cvss_score": cvss_score,
                    "cvss_vector": cvss_vector,
                    "published_date": vuln.get("PublishedDate"),
                    "primary_url": vuln.get("PrimaryURL"),
                }
            )
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 99), f["package"]))
    return findings


def _summarize(findings: list[dict]) -> dict[str, int]:
    summary = {sev: 0 for sev in _SEVERITY_ORDER}
    for finding in findings:
        summary[finding["severity"]] = summary.get(finding["severity"], 0) + 1
    return summary


def scan_image(image: str) -> dict:
    """Scans a container image for known CVEs via Trivy, returning a summary
    count by severity plus a flat, severity-sorted list of findings. Results
    are cached in-process per image (see _cache) since scanning is
    comparatively expensive and the same image is often shared across pods.
    """
    cached = _cached(image)
    if cached is not None:
        return cached

    raw, command = _run_trivy(image)
    findings = _parse_findings(raw)
    result = {"image": image, "summary": _summarize(findings), "findings": findings, "command": command}
    _cache[image] = (time.monotonic(), result)
    return result
