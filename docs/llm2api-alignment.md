# gemini2api 对齐 badafans/llm2api —— 差距分析（定稿）

> 依据：llm2api 源码逐行清单（/tmp/llm2api-main.go，7979 行，全部带行号）＋ gemini2api 源码逐行查实。

## 两者的性质差异

- gemini2api：包 Google Gemini **网页版**（Cookie 会话），上游固定是 Google，另有第三方 API Key 池做兜底/直连。
- badafans/llm2api：**多上游 API 网关**（Go），上游是 OpenAI/Anthropic/Responses API，核心是「模型别名 → 上游 → 协议互转」。

所以「对齐」= 把 llm2api 里对 gemini2api 有意义的能力补进来，不是把它重写成 llm2api。

## 已对齐的（不用做）

| llm2api 特性 | gemini2api 的对应物 | 证据 |
| --- | --- | --- |
| 三协议对外（Chat/Anthropic/Responses） | 全有，还多出 Gemini 协议与 DeepResearch | main.py:402-417 |
| 图片生成 /v1/images/generations | 有 | openai.py |
| 实时拉上游模型列表 | fetch_models 已有，带 SSRF 防护，支持 anthropic/gemini/openai 三种头 | api_keys.py:214-280 |
| Token 统计按模型、每日桶 | usage_stats 有 model_requests 与 daily bucket | usage_stats.py:13,40-41,141-142 |
| 管理面板自托管、配置热生效 | settings POST 内存+覆盖+.env 三层原子、失败回滚 | settings.py:317-360 |
| 多上游故障转移（失败换下一家） | _dispatch_thirdparty 固定优先、失败冷却换下一家、全败返回最后错误 | openai.py:201-226 |
| 别名 → 目标模型 | ModelMapping.resolve 已接在请求路径上 | openai.py:255-256 |
| 非上游模型走第三方 | 解析出的模型不是 Gemini 就发第三方 | openai.py:269-272 |
| 兜底链（主失败→第三方重试） | fallback.py：随机顺序、跳过非对话模型 | fallback.py |
| 图片本地托管 | /images/{image_id} + data/generated_images | main.py:435 |

## 差距（要对齐的）

| # | llm2api 特性（行号） | gemini2api 现状 | 证据 | 优先级 |
| --- | --- | --- | --- | --- |
| 1 | 多 Key 轮询分发：游标+互斥锁，idx=cursor%total、cursor=(idx+1)%total，total<=1 不轮转（L294-297, 328-338） | get_entries_for_model 固定优先第一家，不是轮询 | api_key_store.py:136-146 | P1 |
| 2 | 429/500/502/503/504 立即切下一把 Key（多 Key 零等待）；单 Key 指数退避 1s 起、×2、30s 封顶；无重试上限（L396-403, 3026-3146, 382-394） | 失败即冷却换下一家，但没有 429 特判、没有指数退避 | openai.py:213-226 | P1 |
| 3 | 每别名独立代理出口：别名带 socks5_proxy（name 或 addr），代理不存在按直连；流式与非流式都走（L48-53, 220-243, 1210-1226） | 只有全局 proxy 环境变量 | P1 |
| 4 | reasoning_effort_map：客户端 effort → 上游 effort，未配置原样透传（L1436-1445, 2404-2411） | 有 reasoning_effort 字段但没有映射表 | models/openai.py:34 | P1 |
| 5 | ensureReasoningContent：无条件给历史 assistant 补空 reasoning_content（DeepSeek thinking 必需，常开）（L2255-2263） | ChatMessage 没有该字段；第三方直连转发原始 messages_raw | models/openai.py:6-11 | P1 |
| 6 | 多模态路由 | ✅ 完成（2.6.0）：映射条目支持 multimodal_target；带新图自动切多模态模型、纯文本轮剥历史图成占位符；覆盖 chat 与 responses 入口 |
| 7 | /v1/images/edits | ✅ 完成（2.5.0）：multipart 透传到第三方池；model 外字段与文件原样；anthropic/gemini 型 501；顺手修了 generations 签名缺 request 的 NameError |
| 8 | 严格别名 | ✅ 完成（2.3.0/2.3.1）：开关默认关、零行为变化；开启后未识别模型 400；覆盖全部 6 个入口；静态模型表兜底修复无健康账号时的误伤 |
| 9 | 响应头透传 | ✅ 完成（2.4.0）：第三方转发失败响应带 X-Upstream-Status |
| 10 | 请求体上限 | ✅ 完成（2.4.0）：默认 10MB / 图片 30MB，0=不限制，每请求动态读、面板改即时生效 |

## 草稿里写错、此处更正的

- **with_reasoning 不是 llm2api 的特性**：本版本全文件 0 处匹配。它对历史推理内容的处理是 ensureReasoningContent（无条件补空值，常开）。上表第 5 条以此为准。
- llm2api 的 /api/reload 是**空操作**（只报计数不读盘，L7384-7397）；真正的热重载在 POST /api/config。gemini2api 的 settings POST 已是三层原子即时生效 —— **不算差距，移除**。
- llm2api 客户端侧**零鉴权**（客户端认证头被剥离不转发）。gemini2api 有 API Key 校验（可关）。这是设计取舍，不对齐 —— 保留 gemini2api 的鉴权能力。
- llm2api 统计按「路由后目标模型名」聚合、每日 0 点重置、异步写盘；gemini2api 的 usage_stats 语义相近，暂不对齐。

## 实现顺序

1. **P1-1/2（Key 轮询与重试）**：api_key_store 加 per-model 游标；openai.py 第三方分发改为「重试循环 + 可重试状态码判定 + 多 Key 立即轮转 / 单 Key 指数退避」。
2. **P1-3（每别名代理出口）**：model_mapping 条目从「别名→模型名」升级为「别名→{target, proxy}」，兼容旧格式；config 加代理清单；转发时按别名出口建客户端。
3. **P1-4（reasoning_effort_map）**：config 加映射表；入口处对 reasoning_effort 查表替换。
4. **P1-5（ensureReasoningContent）**：第三方直连路径转发前给历史 assistant 补 reasoning_content；ChatMessage 加该字段。
5. P2/P3 在 P1 稳定后逐个加，每项过演练。

## 状态

- llm2api 清单：完成（全部带行号）
- gemini2api 清单：本文件按源码逐行查实

### 实现进度

| # | 项 | 状态 | 提交 |
| --- | --- | --- | --- |
| 1 | Key 轮询分发 | ✅ 完成 | d710bf9 |
| 2 | 429/5xx 智能重试 | ✅ 完成（多 Key 零等待切换、单 Key 指数退避；重试次数用 max_retries 封顶，有意偏离） | d710bf9 |
| 3 | 每别名（每 entry）代理出口 | ✅ 完成（http/https/socks5；同一 model 不同 Key 可走不同出口；空串跟随环境代理；坏 scheme 降级直连不炸） | 见 2.2.0 提交 |
| 4 | reasoning_effort_map | ✅ 完成 | d710bf9 |
| 5 | ensureReasoningContent | ✅ 完成 | d710bf9 |
| 6 | 多模态路由 | ✅ 完成（2.6.0）：映射条目支持 multimodal_target；带新图自动切多模态模型、纯文本轮剥历史图成占位符；覆盖 chat 与 responses 入口 |

验证基线：单测 25/25 + effort 映射 5 例 + reasoning 兜底 3 例；原生演练单 fpk 30/0、
双 fpk（版本切换+回滚）36/0；上游耦合 17/0。
