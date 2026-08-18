# Research Notes

技术调研存档 —— 想起来要优化/重构的时候翻这里。最后更新：2026-08。

## 现状评估（2026-08）

整体结论：**主架构路线（FastAPI + httpx REST 透传）是对的，不需要推倒重来；但格式转换层与错误处理有较多深层缺陷，Auth 层存在历史遗留过度设计。**

- ~1000 行 Python，6 个模块（`main` / `config` / `auth` / `routes` / `convert` / `proxy`），分层基本清晰
- FastAPI + httpx(h2)，共享 AsyncClient，并发与异步 I/O 基础方向正确
- 4 种认证模式（AI Studio / SA / ADC / Vertex API Key），按环境变量自动探测
- SA 模式走 Vertex 自带 OpenAI 端点透传；API Key / AI Studio 模式走 `convert.py` 双向转换

### 已知欠账与隐患（按严重程度）

1. **流式转换错误处理存在严重 Bug**
   - `proxy.py` 的 `_stream_with_convert`：当上游 Vertex / AI Studio 返回 4xx/5xx 时，仍直接返回 `HTTP 200 text/event-stream` 并在流中抛出未封装的原始报错，导致客户端（如 Open WebUI）解析崩溃或静默挂起。
   - `convert.py` 的 `gemini_stream_to_openai`：SSE 流中途若出现上游 error JSON（如安全策略拦截、配额耗尽），因找不到 `candidates` 会被静默吞掉，最终只发送 `data: [DONE]`，客户端完全感知不到失败原因。
2. **`svbridge-config.json` 明文落盘与凭据污染**
   - 相对 CWD 路径、读写无锁、将短期 OAuth Token 写入磁盘文件。
   - 文件里的 `key` 会在环境变量未设置 `PROXY_KEY` 时静默覆盖生效，极易引发混淆与安全隐患。
3. **`convert.py` 功能严重缺失**
   - **Tool Calling / Function Calling**：完全不支持 OpenAI `tools` / `tool_choice` / `tool_calls` 及 `role: "tool"` 响应转换，无法支撑 Agent / 插件类客户端。
   - **Structured Outputs / JSON Mode**：未映射 `response_format`（`type: json_object` / `json_schema`）到 Gemini 的 `responseMimeType: "application/json"` 及 `responseSchema`。
   - **Gemini 2.x / 3.x 思考模型（Thinking / Reasoning）**：未映射 `thinkingConfig` 参数；流式响应中的思考过程（`thought: true`）未映射为 OpenAI 规范的 `reasoning_content` delta。
   - **生成参数缺口**：`presence_penalty`、`frequency_penalty`、`seed`、`top_k` 均未映射。
   - **Stream Usage**：未规范支持 OpenAI 标准的 `stream_options: {"include_usage": true}`（在流末尾输出独立 usage chunk）。
   - **多模态 URL 处理**：URL 图片硬编码 `image/jpeg`，且 `fileData.fileUri` 不支持外部 HTTP URL（Gemini API 仅支持 GCS 或 File API 路径）。
4. **Auth 层的历史遗留包袱与过度设计**
   - `auth.py` 混杂了 `APScheduler` 后台线程、`RLock` 与文件读写；每次刷新还重新调用 `default()` 解析环境。
   - 实际上只需在内存中持有 `google.auth.credentials`，在异步上下文按需/定期刷新即可，可顺便**彻底移除 `apscheduler` 和 `requests` 依赖**。
5. **代码重复与路由细节缺陷**
   - **非流式 `generateContent` 丢失 Query 参数**：`streamGenerateContent` 转发了 query，但 `generate_content` 漏掉了 `_forward_query`。
   - **`ApiKeyAuth` 写死 API 版本**：URL 中硬编码 `/v1`，无法通过 `VERTEX_API_VERSION` 配置 `v1beta1`。
   - **`x-goog-user-project` 重复注入**：在 `auth.py` 与 `routes.py` 中重复设置。
   - **模型名称归一化过于粗暴**：`_normalize_model` 对所有不带 `/` 的模型一律加 `google/` 前缀，影响潜在的第三方 publisher 模型。
   - **模型列表重复**：`/v1/models` 未对合并后的模型列表做 id 去重。
   - **运行时用 `assert` 做校验**（`proxy.py`、`get_gcloud_project_id`）—— 在 `python -O` 下会失效。
   - **小毛病**：CLI 参数通过改 `os.environ` 导致 `load_config` 被调用两次；非 JSON 请求体直接触发 500。
6. **零测试、零工具链** —— 没有 pytest/ruff/mypy/CI。`convert.py` 全部由纯函数组成，极易单测却处于裸奔状态。

---

## 调研：Python 生态下做 Gemini 代理，该用什么

核心发现：Google 官方有一份 [Partner and library integrations](https://ai.google.dev/gemini-api/docs/partner-integration) 指南，专门写给做网关/中间件的人。它把生态方分四类，本项目属于 **Aggregator**（把不同 LLM 归一成统一接口的代理）。官方对 Aggregator 的推荐是：

> **Direct API (REST/gRPC)** —— 零依赖、全特性访问、语言无关。

也就是说，**httpx 直连 REST 正是官方推荐路线，不是落后写法。** GenAI SDK 官方定位是给终端应用开发者和企业内部平台的。

### 候选方案评估

#### 1. google-genai（官方 SDK，`googleapis/python-genai`）

- ✅ 旧的 `google-cloud-aiplatform` / `vertexai` 包**已死**：2026 年 6 月后的版本不再支持新 Gemini 模型，Gemini 3.x 只在 google-genai 里。
- ❌ **不需要为了 Auth 引入它**：`google-auth` 本身就是其底层，直接用轻量级的 `google.auth.default()` 管理凭据即可，不需要为 auth 引进几十 MB 的重型 SDK。
- ❌ 代价：请求/响应要过 SDK 类型系统，做纯透传反而碍事；依赖较重（官方自己也提醒 dependency weight）。
- **结论：不引入。保持 REST 透传架构。**

#### 2. Vercel AI SDK for Python（`vercel-labs/ai-python`）

- 存在，public beta（~165 star），`uv add ai`
- ❌ **方向不匹配**：它是消费端 agent 工具包（tool loop、hooks、结构化输出、`ai.Agent`），模型路由默认走 Vercel AI Gateway（他们的托管云）。
- ❌ Python 版**没有** `@ai-sdk/google` 那样的直连 Gemini provider —— TS 版的 provider 生态没跟过来。
- **结论：给“写应用调模型的人”用的，不是给“做服务端网关的人”用的。排除。**

#### 3. 维持现状（REST 透传 + 轻量双向转换）

- 官方推荐的 Aggregator 路线
- 依赖轻、行为透明、冷启动快、延迟低
- **结论：主架构坚定不动。**

### 可以顺手偷的东西

1. **`x-goog-api-client` header**（官方 best practice，现在没发）：代理应发送 `x-goog-api-client: simple-vertex-bridge/0.4.0`，Google 能据此识别流量模式、出问题时主动帮忙 debug。
2. **LiteLLM 的转换映射**：Python 写的 OpenAI↔各家转换层，tool call 映射、thinking 映射、MIME 处理这些坑都踩平了。补 `convert.py` 缺口时抄它的映射逻辑，不引入额外依赖。

---

## 优化推进路线（分阶段建议）

### 阶段一：架构精简与稳定性加固（打好底座）
1. **砍掉 `svbridge-config.json` 磁盘落盘**：改为纯内存 Token 缓存，杜绝明文凭据与 `PROXY_KEY` 污染。
2. **重构 Auth 刷新机制**：移除 `apscheduler` 与 `requests`，改用内存中 `google-auth` 异步按需/定时刷新，精简依赖。
3. **修复 `proxy.py` 错误处理**：修复流式转换时上游非 200 状态码被掩盖为 200 的 Bug，补齐 SSE 上游错误透传。
4. **清理运行时 `assert` 与异常**：非法 JSON 返回 400，消除 `python -O` 隐患。

### 阶段二：补全 `convert.py` 转换能力（核心体验）
1. **Tool Calling / Function Calling**：支持 `tools` ↔ `functionDeclarations` 双向映射及工具执行结果响应。
2. **Structured Outputs**：支持 `response_format` (JSON Object / JSON Schema) ↔ `responseMimeType` / `responseSchema`。
3. **Gemini 2.x/3.x 思考模型（Reasoning）**：支持 `thinkingConfig` 参数，流式解析中将 `thought: true` 映射为 `reasoning_content` delta。
4. **补齐生成参数**：映射 `presence_penalty`、`frequency_penalty`、`seed`、`top_k` 等。
5. **规范 Stream Usage**：支持 `stream_options.include_usage`。

### 阶段三：路由与细节规范打磨
1. **修复 `generate_content` Query 参数透传**。
2. **统一 API 版本**：修复 `ApiKeyAuth` 中硬编码的 `/v1`。
3. **模型列表去重与 Publisher 前缀规范**。
4. **添加 `x-goog-api-client` 官方 Header**。

### 阶段四：工具链与测试工程化
1. **引入 `pytest`**：针对 `convert.py` 所有纯函数（普通对话、流式 SSE、多模态、工具调用、思考流、异常情况）编写全面的单元测试。
2. **配置 `ruff` 和 `mypy`**，锁定依赖版本。

---

## 参考链接

- [Partner and library integrations | Gemini API](https://ai.google.dev/gemini-api/docs/partner-integration) —— 官方对网关/框架方的架构建议
- [googleapis/python-genai](https://github.com/googleapis/python-genai) —— 现役官方 Python SDK
- [Vertex AI SDK Is Dead: Migrate to google-genai](https://byteiota.com/vertex-ai-sdk-is-dead-migrate-to-google-genai-before-your-code-breaks/) —— 旧 SDK 弃用时间线
- [LiteLLM Gemini Provider Implementation](https://github.com/BerriAI/litellm) —— 成熟的 OpenAI ↔ Gemini 转换参考
