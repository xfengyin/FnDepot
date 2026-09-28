"""Credential helpers: cookie parsing, storage preview and login validation."""
import json
import os
import re
import time
from urllib.parse import urlparse

from .config import CONFIG, save_config
from .gemini import log
from .multimodal import fetch_gemini_page, parse_wiz_tokens

_UNSET = object()


def _cookie_pairs(cookie_str: str) -> dict:
    pairs = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            name, value = part.split("=", 1)
            pairs[name.strip()] = value
    return pairs


def parse_cookie_text(text: str) -> dict:
    """Parse pasted cookie content.

    Accepts a raw ``k=v; k2=v2`` string or JSON in the common exported forms:
    ``{"cookie": "...", "sapisid": "..."}``, ``{"cookies": [{"name","value"}...]}``
    or a bare array of ``{"name","value"}`` items.

    Returns ``{"cookie", "sapisid", "names"}``. Raises ValueError when nothing
    usable is found.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("内容为空")
    cookie_str = ""
    sapisid = None

    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}") from e
        if isinstance(data, dict):
            if isinstance(data.get("cookie"), str):
                cookie_str = data["cookie"].strip()
            if isinstance(data.get("sapisid"), str):
                sapisid = data["sapisid"].strip() or None
            if not cookie_str and isinstance(data.get("cookies"), list):
                data = data["cookies"]
        if isinstance(data, list):
            parts = []
            for item in data:
                if isinstance(item, dict) and item.get("name"):
                    parts.append(f"{item['name']}={item.get('value', '')}")
            cookie_str = "; ".join(parts)
    else:
        cookie_str = text

    cookie_str = cookie_str.strip()
    if not cookie_str or "=" not in cookie_str:
        raise ValueError("未找到可用的 cookie（需要 k=v; k2=v2 形式的内容）")

    pairs = _cookie_pairs(cookie_str)
    if sapisid is None:
        sapisid = (pairs.get("SAPISID") or pairs.get("__Secure-3PAPISID") or "").strip() or None
    # 规范化：去掉换行，保留分号分隔
    cookie_str = "; ".join(f"{k}={v}" for k, v in pairs.items()) if pairs else cookie_str
    return {
        "cookie": cookie_str,
        "sapisid": sapisid,
        "names": list(pairs.keys()),
    }


def cookie_preview(cookie_str: str) -> list:
    """Cookie 名称列表（不包含值），供面板展示。"""
    return list(_cookie_pairs(cookie_str).keys())


def credential_info() -> dict:
    """Current credential state for GET /panel/api/credentials."""
    cookie_file = CONFIG.get("cookie_file") or ""
    info = {
        "configured": False,
        "file": cookie_file,
        "auth_user": CONFIG.get("auth_user"),
        "xsrf_token_set": bool(CONFIG.get("xsrf_token")),
        "sapisid": False,
        "size": 0,
        "mtime": None,
        "names": [],
        "default_file": os.path.join(_state_dir(), "cookie.txt"),
    }
    if not cookie_file or not os.path.exists(cookie_file):
        return info
    try:
        info["size"] = os.path.getsize(cookie_file)
        info["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(cookie_file)))
        with open(cookie_file, "r") as f:
            raw = f.read().strip()
        parsed = parse_cookie_text(raw)
        info["configured"] = True
        info["sapisid"] = bool(parsed["sapisid"])
        info["names"] = parsed["names"]
    except Exception as e:
        info["error"] = str(e)
    return info


def _state_dir() -> str:
    cfg_path = CONFIG.get("_path")
    if cfg_path:
        return os.path.dirname(os.path.abspath(cfg_path))
    return os.path.expanduser("~/.config/gemini-web2api")


def validate_cookie(cookie_str: str, sapisid: str = None, timeout: int = 20) -> dict:
    """Probe gemini.google.com with a candidate cookie and report login state."""
    result = {
        "ok": False,
        "logged_in": False,
        "final_url": "",
        "status": None,
        "email": None,
        "tokens": {},
        "error": None,
    }
    if not cookie_str:
        result["error"] = "没有可校验的 cookie"
        return result
    try:
        final_url, status, html = fetch_gemini_page(cookie_str, sapisid, timeout=timeout)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    final_url = final_url or ""
    result["final_url"] = final_url
    result["status"] = status
    result["tokens"] = parse_wiz_tokens(html)
    host = urlparse(final_url).netloc.lower()
    signed_out = "accounts.google.com" in final_url.lower() or "/signin" in final_url.lower()
    has_app_tokens = "qKIAYe" in html
    result["logged_in"] = (not signed_out) and has_app_tokens and host.endswith("gemini.google.com")
    for pattern in (r'"oPEP7c":"([^"@\s]+@[^"\s]+)"', r'"W3Yyqf":"([^"@\s]+@[^"\s]+)"'):
        m = re.search(pattern, html)
        if m:
            result["email"] = m.group(1)
            break
    result["ok"] = bool(result["logged_in"])
    if not result["logged_in"] and not result["error"]:
        if signed_out:
            result["error"] = "会话未登录（请求被重定向到 Google 登录页）"
        elif not has_app_tokens:
            result["error"] = "页面未返回 Gemini 应用数据，cookie 可能无效或已过期"
        else:
            result["error"] = "未能确认登录状态"
    return result


def save_cookie(content: str, auth_user=_UNSET) -> dict:
    """Parse and persist pasted cookie content; update config accordingly."""
    parsed = parse_cookie_text(content)
    target = CONFIG.get("cookie_file") or os.path.join(_state_dir(), "cookie.txt")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        f.write(parsed["cookie"] + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    patch = {"cookie_file": target}
    if auth_user is not _UNSET:
        patch["auth_user"] = auth_user
    save_config(patch)
    log(f"Cookie saved to {target} ({len(parsed['names'])} entries, sapisid={'yes' if parsed['sapisid'] else 'no'})", ch="task")
    return {"file": target, "names": parsed["names"], "sapisid": bool(parsed["sapisid"])}


def clear_cookie() -> dict:
    """Delete the configured cookie file (keep the path in config)."""
    cookie_file = CONFIG.get("cookie_file") or ""
    removed = False
    if cookie_file and os.path.exists(cookie_file):
        os.remove(cookie_file)
        removed = True
    log(f"Cookie cleared ({cookie_file or 'not configured'})", ch="task")
    return {"removed": removed, "file": cookie_file}
