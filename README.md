# gemini-web2api for 飞牛 fnOS（FPK + FnDepot 应用源）

把 [Sophomoresty/gemini-web2api](https://github.com/Sophomoresty/gemini-web2api) 打包成飞牛 fnOS 原生应用（`.fpk`），
并提供一个符合 [FnDepot 外部应用源 V2 规范](https://github.com/EWEDLCM/FnDepot) 的应用源索引 `fnpack.json`。

- 应用包：`packages/gemini-web2api_1.1.0_all.fpk`（x86 / arm 通用，纯 Python，无架构相关二进制）
- 应用源：仓库根目录的 `fnpack.json`
- 上游版本：[2bb988b](https://github.com/Sophomoresty/gemini-web2api/commit/2bb988bfcbb82a7fab5d2c99aa5560ff40d64f7e)（v1.1.0）

## 安装

### 方式一：在 FnDepot 中添加应用源（推荐）

在 FnDepot 的「应用源 / Sources」里添加：

```text
https://github.com/xfengyin/FnDepot
```

同步后即可在 FnDepot 中搜索到 **Gemini Web2API** 并一键安装。

### 方式二：直接安装 fpk

在飞牛 fnOS 的「应用中心 → 手动安装」里选择 `packages/gemini-web2api_1.1.0_all.fpk`，
或者在 SSH 中执行：

```bash
appcenter-cli install-fpk ./packages/gemini-web2api_1.1.0_all.fpk
```

## 使用

安装完成后应用默认监听 **8081** 端口，可以直接当作 OpenAI 兼容接口使用：

| 项目 | 值 |
| --- | --- |
| Base URL | `http://<NAS 地址>:8081/v1` |
| API Key | 默认 `sk-gemini`（可在配置文件里修改或留空免密） |
| 模型列表 | `GET /v1/models` |
| 兼容接口 | `/v1/chat/completions`、`/v1/responses`（Codex CLI）、`/v1beta/models`（Gemini CLI） |

应用卡片（桌面图标）打开的是状态页 `http://<NAS 地址>:8081/`，会返回当前版本和可用模型。

### 配置文件

首次启动时会自动生成，路径为应用数据目录：

```text
/vol1/@appdata/gemini-web2api/config.json   # 卷号按实际安装位置，可能是 /vol2
```

常用字段（与上游 `config.example.json` 一致）：

- `api_keys`：API Key 列表，留空数组表示免密。
- `cookie_file`：Gemini 账号 Cookie 文件路径，填了就用登录态、配额更高。
- `proxy`：访问 Gemini 的 HTTP 代理，例如 `http://127.0.0.1:7890`。
- `default_model`、`temporary_chats`、`log_requests` 等。

改完配置后在应用中心重启应用即可生效（也可以直接点应用设置里的「保存」，会触发 `config_callback` 自动重启）。

> 内置了 `httpx`，所以开箱就是真正的流式输出（SSE），无需另外 `pip install`。

## 跑不通先看这里

这个应用只是把 Gemini 网页端包成 OpenAI 接口，**它自己不产生任何 AI 能力**，所以必须满足两件事：

### 1. 这台 NAS 要能访问 Google

大陆网络直连 `gemini.google.com` 是不通的（DNS 被污染 + TCP 超时）。在配置文件里加代理：

```json
"proxy": "http://192.168.31.244:7890"
```

怎么确认代理到底通不通：

```bash
# 期望 204
curl -x http://<代理>:<端口> -o /dev/null -w "%{http_code}\n" https://www.google.com/generate_204
# 期望 200，可能需要 1 分钟以上
curl -x http://<代理>:<端口> -o /dev/null -w "%{http_code}\n" -m 90 https://gemini.google.com/app
```

### 2. Google 要愿意接受这次请求

**机房 IP（VPS / 云主机）做出口时，Google 会对匿名请求返回 `BardErrorInfo [1060]`**，大约六成的请求会被打回，跟模型、参数都无关。

- **1.1.1 起**：应用能正确识别这个错误并自动重试（默认 `retry_attempts=5`），实测成功率从 ~40% 提升到 6/6。
- **想彻底稳定**：配置 Gemini 账号 Cookie（`cookie_file`），带登录态就不再吃这个限流。Cookie 获取方式见上游 [README_CN](https://github.com/Sophomoresty/gemini-web2api/blob/main/README_CN.md)。

### 接口返回 200 但 content 是 null？

这就是上面说的 1060 被静默吞掉的现象。1.1.1 之前上游的正则只认 `BardErrorInfo [1060]`（带空格），而 Google 实际返回的是 `"BardErrorInfo",[1060]`，匹配不上 → 不抛错 → 不重试 → 返回空内容。

1.1.1 的打包脚本里已经修掉了这个正则（`build.sh` 中「打补丁」那一步），同时把默认 `retry_attempts` 从 3 提到 5。

## 构建

需要 `git`、`python3`（含 pip）、ImageMagick（`convert`）和飞牛官方的 `fnpack`。

```bash
./build.sh
```

脚本会依次：拉取上游源码 → 复制 Python 包到 `app/server` → 安装 `httpx` 到 `app/vendor` →
生成应用图标 → `fnpack build` → 把产物移到 `packages/` → 回写 `fnpack.json` 的 `size` 与 `sha256`。

网络不通时可以先手动 clone 上游，再指定本地目录：

```bash
UPSTREAM_DIR=/path/to/gemini-web2api ./build.sh
```

升级到上游新版本时，修改 `UPSTREAM_REF`（或设同名环境变量）、`src/gemini-web2api/manifest` 里的
`version`，并在 `fnpack.json` 的 `releases` 下新增一个版本节点，然后重新构建。

## 目录结构

```text
.
├── fnpack.json                       # FnDepot 外部应用源 V2 索引
├── build.sh                          # 构建脚本
├── assets/
│   └── gemini-web2api-icon.png       # 应用源里的图标
├── docs/
│   └── gemini-web2api.md             # 应用源里的 README
├── packages/
│   └── gemini-web2api_1.1.0_all.fpk  # 构建产物
└── src/gemini-web2api/               # fnpack 应用包工程
    ├── manifest                      # 应用元数据（appname/version/端口/入口）
    ├── ICON.PNG / ICON_256.PNG       # 包图标（构建生成）
    ├── config/{privilege,resource}   # 运行用户与资源声明
    ├── cmd/                          # 生命周期脚本
    ├── i18n/                         # 多语言文案
    └── app/
        ├── config.example.json       # 默认配置（首次启动复制到应用数据目录）
        ├── server/                   # 上游 Python 包（构建生成）
        ├── vendor/                   # httpx 及其依赖（构建生成）
        └── ui/config, ui/images/     # 桌面入口与入口图标
```

## 实现说明

- **运行方式**：原生应用（非 Docker），以专用应用用户 `gemini-web2api` 运行，通过 `cmd/main` 起停。
- **端口**：`manifest.service_port = 8081`，也写入 `app/ui/config`，与 `TRIM_SERVICE_PORT` 保持一致。
- **Python**：使用系统自带的 `python3`（飞牛 fnOS 基于 Debian，自带 Python 3.11），安装时会校验版本 ≥ 3.8。
- **依赖**：`httpx` 直接打进 fpk 的 `app/vendor`，安装时不联网、不需要 pip。
- **数据**：配置与日志放在 `TRIM_PKGVAR`，升级保留；卸载默认保留数据。

## 许可

上游 gemini-web2api 使用 MIT 许可，本仓库的打包脚本与配置同样使用 MIT。
应用包内已包含上游的 `LICENSE`。
