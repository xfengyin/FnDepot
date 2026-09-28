# gemini-web2api

把 Google Gemini 网页端转换成 **OpenAI 兼容 API**，跑在飞牛 fnOS 上，并内置 Web 控制台。
本项目基于 [Sophomoresty/gemini-web2api](https://github.com/Sophomoresty/gemini-web2api)（MIT）封装与增强。

## 特性

- **零成本**：直接用 Gemini 网页端，不需要 Google Cloud API Key。
- **OpenAI 兼容**：`/v1/chat/completions`、`/v1/models`，可直接接入 Cherry Studio、ChatBox、NextChat 等客户端。
- **Codex CLI / Gemini CLI**：额外兼容 `/v1/responses` 与 `/v1beta/models`。
- **流式输出**：包内已内置 `httpx`，安装后即为真正的 SSE 流式。
- **函数调用**：完整 Function Calling 支持。
- **多模型**：Flash / Pro / Auto / Lite / Thinking，通过 `@think=N` 后缀调整思考深度。
- **Web 控制台**：凭证管理、模型探测、用量统计、配置编辑、运行日志，支持深浅色主题。
- **可选 Cookie**：填入 Gemini 账号 Cookie 后使用登录态，配额更高、更稳定。

## 安装后怎么用

1. 在应用中心启动应用（安装后会自动启动）。
2. 客户端接入：

   | 字段 | 值 |
   | --- | --- |
   | Base URL | `http://<NAS 地址>:8081/v1` |
   | API Key | `sk-gemini`（默认值；面板与接口共用） |
   | 模型 | 打开 `http://<NAS 地址>:8081/v1/models` 查看 |

3. 桌面图标打开的是 **控制台**：`http://<NAS 地址>:8081/panel`（首次进入输入上面的 API Key）。

## Web 控制台

- **概览**：服务状态、请求 / 失败 / Token / 平均延迟统计、配置摘要、最近探测结果
- **凭证**：粘贴 / 导入 Cookie、一键校验登录态（尽力解析账号邮箱）、清除
- **模型与探测**：模型清单、真实探测（记录延迟）、一键设默认模型
- **用量**：按小时聚合的 Token 堆叠柱图 + 按模型明细（24h / 3天 / 7天 / 30天）
- **配置**：全部字段可视化编辑，保存即时生效（端口 / 地址需重启）
- **运行日志**：实时日志，按 对话 / 任务 / 系统 频道筛选

> 「校验凭证」与「模型探测」会对上游发起真实请求，请勿频繁操作。

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

- `api_keys`：改成 `[]` 即可免密钥访问（不建议在可被外部访问的网络里这么做）。
- `cookie_file`：指向存放 Gemini Cookie 的文件；也可以直接在面板「凭证」页粘贴保存。
- `proxy`：无法直连 Google 时指定代理，例如 `http://127.0.0.1:7890`。

面板「配置」页保存即时生效；手工编辑文件后需在应用中心重启应用。

## 常见问题

**面板提示输入密钥，密钥是什么？**

密钥就是 `config.json` 里的 `api_keys`，安装默认值是 `sk-gemini`。

**调用时返回 200 但 content 是 null / 客户端显示「没有回复」**

说明这台机器到 Google 的链路被拒绝了，最常见的是机房 IP 遭遇 `BardErrorInfo [1060]`：

- 先确认能访问 Google：`curl -x <代理> -o /dev/null -w "%{http_code}" https://gemini.google.com/app` 应该是 200；也可以直接在面板「凭证」页点「校验当前凭证」。
- 直连大陆网络一定失败，必须在配置里填 `proxy`。
- **1.1.1 起会自动识别并重试**（默认 8 次 / 间隔 1s）；想彻底稳定就在面板「凭证」页粘贴 Gemini 账号 Cookie。

**Pro 模型实际返回 Flash 的内容**

`gemini-3.1-pro` 在无 Cookie 时会路由到 Flash，属上游行为；需要 Gemini Advanced 账号 Cookie 才会真正走 Pro。

## 应用信息

| 项目 | 值 |
| --- | --- |
| 应用名 | `gemini-web2api` |
| 版本 | 1.2.1 |
| 架构 | all（x86 / arm 通用） |
| 端口 | 8081 |
| 运行用户 | `gemini-web2api`（package 模式） |
| 数据目录 | `TRIM_PKGVAR`（`/vol?/@appdata/gemini-web2api`） |
| 控制台 | `http://<NAS 地址>:8081/panel` |
| 上游 | https://github.com/Sophomoresty/gemini-web2api |

## 许可

MIT，随包附带上游 `LICENSE`。
