"""Configuration management."""
import json
import os

DEFAULT_CONFIG = {
    "port": 8081,
    "host": "0.0.0.0",
    "retry_attempts": 3,
    "retry_delay_sec": 2,
    "request_timeout_sec": 180,
    "gemini_bl": "boq_assistant-bard-web-server_20260716.08_p0",
    "auth_user": None,
    "xsrf_token": None,
    "default_model": "gemini-3.6-flash",
    "log_requests": True,
    "cookie_file": None,
    "proxy": None,
    "api_keys": [],
    "temporary_chats": False,
}

# Keys the management panel is allowed to write back.
EDITABLE_FIELDS = (
    "port", "host", "default_model", "api_keys", "cookie_file", "proxy",
    "retry_attempts", "retry_delay_sec", "request_timeout_sec",
    "gemini_bl", "auth_user", "xsrf_token", "log_requests", "temporary_chats",
)

# Changing these requires a process restart to take effect.
RESTART_FIELDS = ("port", "host")

CONFIG = dict(DEFAULT_CONFIG)
CONFIG["_path"] = None


def load_config(path: str = None):
    """Load config from JSON file. The path is remembered for later saves."""
    if path:
        CONFIG["_path"] = os.path.abspath(path)
        if os.path.exists(path):
            with open(path) as f:
                CONFIG.update(json.load(f))
    return CONFIG


def find_config():
    """Search for config file in standard locations."""
    for p in ["./config.json", os.path.expanduser("~/.config/gemini-web2api/config.json")]:
        if os.path.exists(p):
            return p
    return None


def default_config_path() -> str:
    return os.path.expanduser("~/.config/gemini-web2api/config.json")


def _as_bool(v):
    if isinstance(v, bool):
        return v
    raise ValueError("必须是布尔值")


def _as_int(lo, hi):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("必须是整数")
        v = int(v)
        if v < lo or v > hi:
            raise ValueError(f"取值需在 {lo} ~ {hi} 之间")
        return v
    return check


def _as_number(lo, hi):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("必须是数字")
        v = float(v)
        if v < lo or v > hi:
            raise ValueError(f"取值需在 {lo} ~ {hi} 之间")
        return v
    return check


def _as_str(allow_empty=False, allow_null=False):
    def check(v):
        if v is None:
            if allow_null:
                return None
            raise ValueError("不能为空")
        if not isinstance(v, str):
            raise ValueError("必须是字符串")
        v = v.strip()
        if not v:
            if allow_null:
                return None
            if allow_empty:
                return ""
            raise ValueError("不能为空")
        return v
    return check


def _as_int_or_null(lo, hi):
    inner = _as_int(lo, hi)

    def check(v):
        if v is None or v == "":
            return None
        return inner(v)
    return check


def _as_key_list(v):
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError("必须是字符串列表")
    out = []
    for item in v:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("列表项必须是非空字符串")
        out.append(item.strip())
    return out


_VALIDATORS = {
    "port": _as_int(1, 65535),
    "host": _as_str(),
    "default_model": _as_str(),
    "api_keys": _as_key_list,
    "cookie_file": _as_str(allow_null=True),
    "proxy": _as_str(allow_null=True),
    "retry_attempts": _as_int(1, 20),
    "retry_delay_sec": _as_number(0, 60),
    "request_timeout_sec": _as_int(1, 3600),
    "gemini_bl": _as_str(),
    "auth_user": _as_int_or_null(0, 99),
    "xsrf_token": _as_str(allow_null=True),
    "log_requests": _as_bool,
    "temporary_chats": _as_bool,
}


def _write_config_file(applied: dict):
    """Read-modify-write the config file atomically, preserving unknown keys."""
    path = CONFIG.get("_path") or default_config_path()
    base = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                base.update(loaded)
        except (json.JSONDecodeError, OSError):
            base = {}
    base.update(applied)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    CONFIG["_path"] = path


def save_config(patch: dict) -> tuple:
    """Validate and apply a config patch.

    Returns (applied, restart_required). Raises ValueError on invalid input.
    """
    if not isinstance(patch, dict) or not patch:
        raise ValueError("空的配置变更")
    applied = {}
    for key, value in patch.items():
        if key not in EDITABLE_FIELDS:
            raise ValueError(f"不可修改的配置项: {key}")
        validator = _VALIDATORS[key]
        try:
            applied[key] = validator(value)
        except ValueError as e:
            raise ValueError(f"{key}: {e}") from e
    CONFIG.update(applied)
    _write_config_file(applied)
    restart_required = sorted(k for k in applied if k in RESTART_FIELDS)
    return applied, restart_required
