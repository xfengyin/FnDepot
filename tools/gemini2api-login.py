#!/usr/bin/env python3
"""用你自己的浏览器登录，把 Cookie 直接提交给 gemini2api。

为什么需要它：__Secure-1PSID 是 HttpOnly Cookie，网页里的 JavaScript 读不到，
所以「点一下就登录」不可能纯靠网页完成——要在有浏览器的那台机器上跑一段代码。
本脚本就是那段代码，只用标准库。

做法：用一份临时配置目录启动 Chrome/Edge/Chromium，并打开远程调试端口。
你在弹出的窗口里正常登录 Gemini，脚本通过 CDP（Chrome DevTools Protocol）
读取 Cookie（CDP 不受 HttpOnly 限制），再调 POST /admin/accounts 建账号。

用法：
    python3 gemini2api-login.py --base http://<NAS-LAN-IP>:5918 --key sk-gemini2api

    # 想指定浏览器 / 自建标签
    python3 gemini2api-login.py --base ... --key ... --browser /usr/bin/chromium
    python3 gemini2api-login.py --base ... --key ... --label 主账号

安全说明：临时配置目录用完即删，不会碰你日常浏览器的任何数据；脚本也不会
把 Cookie 打印到终端或日志。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PSID = "__Secure-1PSID"
PSIDTS = "__Secure-1PSIDTS"
LOGIN_URL = "https://gemini.google.com/app"

# 常见安装位置。用户也可以用 --browser 直接指路径。
CANDIDATES = {
    "Linux": [
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable",
        "/usr/bin/brave-browser", "/snap/bin/chromium",
    ],
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
}


def find_browser(explicit: str | None) -> str:
    if explicit:
        if not os.path.exists(explicit):
            sys.exit("找不到指定的浏览器：%s" % explicit)
        return explicit
    system = platform.system()
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
             "microsoft-edge", "microsoft-edge-stable", "brave-browser", "chrome", "msedge"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in CANDIDATES.get(system, []):
        if os.path.exists(path):
            return path
    sys.exit(
        "没找到 Chromium 系浏览器。用 --browser 指定可执行文件路径，"
        "或在有浏览器的机器上运行本脚本。"
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- 极简 WebSocket 客户端（CDP 只用到 文本帧；避免引入第三方库） ----------

class WebSocket:
    def __init__(self, url: str, timeout: float = 15.0):
        if not url.startswith("ws://"):
            raise ValueError("只支持 ws:// ：%s" % url)
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            "GET /%s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ) % (path, hostport, key)
        self.sock.sendall(handshake.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 握手被关闭")
            header += chunk
        status = header.split(b"\r\n", 1)[0].decode(errors="replace")
        if "101" not in status:
            raise ConnectionError("WebSocket 握手失败：%s" % status)
        self.buf = header.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("WebSocket 连接已关闭")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send_text(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])  # FIN + text
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str:
        while True:
            b0, b1 = self._read(2)
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else b""
            data = self._read(length)
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise ConnectionError("WebSocket 被对端关闭")
            if opcode == 0x9:  # ping -> pong
                self._send_control(0xA, data)
                continue
            if opcode in (0x1, 0x2):
                return data.decode(errors="replace")

    def _send_control(self, opcode: int, data: bytes) -> None:
        mask = os.urandom(4)
        frame = bytearray([0x80 | opcode, 0x80 | len(data)]) + mask
        frame += bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(frame))

    def close(self) -> None:
        try:
            self._send_control(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class CDP:
    """CDP 只需要「发一条命令、等它的回复」，replies 之外的异步事件直接跳过。"""

    def __init__(self, ws_url: str):
        self.ws = WebSocket(ws_url)
        self.next_id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0):
        self.next_id += 1
        msg_id = self.next_id
        self.ws.send_text(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
        raise TimeoutError("CDP 调用超时：%s" % method)

    def close(self) -> None:
        self.ws.close()


# --- 与 gemini2api 通信 ------------------------------------------------------

def http_json(url: str, payload: dict, api_key: str, method: str = "POST"):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode(errors="replace")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def collect_cookies(cdp: CDP):
    result = cdp.call("Storage.getCookies")
    values = {}
    for cookie in result.get("cookies", []):
        name = cookie.get("name")
        if name in (PSID, PSIDTS) and "google.com" in cookie.get("domain", ""):
            values[name] = cookie.get("value", "")
    return values


def main() -> int:
    ap = argparse.ArgumentParser(description="用本地浏览器登录并把 Cookie 提交给 gemini2api")
    ap.add_argument("--base", required=True, help="gemini2api 地址，例如 http://<NAS-LAN-IP>:5918")
    ap.add_argument("--key", required=True, help="API Key（若服务端设了 ADMIN_API_KEY 则用它）")
    ap.add_argument("--label", default="浏览器登录", help="账号备注")
    ap.add_argument("--browser", default=None, help="浏览器可执行文件路径")
    ap.add_argument("--timeout", type=int, default=300, help="等待登录的秒数，默认 300")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    browser = find_browser(args.browser)
    port = free_port()
    profile = tempfile.mkdtemp(prefix="g2a-login-")

    print("浏览器 : %s" % browser)
    print("中转站 : %s" % base)
    print()
    print("即将打开一个全新的浏览器窗口（独立的临时配置，不影响你日常浏览器的数据）。")
    print("请在里面登录 Google 账号并打开 Gemini。登录成功后脚本会自动继续。")
    print()

    proc = subprocess.Popen(
        [
            browser,
            "--user-data-dir=" + profile,
            "--remote-debugging-port=%d" % port,
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            LOGIN_URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    exit_code = 1
    try:
        ws_url = None
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                print("浏览器启动后立即退出，请确认 --browser 路径正确。", file=sys.stderr)
                return 1
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as resp:
                    ws_url = json.load(resp).get("webSocketDebuggerUrl")
                if ws_url:
                    break
            except Exception:
                time.sleep(0.5)
        if not ws_url:
            print("连不上浏览器的调试端口。", file=sys.stderr)
            return 1

        cdp = CDP(ws_url)
        print("等待登录 …（最多 %d 秒，Ctrl-C 可取消）" % args.timeout)
        last_state = None
        found = None
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                print("浏览器被关闭了。", file=sys.stderr)
                return 1
            values = collect_cookies(cdp)
            has_psid = bool(values.get(PSID))
            has_psidts = bool(values.get(PSIDTS))
            state = (has_psid, has_psidts)
            if state != last_state:
                print("  __Secure-1PSID %s / __Secure-1PSIDTS %s" % (
                    "已获取" if has_psid else "等待中",
                    "已获取" if has_psidts else "等待中",
                ))
                last_state = state
            if has_psid:
                found = values
                break
            time.sleep(1)

        if not found:
            print("超时未拿到 Cookie。", file=sys.stderr)
            return 1

        print()
        print("已拿到 Cookie，正在提交到 %s …" % base)
        status, raw = http_json(
            base + "/admin/accounts",
            {"psid": found.get(PSID, ""), "psidts": found.get(PSIDTS, ""), "label": args.label},
            args.key,
        )
        if 200 <= status < 300:
            print("成功：账号已添加。")
            exit_code = 0
        else:
            print("提交失败（HTTP %s）：%s" % (status, raw[:400]), file=sys.stderr)
            exit_code = 1
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
