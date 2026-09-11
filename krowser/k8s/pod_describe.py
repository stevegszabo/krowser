"""Builds a kubectl-describe-style summary for a Pod, using only data
already available from the Kubernetes API client (the Pod object plus its
Events) -- no shelling out to the kubectl binary. This is a close
approximation of `kubectl describe pod`'s most commonly consulted sections,
not a byte-for-byte reimplementation of kubectl's own describer.

describe_pod() returns a list of {"title", "text"} sections (Details,
Init Containers, Containers, Conditions, Volumes, Events) rather than one
flat string, so the frontend can render each under its own heading.
"""

from typing import Any

from krowser.k8s.status import humanize_age

_LABEL_WIDTH = 16


def _pad(value: str, width: int) -> str:
    # Guarantees at least a 2-space gap even when a cell overflows its
    # nominal column width (e.g. a long label like "Priority Class Name:").
    return value.ljust(width) if len(value) < width else value + "  "


def _kv(label: str, value: Any, indent: str = "") -> str:
    shown = value if value not in (None, "") else "<none>"
    return f"{indent}{_pad(label + ':', _LABEL_WIDTH)}{shown}"


def _fmt_labels(labels: dict[str, str] | None) -> list[str]:
    if not labels:
        return ["<none>"]
    return [f"{k}={v}" for k, v in sorted(labels.items())]


def _fmt_annotations(annotations: dict[str, str] | None) -> list[str]:
    if not annotations:
        return ["<none>"]
    return [f"{k}: {v}" for k, v in sorted(annotations.items())]


def _fmt_multiline(label: str, items: list[str], indent: str = "") -> list[str]:
    if not items:
        return [f"{indent}{_pad(label + ':', _LABEL_WIDTH)}<none>"]
    lines = [f"{indent}{_pad(label + ':', _LABEL_WIDTH)}{items[0]}"]
    lines += [f"{indent}{_pad('', _LABEL_WIDTH)}{item}" for item in items[1:]]
    return lines


def _fmt_controlled_by(owner_refs: list[Any] | None) -> str:
    if not owner_refs:
        return "<none>"
    ref = owner_refs[0]
    return f"{ref.kind}/{ref.name}"


def _fmt_ports(ports: list[Any] | None, attr: str) -> str:
    if not ports:
        return "<none>"
    formatted = []
    for p in ports:
        value = getattr(p, attr)
        if value is None:
            continue
        formatted.append(f"{value}/{p.protocol or 'TCP'}")
    return ", ".join(formatted) if formatted else "<none>"


def _fmt_resources(resources: Any) -> list[str]:
    lines = []
    for section, values in (("Limits", resources.limits if resources else None),
                             ("Requests", resources.requests if resources else None)):
        if not values:
            continue
        lines.append(f"  {section}:")
        for name, qty in sorted(values.items()):
            lines.append(f"    {name}:{'':<{max(1, 8 - len(name))}}{qty}")
    return lines


def _fmt_env(env: list[Any] | None) -> list[str]:
    if not env:
        return ["  Environment:  <none>"]
    lines = ["  Environment:"]
    for e in env:
        if e.value is not None:
            lines.append(f"    {e.name}:  {e.value}")
        elif e.value_from is not None:
            vf = e.value_from
            if vf.config_map_key_ref:
                ref = vf.config_map_key_ref
                lines.append(f"    {e.name}:  <set to the key '{ref.key}' in config map '{ref.name}'>")
            elif vf.secret_key_ref:
                ref = vf.secret_key_ref
                lines.append(f"    {e.name}:  <set to the key '{ref.key}' in secret '{ref.name}'>")
            elif vf.field_ref:
                lines.append(f"    {e.name}:  (source: {vf.field_ref.field_path})")
            else:
                lines.append(f"    {e.name}:  <set from resource field>")
        else:
            lines.append(f"    {e.name}:  ")
    return lines


def _fmt_mounts(mounts: list[Any] | None) -> list[str]:
    if not mounts:
        return ["  Mounts:  <none>"]
    lines = ["  Mounts:"]
    for m in mounts:
        suffix = " (ro)" if m.read_only else " (rw)"
        lines.append(f"    {m.mount_path} from {m.name}{suffix}")
    return lines


def _fmt_container_state(state: Any) -> list[str]:
    if state is None:
        return ["  State:          Unknown"]
    if state.running:
        return ["  State:          Running", f"    Started:      {state.running.started_at}"]
    if state.waiting:
        lines = ["  State:          Waiting"]
        if state.waiting.reason:
            lines.append(f"    Reason:       {state.waiting.reason}")
        return lines
    if state.terminated:
        t = state.terminated
        lines = ["  State:          Terminated"]
        if t.reason:
            lines.append(f"    Reason:       {t.reason}")
        if t.exit_code is not None:
            lines.append(f"    Exit Code:    {t.exit_code}")
        if t.started_at:
            lines.append(f"    Started:      {t.started_at}")
        if t.finished_at:
            lines.append(f"    Finished:     {t.finished_at}")
        return lines
    return ["  State:          Unknown"]


def _fmt_container_group(containers: list[Any], statuses_by_name: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for i, c in enumerate(containers):
        if i > 0:
            lines.append("")
        status = statuses_by_name.get(c.name)
        lines.append(f"{c.name}:")
        if status is not None:
            lines.append(f"  Container ID:   {status.container_id or '<none>'}")
        lines.append(f"  Image:          {c.image}")
        if status is not None:
            lines.append(f"  Image ID:       {status.image_id or '<none>'}")
        lines.append(f"  Port:           {_fmt_ports(c.ports, 'container_port')}")
        lines.append(f"  Host Port:      {_fmt_ports(c.ports, 'host_port')}")
        if status is not None:
            lines += _fmt_container_state(status.state)
            lines.append(f"  Ready:          {status.ready}")
            lines.append(f"  Restart Count:  {status.restart_count}")
        lines += _fmt_resources(c.resources)
        lines += _fmt_env(c.env)
        lines += _fmt_mounts(c.volume_mounts)
    return lines


def _fmt_conditions(conditions: list[Any] | None) -> list[str]:
    if not conditions:
        return ["<none>"]
    lines = [f"{_pad('Type', 20)}Status", f"{_pad('----', 20)}------"]
    for c in conditions:
        lines.append(f"{_pad(c.type, 20)}{c.status}")
    return lines


def _fmt_volume(v: Any) -> list[str]:
    lines = [f"{v.name}:"]
    if v.config_map:
        lines.append("  Type:      ConfigMap")
        lines.append(f"  Name:      {v.config_map.name}")
    elif v.secret:
        lines.append("  Type:      Secret")
        lines.append(f"  SecretName:  {v.secret.secret_name}")
    elif v.persistent_volume_claim:
        lines.append("  Type:      PersistentVolumeClaim")
        lines.append(f"  ClaimName:  {v.persistent_volume_claim.claim_name}")
    elif v.empty_dir:
        lines.append("  Type:      EmptyDir")
    elif v.host_path:
        lines.append("  Type:      HostPath")
        lines.append(f"  Path:      {v.host_path.path}")
    elif v.projected:
        lines.append("  Type:      Projected (a volume that contains injected data from multiple sources)")
    else:
        lines.append("  Type:      <other>")
    return lines


def _fmt_volumes(volumes: list[Any] | None) -> list[str]:
    if not volumes:
        return ["<none>"]
    lines: list[str] = []
    for i, v in enumerate(volumes):
        if i > 0:
            lines.append("")
        lines += _fmt_volume(v)
    return lines


def _fmt_tolerations(tolerations: list[Any] | None) -> str:
    if not tolerations:
        return "<none>"
    parts = []
    for t in tolerations:
        piece = f"{t.key or ''}"
        if t.operator:
            piece += f" {t.operator}"
        if t.value:
            piece += f" {t.value}"
        if t.effect:
            piece += f":{t.effect}"
        if t.toleration_seconds is not None:
            piece += f" for {t.toleration_seconds}s"
        parts.append(piece.strip())
    return "\n".join(f"{_pad('', _LABEL_WIDTH)}{p}" if i else p for i, p in enumerate(parts))


def _fmt_events(events: list[Any]) -> list[str]:
    if not events:
        return ["<none>"]

    def sort_key(e):
        ts = e.last_timestamp or e.first_timestamp or e.event_time
        return ts or ""

    ordered = sorted(events, key=sort_key)
    lines = [f"{_pad('Type', 8)}{_pad('Reason', 20)}{_pad('Age', 10)}{_pad('From', 20)}Message"]
    lines.append(f"{_pad('----', 8)}{_pad('------', 20)}{_pad('---', 10)}{_pad('----', 20)}-------")
    for e in ordered:
        ts = e.last_timestamp or e.first_timestamp or e.event_time
        age, _ = humanize_age(ts)
        count_suffix = f" (x{e.count})" if e.count and e.count > 1 else ""
        from_component = e.source.component if e.source else (e.reporting_component or "")
        lines.append(
            f"{_pad(e.type or '', 8)}{_pad(e.reason or '', 20)}{_pad(age + count_suffix, 10)}"
            f"{_pad(from_component, 20)}{e.message or ''}"
        )
    return lines


def _fmt_details(pod: Any) -> str:
    meta = pod.metadata
    spec = pod.spec
    status = pod.status or object()

    lines = [
        _kv("Name", meta.name),
        _kv("Namespace", meta.namespace),
    ]
    if spec.priority is not None:
        lines.append(_kv("Priority", spec.priority))
    if spec.priority_class_name:
        lines.append(_kv("Priority Class Name", spec.priority_class_name))
    node_line = spec.node_name or "<none>"
    if spec.node_name and getattr(status, "host_ip", None):
        node_line = f"{spec.node_name}/{status.host_ip}"
    lines.append(_kv("Node", node_line))
    lines.append(_kv("Start Time", getattr(status, "start_time", None)))
    lines += _fmt_multiline("Labels", _fmt_labels(meta.labels))
    lines += _fmt_multiline("Annotations", _fmt_annotations(meta.annotations))
    lines.append(_kv("Status", getattr(status, "phase", None)))
    lines.append(_kv("IP", getattr(status, "pod_ip", None)))
    pod_ips = getattr(status, "pod_i_ps", None) or []
    lines.append("IPs:")
    if pod_ips:
        for ip in pod_ips:
            lines.append(f"  IP:  {ip.ip}")
    else:
        lines.append("  <none>")
    lines.append(_kv("Controlled By", _fmt_controlled_by(meta.owner_references)))
    lines.append(_kv("QoS Class", getattr(status, "qos_class", None)))
    lines += _fmt_multiline("Node-Selectors", _fmt_labels(spec.node_selector) if spec.node_selector else ["<none>"])
    lines.append(_kv("Tolerations", _fmt_tolerations(spec.tolerations)))
    return "\n".join(lines)


def describe_pod(pod: Any, events: list[Any]) -> list[dict[str, str]]:
    spec = pod.spec
    status = pod.status or object()

    sections = [{"title": "Details", "text": _fmt_details(pod)}]

    init_containers = spec.init_containers or []
    if init_containers:
        init_statuses = {s.name: s for s in (getattr(status, "init_container_statuses", None) or [])}
        sections.append({
            "title": "Init Containers",
            "text": "\n".join(_fmt_container_group(init_containers, init_statuses)),
        })

    statuses = {s.name: s for s in (getattr(status, "container_statuses", None) or [])}
    container_lines = _fmt_container_group(spec.containers or [], statuses)
    sections.append({"title": "Containers", "text": "\n".join(container_lines) if container_lines else "<none>"})

    sections.append({"title": "Conditions", "text": "\n".join(_fmt_conditions(getattr(status, "conditions", None)))})
    sections.append({"title": "Volumes", "text": "\n".join(_fmt_volumes(spec.volumes))})
    sections.append({"title": "Events", "text": "\n".join(_fmt_events(events))})

    return sections
