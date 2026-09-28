"""Thread-safe in-memory log ring buffer with channels (for the /panel console)."""
import threading
import time
from collections import deque

CHANNELS = ("sys", "chat", "task")
_MAX_ENTRIES = 800

_lock = threading.Lock()
_entries = deque(maxlen=_MAX_ENTRIES)


def add(channel: str, text: str, ts: float = None):
    """Append one log line. Unknown channels fall back to 'sys'."""
    if channel not in CHANNELS:
        channel = "sys"
    line = (text or "").rstrip("\n")
    if not line:
        return
    if ts is None:
        ts = time.time()
    with _lock:
        _entries.append({"ch": channel, "ts": ts, "text": line})


def snapshot() -> list:
    """Return a copy of all buffered entries (oldest first)."""
    with _lock:
        return [dict(e) for e in _entries]


def clear():
    with _lock:
        _entries.clear()
