"""Usage statistics (requests / tokens / latency / probes) with hourly buckets.

State lives in memory and is persisted to ``usage.json`` next to the config
file (debounced write, at most once per 30s; flushed on shutdown).  Token
counts use the same len(text)//4 estimate as the API responses themselves.
"""
import json
import os
import threading
import time

from .config import CONFIG

START_TS = time.time()

_FLUSH_INTERVAL = 30  # seconds
_KEEP_HOURS = 24 * 35  # prune buckets older than ~35 days

_lock = threading.RLock()
_path = None
_dirty = False
_last_flush = 0.0
_in_flight = 0

_state = {
    "since": None,
    "totals": None,
    "models": {},
    "hours": {},
    "probes": {},
}


def _new_counter() -> dict:
    return {
        "requests": 0,
        "errors": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms_sum": 0.0,
        "latency_count": 0,
    }


_STATE_VERSION = 1


def _default_state() -> dict:
    return {
        "version": _STATE_VERSION,
        "since": time.time(),
        "totals": _new_counter(),
        "models": {},
        "hours": {},
        "probes": {},
    }


def state_dir() -> str:
    """Directory used for runtime state files (next to the config file)."""
    cfg_path = CONFIG.get("_path")
    if cfg_path:
        return os.path.dirname(os.path.abspath(cfg_path))
    return os.path.expanduser("~/.config/gemini-web2api")


def init(path: str = None):
    """Load persisted state. ``path`` defaults to <state_dir>/usage.json."""
    global _path, _state, _dirty
    with _lock:
        _path = path or os.path.join(state_dir(), "usage.json")
        loaded = None
        try:
            if os.path.exists(_path):
                with open(_path, "r") as f:
                    loaded = json.load(f)
        except Exception:
            loaded = None
        base = _default_state()
        if isinstance(loaded, dict):
            for key in ("since", "totals", "models", "hours", "probes"):
                if isinstance(loaded.get(key), type(base[key])) or loaded.get(key) is None:
                    if loaded.get(key) is not None:
                        base[key] = loaded[key]
            if not isinstance(base["totals"], dict):
                base["totals"] = _new_counter()
        _state = base
        _prune_locked()
        _dirty = False


def _prune_locked():
    """Drop hourly buckets older than _KEEP_HOURS."""
    hours = _state.get("hours")
    if not isinstance(hours, dict):
        _state["hours"] = {}
        return
    cutoff = time.time() - _KEEP_HOURS * 3600
    for key in [k for k in hours]:
        try:
            t = time.mktime(time.strptime(key, "%Y-%m-%dT%H"))
        except ValueError:
            del hours[key]
            continue
        if t < cutoff:
            del hours[key]


def _bucket_key(ts: float = None) -> str:
    return time.strftime("%Y-%m-%dT%H", time.localtime(ts or time.time()))


def record(model: str, prompt_chars: int, completion_chars: int, latency_ms: float, ok: bool = True):
    """Register one finished API request."""
    global _dirty
    model = model or "unknown"
    p_tok = max(0, int(prompt_chars // 4))
    c_tok = max(0, int(completion_chars // 4))
    with _lock:
        _ensure_totals()
        totals = _state["totals"]
        per_model = _state["models"].setdefault(model, _new_counter())
        bucket = _state["hours"].setdefault(_bucket_key(), _new_counter())
        for counter in (totals, per_model, bucket):
            counter["requests"] += 1
            if not ok:
                counter["errors"] += 1
        if ok:
            totals["prompt_tokens"] += p_tok
            totals["completion_tokens"] += c_tok
            per_model["prompt_tokens"] += p_tok
            per_model["completion_tokens"] += c_tok
            bucket["prompt_tokens"] += p_tok
            bucket["completion_tokens"] += c_tok
            for counter in (totals, per_model):
                counter["latency_ms_sum"] += max(0.0, float(latency_ms))
                counter["latency_count"] += 1
        if _state["since"] is None:
            _state["since"] = time.time()
        _dirty = True
    flush()


def _ensure_totals():
    if not isinstance(_state.get("totals"), dict):
        _state["totals"] = _new_counter()


def in_flight(delta: int):
    global _in_flight
    with _lock:
        _in_flight = max(0, _in_flight + delta)


def in_flight_count() -> int:
    with _lock:
        return _in_flight


def probe_result(model: str, ok: bool, latency_ms: float, error: str = None):
    global _dirty
    with _lock:
        _state["probes"][model] = {
            "ok": bool(ok),
            "latency_ms": round(float(latency_ms), 1),
            "tested_at": time.time(),
            "error": (error or "")[:300] or None,
        }
        _dirty = True
    flush()


def probes_snapshot() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _state["probes"].items()}


def _counter_out(c: dict) -> dict:
    latency_count = c.get("latency_count", 0) or 0
    prompt_tokens = c.get("prompt_tokens", 0) or 0
    completion_tokens = c.get("completion_tokens", 0) or 0
    return {
        "requests": c.get("requests", 0),
        "errors": c.get("errors", 0),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "avg_latency_ms": round(c.get("latency_ms_sum", 0.0) / latency_count, 1) if latency_count else None,
    }


def snapshot(hours: int = 72) -> dict:
    """Aggregate stats for the panel. hours<=0 means all history."""
    with _lock:
        _ensure_totals()
        now = time.time()
        series = []
        totals_window = _new_counter()
        for key in sorted(_state["hours"]):
            bucket = _state["hours"][key]
            try:
                t = time.mktime(time.strptime(key, "%Y-%m-%dT%H"))
            except ValueError:
                continue
            if hours > 0 and t < now - hours * 3600:
                continue
            series.append({
                "t": key,
                "requests": bucket.get("requests", 0),
                "errors": bucket.get("errors", 0),
                "prompt_tokens": bucket.get("prompt_tokens", 0),
                "completion_tokens": bucket.get("completion_tokens", 0),
                "total_tokens": (bucket.get("prompt_tokens", 0) or 0) + (bucket.get("completion_tokens", 0) or 0),
            })
            totals_window["requests"] += bucket.get("requests", 0)
            totals_window["errors"] += bucket.get("errors", 0)
            totals_window["prompt_tokens"] += bucket.get("prompt_tokens", 0)
            totals_window["completion_tokens"] += bucket.get("completion_tokens", 0)
        by_model = []
        for name, counter in sorted(_state["models"].items()):
            item = _counter_out(counter)
            item["model"] = name
            by_model.append(item)
        by_model.sort(key=lambda x: x["requests"], reverse=True)
        probes = {k: dict(v) for k, v in _state["probes"].items()}
        out = {
            "since": _state.get("since"),
            "totals": _counter_out(_state["totals"]),
            "window_totals": _counter_out(totals_window),
            "by_model": by_model,
            "series": series,
            "buckets": len(series),
            "hours": hours,
            "probes": probes,
            "state_file": _path or "",
            "file_bytes": os.path.getsize(_path) if _path and os.path.exists(_path) else 0,
        }
        return out


def flush(force: bool = False):
    """Persist state to disk (debounced unless force=True)."""
    global _dirty, _last_flush
    with _lock:
        if _path is None:
            return
        now = time.time()
        if not _dirty and not force:
            return
        if not force and now - _last_flush < _FLUSH_INTERVAL:
            return
        payload = json.dumps(_state, ensure_ascii=False)
        tmp = _path + ".tmp"
        try:
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            with open(tmp, "w") as f:
                f.write(payload)
            os.replace(tmp, _path)
            _dirty = False
            _last_flush = now
        except Exception:
            # Never let stats persistence break request handling.
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
