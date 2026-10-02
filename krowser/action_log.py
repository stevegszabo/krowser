from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

# A plain in-memory ring buffer, not a database -- krowser is a single local
# server process, so this is enough to answer "what has krowser itself done
# to the cluster this session", and resetting on restart is the right
# behavior for that question, not a limitation to work around.
_MAX_ENTRIES = 50

_log: deque["ActionLogEntry"] = deque(maxlen=_MAX_ENTRIES)


@dataclass
class ActionLogEntry:
    timestamp: str
    action: str
    kind: str
    namespace: str
    name: str
    context: str | None
    success: bool
    detail: str


def record(
    action: str,
    kind: str,
    namespace: str,
    name: str,
    context: str | None,
    success: bool,
    detail: str,
) -> None:
    _log.appendleft(
        ActionLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            kind=kind,
            namespace=namespace,
            name=name,
            context=context,
            success=success,
            detail=detail,
        )
    )


def recent() -> list[dict]:
    """Newest first -- record() always appendlefts, so the deque's own
    iteration order already is that; evicting the oldest entry past
    _MAX_ENTRIES happens automatically off the opposite (right) end."""
    return [asdict(entry) for entry in _log]
