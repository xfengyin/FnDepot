## 环境 / Environment

- 版本：**v1.6.40**（ghcr.io/xwteam/gemini2api:1.6.40，latest release）
- 部署：飞牛 fnOS 上的 Docker 应用，主服务通过 `HTTPS_PROXY` 出网
- 账号：1 个，通过面板正常添加（psid / psidts 均存在，页面/API 校验通过）

## 现象 / Symptom

**账号健康、自检通过，但所有模型、所有协议都返回 200 + 空内容。**

`GET /admin/accounts/account-0/check` —— 一切正常：

```json
{"valid":true,"has_token":true,"models_count":4,"status":"active"}
```

`GET /admin/status`：

```
is_healthy=true  consecutive_failures=0  consecutive_empty=25
last_error: "Empty response x25 (upstream 200, no content)"
```

`POST /v1/chat/completions`（非流式）：

```json
{"choices":[{"index":0,"message":{"role":"assistant","content":""},"finish_reason":"stop"}],
 "usage":{"prompt_tokens":7,"completion_tokens":0,"total_tokens":7}}
```

`POST /v1beta/models/gemini-flash:generateContent`：

```json
{"candidates":[{"content":{"parts":[{"text":""}],"role":"model"},"finishReason":"STOP","index":0}],
 "usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":0,"totalTokenCount":3}}
```

流式（`stream:true`）：只有空的 delta 分片 + `[DONE]`，**没有 error 帧**。

## 已排除的 / Ruled out

| 怀疑点 | 证据 |
| --- | --- |
| 账号会话失效 | 用该账号的 cookie 直接请求 `https://gemini.google.com/app`：HTTP **200**，769KB，`SNlM0e` / `cfb2h` / `FdrFJe` 均在，页面内**含该账号邮箱**，且**无** "Gemini isn't currently supported in your country" |
| 模型特定 | 四个模型全空；另外用 `gemini-2.5-flash` 请求也空 |
| 协议特定 | OpenAI 与 generateContent 表现一致 |
| 代理链路 | `www.google.com` / `accounts.google.com` / `gemini.google.com` 从该容器内实测**全部 HTTP 200** |
| cookie 轮换没起来 | 已调 `POST /admin/reload-cookies` → `{"status":"ok","message":"Cookies reloaded successfully","healthy":true}`，且日志出现 `Auto-refresh loop started`；之后仍然全空 |

## 一个可能的线索 / A possible lead

本账号解析出来的模型是 **Gemini 3**：

```
app.core.gemini_client: Account models resolved:
  {'flash-lite': 'gemini-3-flash-lite', 'flash': 'gemini-3-flash',
   'pro': 'gemini-3-pro', 'flash-thinking': 'gemini-3-flash-thinking'}
  -> public ['gemini-pro', 'gemini-flash', 'gemini-flash-thinking', 'gemini-flash-lite']
```

**想请教：v1.6.40 对 Gemini 3 的响应形态是否已经完全处理？** 如果 `f.req` / 响应解析仍是按 Gemini 2.x 写的，
会不会正好表现成"HTTP 200 但解析不出正文"？

这条能解释为什么 v1.6.40 的发布说明里那位报告人（ACTIVE / Errors 0 / Models 4，聊天框只有「无响应内容」）
和我的现象完全一致 —— 如果那位的账号同样被解析到 Gemini 3 的话。

## 补充信息 / Extra

- 面板的模型测试页（`/#playground`）在这个状态下显示的是什么？（我这边看到的是空内容；
  按 v1.6.40 的说明它应该会显示 `网关错误 (5xx): <真实原因>`，但没有出现 error 帧）
- 若需要，我可以提供 `/admin/logs` 或带上 `LOG_LEVEL=debug` 的完整请求/响应（会先脱敏凭据）。

谢谢！这个项目整体非常好用，这条是唯一挡住我的问题。
