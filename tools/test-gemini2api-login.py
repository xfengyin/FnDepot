#!/usr/bin/env python3
"""交叉验证 gemini2api-login.py 的 WebSocket / CDP 部分。

本机没有浏览器，所以这里自己写一个 mock CDP 服务端：握手、帧解析、回复
全部用独立的实现（不复用被测代码），确保验的是真的协议而不是自己骗自己。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util

spec = importlib.util.spec_from_file_location(
    "g2alogin", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini2api-login.py"))
g2alogin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g2alogin)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def read_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("eof")
        buf += chunk
    return buf


def read_frame(conn):
    """服务端读客户端帧：客户端发来的帧一定是 masked 的。"""
    b0, b1 = read_exact(conn, 2)
    opcode = b0 & 0x0F
    masked = b1 & 0x80
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", read_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", read_exact(conn, 8))[0]
    assert masked, "客户端帧必须 masked"
    mask = read_exact(conn, 4)
    data = read_exact(conn, length)
    return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def send_text(conn, text):
    """服务端发帧：不加 mask。"""
    payload = text.encode()
    header = bytearray([0x81])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < (1 << 16):
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    conn.sendall(bytes(header) + payload)


COOKIES = [
    {"name": "__Secure-1PSID", "value": "PSID-VALUE-123", "domain": ".google.com", "path": "/"},
    {"name": "__Secure-1PSIDTS", "value": "PSIDTS-VALUE-456", "domain": ".google.com", "path": "/"},
    {"name": "SID", "value": "irrelevant", "domain": ".google.com", "path": "/"},
    {"name": "__Secure-1PSID", "value": "other-domain", "domain": ".example.com", "path": "/"},
]
seen_methods = []


def server(sock):
    conn, _ = sock.accept()
    # HTTP 握手
    req = b""
    while b"\r\n\r\n" not in req:
        req += conn.recv(4096)
    headers = {}
    for line in req.decode(errors="replace").split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    key = headers["sec-websocket-key"]
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    conn.sendall((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
    ).encode())
    # 先发一条异步事件，验证客户端会跳过没有 id 的帧
    send_text(conn, json.dumps({"method": "Target.targetCreated", "params": {}}))
    while True:
        try:
            opcode, data = read_frame(conn)
        except Exception:
            return
        if opcode == 0x8:
            return
        if opcode == 0x9:
            continue
        msg = json.loads(data)
        seen_methods.append(msg["method"])
        if msg["method"] == "Storage.getCookies":
            send_text(conn, json.dumps({"id": msg["id"], "result": {"cookies": COOKIES}}))
        elif msg["method"] == "Boom":
            send_text(conn, json.dumps({"id": msg["id"], "error": {"message": "nope"}}))
        else:
            send_text(conn, json.dumps({"id": msg["id"], "result": {"ok": True}}))
    return


def start_server():
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    t = threading.Thread(target=server, args=(sock,), daemon=True)
    t.start()

    # 顺带起一个 HTTP 端点，验证 /json/version 的解析
    http = socket.socket()
    http.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    http.bind(("127.0.0.1", 0))
    http.listen(1)
    hport = http.getsockname()[1]

    def http_server():
        c, _ = http.accept()
        c.recv(4096)
        body = json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1:%d/devtools/browser/x" % port}).encode()
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                  + str(len(body)).encode() + b"\r\n\r\n" + body)
        c.close()

    threading.Thread(target=http_server, daemon=True).start()
    return port, hport


def main():
    port, hport = start_server()

    with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % hport, timeout=5) as resp:
        ws_url = json.load(resp)["webSocketDebuggerUrl"]
    assert str(port) in ws_url, ws_url
    print("✓ /json/version 解析正确")

    cdp = g2alogin.CDP(ws_url)
    print("✓ WebSocket 握手成功（含 Sec-WebSocket-Accept 校验）")

    # 先发一条普通命令，验证能跳过前面那条异步事件
    res = cdp.call("Runtime.evaluate")
    assert res == {"ok": True}, res
    print("✓ 能把异步事件跳过、正确匹配 id")

    values = g2alogin.collect_cookies(cdp)
    assert values.get("__Secure-1PSID") == "PSID-VALUE-123", values
    assert values.get("__Secure-1PSIDTS") == "PSIDTS-VALUE-456", values
    print("✓ 取到了两个 __Secure- Cookie")

    # 关键：同名的 .example.com 与不相关的 SID 都不能混进来
    assert "SID" not in values, values
    assert values["__Secure-1PSID"] != "other-domain", values
    print("✓ 域名过滤正确（.example.com 的同名 cookie 与 SID 都被排除）")

    # 再发一条大 payload 的命令，走 16 位长度分支
    cdp.next_id += 1
    big = "x" * 300
    cdp.ws.send_text(json.dumps({"id": cdp.next_id, "method": "Runtime.evaluate",
                                 "params": {"expression": big}}))
    assert cdp.call("Runtime.evaluate") == {"ok": True}
    print("✓ 大 payload（>125 字节，走 16 位长度分支）正确")

    try:
        cdp.call("Boom")
        raise AssertionError("错误响应应该抛异常")
    except RuntimeError as e:
        assert "nope" in str(e), e
    print("✓ CDP error 正确抛出")

    assert "Storage.getCookies" in seen_methods, seen_methods
    print("✓ 服务端确实收到了 Storage.getCookies")

    cdp.close()
    print()
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
