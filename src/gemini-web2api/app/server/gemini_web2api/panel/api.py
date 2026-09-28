"""Panel JSON API endpoints."""
import json
import time

from ..config import CONFIG, EDITABLE_FIELDS, RESTART_FIELDS, save_config
from ..models import MODELS
from ..gemini import generate, log, load_cookie, reset_upstream_clients
from ..account import credential_info, save_cookie, clear_cookie, parse_cookie_text, validate_cookie
from .. import stats
from .. import logbuf
from .. import __version__


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _json_body(body: bytes) -> dict:
    if not body:
        return {}
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as e:
        raise ApiError(400, f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    return data


# ─── overview ────────────────────────────────────────────────────────────────


def overview() -> dict:
    info = credential_info()
    totals = stats.snapshot(0)["totals"]
    return {
        "version": __version__,
        "uptime_sec": round(time.time() - stats.START_TS, 1),
        "host": CONFIG.get("host"),
        "port": CONFIG.get("port"),
        "config_path": CONFIG.get("_path") or "",
        "default_model": CONFIG.get("default_model"),
        "model_count": len(MODELS),
        "api_key_enabled": bool(CONFIG.get("api_keys")),
        "api_key_count": len(CONFIG.get("api_keys") or []),
        "proxy_set": bool(CONFIG.get("proxy")),
        "temporary_chats": bool(CONFIG.get("temporary_chats")),
        "log_requests": bool(CONFIG.get("log_requests")),
        "retry": {
            "attempts": CONFIG.get("retry_attempts"),
            "delay_sec": CONFIG.get("retry_delay_sec"),
            "timeout_sec": CONFIG.get("request_timeout_sec"),
        },
        "cookie": {
            "configured": bool(info.get("configured")),
            "file": info.get("file") or "",
            "sapisid": bool(info.get("sapisid")),
            "auth_user": info.get("auth_user"),
            "mtime": info.get("mtime"),
        },
        "in_flight": stats.in_flight_count(),
        "totals": totals,
    }


# ─── models ──────────────────────────────────────────────────────────────────


def models() -> dict:
    probes = stats.probes_snapshot()
    out = []
    for name, cfg in MODELS.items():
        out.append({
            "id": name,
            "desc": cfg.get("desc", ""),
            "mode": cfg.get("mode"),
            "think": cfg.get("think"),
            "enhanced": bool(cfg.get("extra")),
            "is_default": name == CONFIG.get("default_model"),
            "probe": probes.get(name),
        })
    return {"models": out, "default_model": CONFIG.get("default_model")}


def models_probe(body: bytes) -> dict:
    req = _json_body(body)
    target = req.get("model")
    if target and target not in MODELS:
        raise ApiError(400, f"未知模型: {target}")
    names = [target] if target else list(MODELS.keys())
    results = []
    for name in names:
        cfg = MODELS[name]
        t0 = time.time()
        ok, err = True, None
        try:
            text = generate("ping", cfg["mode"], cfg["think"], None, cfg.get("extra"))
            if not (text or "").strip():
                ok, err = False, "空响应（上游未返回内容）"
        except Exception as e:  # noqa: BLE001 - probe reports, never raises
            ok, err = False, f"{type(e).__name__}: {e}"
        latency_ms = (time.time() - t0) * 1000.0
        stats.probe_result(name, ok, latency_ms, err)
        log(f"probe model={name} ok={ok} latency={int(latency_ms)}ms"
            + (f" error={err}" if err else ""), ch="task")
        results.append({
            "model": name,
            "ok": ok,
            "latency_ms": round(latency_ms, 1),
            "error": err,
            "tested_at": time.time(),
        })
    return {"results": results}


def models_default(body: bytes) -> dict:
    req = _json_body(body)
    model = req.get("model")
    if model not in MODELS:
        raise ApiError(400, f"未知模型: {model}")
    _, restart_required = save_config({"default_model": model})
    log(f"default_model -> {model}", ch="task")
    return {"ok": True, "default_model": model, "restart_required": restart_required}


# ─── credentials ─────────────────────────────────────────────────────────────


def credentials() -> dict:
    return credential_info()


def credentials_save(body: bytes) -> dict:
    req = _json_body(body)
    content = req.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ApiError(400, "缺少 cookie 内容（content）")
    kwargs = {}
    if "auth_user" in req:
        kwargs["auth_user"] = req["auth_user"]
    try:
        result = save_cookie(content, **kwargs)
    except ValueError as e:
        raise ApiError(400, str(e)) from e
    reset_upstream_clients()
    return {"ok": True, **result}


def credentials_validate(body: bytes) -> dict:
    req = _json_body(body)
    content = req.get("content")
    source = "candidate"
    if isinstance(content, str) and content.strip():
        try:
            parsed = parse_cookie_text(content)
        except ValueError as e:
            raise ApiError(400, str(e)) from e
        cookie_str, sapisid = parsed["cookie"], parsed["sapisid"]
    else:
        source = "current"
        cookie_str, sapisid = load_cookie()
        if not cookie_str:
            raise ApiError(400, "当前未配置 cookie，请先粘贴保存")
    log(f"credential validate ({source})", ch="task")
    result = validate_cookie(cookie_str, sapisid)
    result["source"] = source
    if result.get("ok"):
        log(f"credential validate ok ({source})"
            + (f" email={result['email']}" if result.get("email") else ""), ch="task")
    else:
        log(f"credential validate failed ({source}): {result.get('error')}", ch="task")
    return result


def credentials_clear() -> dict:
    result = clear_cookie()
    reset_upstream_clients()
    return {"ok": True, **result}


# ─── config ──────────────────────────────────────────────────────────────────

_HOT_RESET_FIELDS = {"proxy", "request_timeout_sec", "cookie_file", "retry_attempts", "retry_delay_sec"}


def config_get() -> dict:
    return {
        "config": {k: CONFIG.get(k) for k in EDITABLE_FIELDS},
        "path": CONFIG.get("_path") or "",
        "restart_fields": list(RESTART_FIELDS),
    }


def config_save(body: bytes) -> dict:
    req = _json_body(body)
    dm = req.get("default_model")
    if dm is not None and dm not in MODELS:
        raise ApiError(400, f"未知模型: {dm}")
    try:
        applied, restart_required = save_config(req)
    except ValueError as e:
        raise ApiError(400, str(e)) from e
    if set(applied) & _HOT_RESET_FIELDS:
        reset_upstream_clients()
    log("config saved: " + ", ".join(sorted(applied))
        + (f" (restart required: {', '.join(restart_required)})" if restart_required else ""), ch="task")
    return {
        "applied": sorted(applied),
        "restart_required": restart_required,
        "config": {k: CONFIG.get(k) for k in EDITABLE_FIELDS},
    }


# ─── logs / usage ────────────────────────────────────────────────────────────


def logs() -> dict:
    entries = []
    for e in logbuf.snapshot():
        entries.append({
            "ch": e["ch"],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(e["ts"])),
            "text": e["text"],
        })
    return {"entries": entries}


def usage(query: dict) -> dict:
    hours = 72
    raw = (query.get("hours") or ["72"])[0]
    try:
        hours = int(raw)
    except ValueError:
        pass
    if hours < 0:
        hours = 0
    return stats.snapshot(hours)


# ─── dispatch ────────────────────────────────────────────────────────────────

_GET_ROUTES = {
    "overview": lambda body, query: overview(),
    "models": lambda body, query: models(),
    "credentials": lambda body, query: credentials(),
    "config": lambda body, query: config_get(),
    "logs": lambda body, query: logs(),
    "usage": lambda body, query: usage(query),
}

_POST_ROUTES = {
    "models/probe": lambda body, query: models_probe(body),
    "models/default": lambda body, query: models_default(body),
    "credentials": lambda body, query: credentials_save(body),
    "credentials/validate": lambda body, query: credentials_validate(body),
    "credentials/clear": lambda body, query: credentials_clear(),
    "config": lambda body, query: config_save(body),
}


def dispatch(method: str, endpoint: str, body: bytes, query: dict) -> tuple:
    routes = _GET_ROUTES if method == "GET" else _POST_ROUTES if method == "POST" else {}
    handler = routes.get(endpoint)
    if handler is None:
        raise ApiError(404, f"unknown endpoint: {method} {endpoint}")
    return 200, handler(body, query)
