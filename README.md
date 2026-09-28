# gemini-web2api for 飞牛 fnOS（FPK + FnDepot 应用源）

把 [Sophomoresty/gemini-web2api](https://github.com/Sophomoresty/gemini-web2api) 封装成飞牛 fnOS 原生应用（`.fpk`），并在其基础上内置一套 **Web 控制台**；仓库同时提供符合 [FnDepot 外部应用源 V2 规范](https://github.com/EWEDLCM/FnDepot) 的应用源索引 `fnpack.json`。

| | |
| --- | --- |
| 应用包 | `packages/gemini-web2api_1.2.1_all.fpk`（x86 / arm 通用，纯 Python，无架构相关二进制） |
| 应用源 | 仓库根目录 `fnpack.json` |
| 控制台 | `http://<NAS 地址>:8081/panel` |
| API | `http://<NAS 地址>:8081/v1` |

## 相对上游的增强（1.2.x）

- **Web 控制台（`/panel`）**：概览、凭证管理、模型探测、用量统计、配置编辑、运行日志；深浅色主题、密钥门
- 桌面图标改为直接打开控制台（`ui/config` 的 `url` 指向 `/panel`）
- 应用显示名称统一为 `gemini-web2api`
- 1.1.x 时期的构建补丁（BardErrorInfo 正则、默认重试 8 次 / 间隔 1s）已并入源码
- 1.2.1 起仓库内置完整源码（`src/gemini-web2api/app/server`），构建时不再拉取上游

## 安装

### 方式一：FnDepot 应用源（推荐）

在 FnDepot 的「应用源 / Sources」里添加：

```text
https://github.com/xfengyin/FnDepot
```

同步后搜索 **gemini-web2api** 一键安装 / 更新。

### 方式二：手动安装 fpk

在应用中心的「手动安装」里选择 `packages/gemini-web2api_1.2.1_all.fpk`。

## 使用

| 项目 | 值 |
| --- | --- |
| Base URL | `http://<NAS 地址>:8081/v1` |
| API Key | 默认 `sk-gemini`（面板与接口共用，可在面板「配置」里修改或留空免密） |
| 模型列表 | `GET /v1/models` |
| 兼容接口 | `/v1/chat/completions`、`/v1/responses`（Codex CLI）、`/v1beta/models`（Gemini CLI） |
| 控制台 | `http://<NAS 地址>:8081/panel` |

### Web 控制台

- **概览**：服务状态、请求 / 失败 / Token / 平均延迟统计、配置摘要、最近探测结果
- **凭证**：粘贴 / 导入 Gemini Cookie，一键校验登录态、清除（不回显完整 Cookie）
- **模型与探测**：模型清单与真实探测（记录延迟）、一键设默认模型
- **用量**：按小时聚合的 Token 堆叠柱图 + 按模型明细（24h / 3天 / 7天 / 30天）
- **配置**：可视化编辑全部字段并热生效（端口 / 地址需重启）
- **运行日志**：实时日志，按 对话 / 任务 / 系统 频道筛选

> 首次进入面板需要输入 `api_keys` 里的密钥（安装默认 `sk-gemini`）。

### 配置文件

首次启动自动生成于应用数据目录：

```text
/vol1/@appdata/gemini-web2api/config.json   # 卷号按实际安装位置，可能是 /vol2
```

常用字段（与上游 `config.example.json` 一致）：

- `api_keys`：密钥列表，留空数组表示免密。
- `cookie_file`：Gemini 账号 Cookie 文件路径（推荐，配额更高且更稳定）。
- `proxy`：访问 Gemini 的 HTTP 代理，例如 `http://127.0.0.1:7890`。
- `default_model`、`temporary_chats`、`log_requests` 等。

改配置有两种方式：面板「配置」页保存（即时生效）；或编辑文件后在应用中心重启。

> 包内已内置 `httpx`，开箱即是真正的流式输出（SSE），无需 pip。

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

也可以直接在面板「凭证」页点「校验当前凭证」。

### 2. Google 要愿意接受这次请求

**机房 IP（VPS / 云主机）做出口时，Google 会对匿名请求返回 `BardErrorInfo [1060]`**，大约六成的请求会被打回，跟模型、参数都无关。

- **1.1.1 起**：应用能正确识别这个错误并自动重试（默认 `retry_attempts=8`、`retry_delay_sec=1`），实测成功率从 ~40% 提升到 10/10。
- **想彻底稳定**：在面板「凭证」页粘贴 Gemini 账号 Cookie（或配置 `cookie_file`），带登录态就不再吃这个限流。

### 接口返回 200 但 content 是 null？

这就是上面说的 1060 被静默吞掉的现象。1.1.1 之前上游的正则只认 `BardErrorInfo [1060]`（带空格），而 Google 实际返回的是 `"BardErrorInfo",[1060]`，匹配不上 → 不抛错 → 不重试 → 返回空内容。该修复已并入源码（`src/gemini-web2api/app/server/gemini_web2api/gemini.py`）。

## 构建

需要 `python3`（含 pip）和飞牛官方的 `fnpack`。

```bash
./build.sh
```

脚本流程：同步 `app/vendor`（httpx）→ `fnpack build` → 产物移入 `packages/` → 回写 `fnpack.json` 的 `size` / `sha256`。

网络受限时，可以先把 httpx 及依赖准备好再构建：

```bash
VENDOR_DIR=/path/to/vendor ./build.sh
```

发布新版本：改 `src/gemini-web2api/manifest` 里的 `version`，在 `fnpack.json` 的 `releases` 下新增版本节点（写好 changelog），然后执行 `./build.sh`。

## 目录结构

```text
.
├── fnpack.json                       # FnDepot 外部应用源 V2 索引
├── build.sh                          # 构建脚本
├── assets/gemini-web2api-icon.png    # 应用源图标
├── docs/gemini-web2api.md            # 应用源里的 README
├── packages/                         # 构建产物（fpk）
└── src/gemini-web2api/               # fnpack 应用包工程
    ├── manifest                      # 应用元数据（appname/version/端口/入口）
    ├── ICON.PNG / ICON_256.PNG       # 包图标
    ├── config/{privilege,resource}   # 运行用户与资源声明
    ├── cmd/                          # 生命周期脚本
    ├── i18n/                         # 多语言文案
    └── app/
        ├── config.example.json       # 默认配置（首次启动复制到应用数据目录）
        ├── server/                   # 完整源码（含 Web 控制台 panel/）
        ├── vendor/                   # httpx 及依赖（构建生成，不入库）
        └── ui/config, ui/images/     # 桌面入口（指向 /panel）与图标
```

## 实现说明

- **运行方式**：原生应用（非 Docker），以专用用户 `gemini-web2api` 运行，通过 `cmd/main` 起停。
- **端口**：`manifest.service_port = 8081`，与 `app/ui/config`、`TRIM_SERVICE_PORT` 保持一致。
- **Python**：使用系统自带 `python3`（fnOS 基于 Debian，自带 3.11），安装时校验 ≥ 3.8。
- **依赖**：`httpx` 打进 `app/vendor`，安装后不联网、不需要 pip。
- **数据**：配置 / 日志 / 用量统计存放在 `TRIM_PKGVAR`（`@appdata/gemini-web2api`），升级保留。
- **控制台**：纯标准库实现（`server/gemini_web2api/panel/`），与 API 共用端口与密钥。

## 许可

上游 gemini-web2api 与本仓库的打包脚本同样使用 MIT 许可；应用包内包含上游 `LICENSE`。
