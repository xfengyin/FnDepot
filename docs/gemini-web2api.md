# Gemini Web2API

把 Google Gemini 网页端转换成 **OpenAI 兼容 API**，跑在飞牛 fnOS 上。
本项目是 [Sophomoresty/gemini-web2api](https://github.com/Sophomoresty/gemini-web2api) 的 fnOS 打包版。

## 特性

- **零成本**：直接用 Gemini 网页端，不需要 Google Cloud API Key。
- **OpenAI 兼容**：`/v1/chat/completions`、`/v1/models`，可直接接入 Cherry Studio、ChatBox、NextChat 等客户端。
- **Codex CLI / Gemini CLI**：额外兼容 `/v1/responses` 与 `/v1beta/models`。
- **流式输出**：包内已内置 `httpx`，安装后即为真正的 SSE 流式。
- **函数调用**：完整 Function Calling 支持。
- **多模型**：Flash / Pro / Auto / Lite / Thinking，通过 `@think=N` 后缀调整思考深度。
- **可选 Cookie**：填入 Gemini 账号 Cookie 后使用登录态，配额更高。

## 安装后怎么用

1. 在应用中心启动应用（安装后会自动启动）。
2. 在客户端里填写：

   | 字段 | 值 |
   | --- | --- |
   | Base URL | `http://<NAS 地址>:8081/v1` |
   | API Key | `sk-gemini`（默认值，可改） |
   | 模型 | 打开 `http://<NAS 地址>:8081/v1/models` 查看 |

3. 浏览器访问 `http://<NAS 地址>:8081/` 可以确认服务状态。

## 配置

配置文件路径：`/vol1/@appdata/gemini-web2api/config.json`（卷号按实际安装位置）。

```json
{
  "port": 8081,
  "host": "0.0.0.0",
  "api_keys": ["sk-gemini"],
  "cookie_file": null,
  "proxy": null,
  "default_model": "gemini-3.6-flash",
  "log_requests": true,
  "temporary_chats": false
}
```

- `api_keys` 改成 `[]` 即可免密钥访问。
- `cookie_file` 指向一个存放 Gemini Cookie 的文件，用于登录态请求。
- `proxy` 用于在无法直连 Google 的网络里指定代理。

修改后重启应用生效。

## 常见问题

**调用时返回 200 但 content 是 null / 客户端显示「没有回复」**

说明这台机器到 Google 的链路被 Google 拒绝了，最常见的是 `BardErrorInfo [1060]`：

- 先确认能访问 Google：`curl -x <代理> -o /dev/null -w "%{http_code}" https://gemini.google.com/app` 应该是 200。
- 直连大陆网络一定失败，必须在 `config.json` 里填 `proxy`。
- 用 VPS / 云主机这类机房 IP 做出口时，Google 会随机对匿名请求返回 1060（约六成）。**1.1.1 起会自动重试**，基本能压下去；想要完全稳定就配 `cookie_file` 用 Gemini 账号 Cookie。

**Pro 模型实际返回 Flash 的内容**

`gemini-3.1-pro` 在无 Cookie 时会路由到 Flash，属于上游行为，需要 Gemini Advanced 账号的 Cookie 才会真正走 Pro。

## 应用信息

| 项目 | 值 |
| --- | --- |
| 应用名 | `gemini-web2api` |
| 版本 | 1.1.0 |
| 架构 | all（x86 / arm 通用） |
| 端口 | 8081 |
| 运行用户 | `gemini-web2api`（package 模式） |
| 数据目录 | `TRIM_PKGVAR`（`/vol?/@appdata/gemini-web2api`） |
| 上游 | https://github.com/Sophomoresty/gemini-web2api |

## 许可

MIT，随包附带上游 `LICENSE`。
