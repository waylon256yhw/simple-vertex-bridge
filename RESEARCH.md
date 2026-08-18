# Research Notes

技术调研存档 —— 想起来要优化/重构的时候翻这里。最后更新：2026-08。

## 现状评估（2026-08）

整体结论：**架构路线是对的，不需要推倒重来。**

- ~1000 行 Python，6 个模块（`main` / `config` / `auth` / `routes` / `convert` / `proxy`），拆分清晰
- FastAPI + httpx(h2)，共享 AsyncClient，`asyncio.to_thread` 处理阻塞刷新，RLock 防 race —— 并发基础扎实
- 4 种认证模式（AI Studio / SA / ADC / Vertex API Key），Strategy 模式，按环境变量自动探测
- SA 模式走 Vertex 自带 OpenAI 端点纯透传（零转换）；API Key 模式才做 OpenAI↔Gemini 转换 —— 这个分流是聪明的

### 已知欠账（按严重程度）

1. **零测试、零工具链** —— 没有 pytest/ruff/mypy/CI。`convert.py` 全是纯函数，最好测却没测
2. **`svbridge-config.json` 明文落盘 OAuth token** —— 相对 CWD 路径、读写无锁、文件里的 `proxy_key` 会静默覆盖空环境变量
3. **convert.py 功能缺口** —— 不支持 tool calling / `response_format`；`top_k`、`presence_penalty`、`frequency_penalty`、`seed` 没映射；URL 图片硬编码 `image/jpeg`（PNG 会坏）
4. **代码重复** —— model dict 字面量在 `routes.py` 出现 4 次；URL 拼 `?`/`&` 逻辑散落 3 处；`x-goog-user-project` 设置两遍
5. **运行时用 `assert` 做校验**（`proxy.py`、`get_gcloud_project_id`）—— `python -O` 下会消失
6. 小毛病：CLI 参数通过改 `os.environ` 再重载生效（config 加载两次）；非流式 `generateContent` 丢 query 参数；坏 JSON body 直接 500；依赖完全没锁版本

## 调研：Python 生态下做 Gemini 代理，该用什么

核心发现：Google 官方有一份 [Partner and library integrations](https://ai.google.dev/gemini-api/docs/partner-integration) 指南，专门写给做网关/中间件的人。它把生态方分四类，本项目属于 **Aggregator**（把不同 LLM 归一成统一接口的代理）。官方对 Aggregator 的推荐是：

> **Direct API (REST/gRPC)** —— 零依赖、全特性访问、语言无关。

也就是说，**httpx 直连 REST 正是官方推荐路线，不是落后写法。** GenAI SDK 官方定位是给终端应用开发者和企业内部平台的。

### 候选方案评估

#### 1. google-genai（官方 SDK，`googleapis/python-genai`）

- ✅ 旧的 `google-cloud-aiplatform` / `vertexai` 包**已死**：2026 年 6 月后的版本不再支持新 Gemini 模型，Gemini 3.x 只在 google-genai 里。要用官方 SDK 只有这一个选择
- ✅ 真正的价值在本项目里是 **auth 层**：`genai.Client` 统一 API Key / Vertex / ADC 认证，`auth.py` 里 210 行 token 刷新、RLock、APScheduler 大部分可以删掉
- ❌ 代价：请求/响应要过 SDK 类型系统，做纯透传反而碍事；依赖较重（官方自己也提醒 dependency weight）
- **结论：适合"重构掉透传架构"的场景，不适合现在的架构。最划算的用法是只外包 auth/token 管理。**

#### 2. Vercel AI SDK for Python（`vercel-labs/ai-python`）

- 存在，public beta（~165 star），`uv add ai`
- ❌ **方向不匹配**：它是消费端 agent 工具包（tool loop、hooks、结构化输出、`ai.Agent`），模型路由默认走 Vercel AI Gateway（他们的托管云）
- ❌ Python 版**没有** `@ai-sdk/google` 那样的直连 Gemini provider —— TS 版的 provider 生态没跟过来
- ⚠️ PyPI 上的 `ai-sdk-python` 是另一个社区复刻（python-ai-sdk/sdk），不是 Vercel 官方的
- **结论：给"写应用调模型的人"用的，不是给"做服务端网关的人"用的。排除。**

#### 3. 维持现状（REST 透传）

- 就是官方推荐的 Aggregator 路线
- 依赖轻、行为透明、冷启动快
- **结论：主架构不动。**

### 可以顺手偷的东西

1. **`x-goog-api-client` header**（官方 best practice，现在没发）：代理应发送 `x-goog-api-client: simple-vertex-bridge/0.4.0`，Google 能据此识别流量模式、出问题时主动帮忙 debug。改动一行的事
2. **google-genai 的 auth 思路**：如果哪天想删掉 `auth.py` 的 token 管理代码，把认证外包给 `genai.Client`
3. **LiteLLM 的转换映射**：Python 写的 OpenAI↔各家转换层，tool call 映射、MIME 处理这些坑都踩平了。补 `convert.py` 缺口时抄它的映射逻辑，不引入依赖

## 下次开发的优先级建议

1. **补 `convert.py` 测试 + 修 token 落盘安全问题**（纯函数好测；token 改进内存或加锁+绝对路径）
2. **补 tool calling / `response_format` 转换**（参考 LiteLLM 映射，顺手修 MIME 硬编码）
3. **小清理**：去重 model dict / URL helper、`assert` 换正常异常、坏 JSON 返回 400、锁依赖版本
4. **加 `x-goog-api-client` header**（一行）
5. （可选）锁 ruff + mypy + pytest 进 dev 依赖

## 参考链接

- [Partner and library integrations | Gemini API](https://ai.google.dev/gemini-api/docs/partner-integration) —— 官方对网关/框架方的架构建议
- [googleapis/python-genai](https://github.com/googleapis/python-genai) —— 现役官方 Python SDK
- [Vertex AI SDK Is Dead: Migrate to google-genai](https://byteiota.com/vertex-ai-sdk-is-dead-migrate-to-google-genai-before-your-code-breaks/) —— 旧 SDK 弃用时间线
- [vercel-labs/ai-python](https://github.com/vercel-labs/ai-python) —— Vercel AI SDK Python 版（beta）
- [python-ai-sdk/sdk](https://github.com/python-ai-sdk/sdk) —— 社区复刻版（非官方）
