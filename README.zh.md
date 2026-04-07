# Simple Vertex Bridge

Vertex AI 反向代理——在服务端保存凭据，对前端暴露 OpenAI 兼容 API。

基于 [zetaloop/simple-vertex-bridge](https://github.com/zetaloop/simple-vertex-bridge) 二开。[[English]](README.md)

## 快速开始

```bash
cp .env.example .env
# 编辑 .env 填入凭据
docker compose up -d
```

API 地址：`http://localhost:8086`

## 接入前端

在 Open WebUI、SillyTavern 或任何 OpenAI 兼容客户端中配置：

| 设置 | 值 |
|------|---|
| API Base URL | `http://your-server:8086/v1` |
| API Key | 你的 `PROXY_KEY` |
| Model | 从列表选择，或直接输入模型名 |

模型名带不带 `google/` 前缀都行：

```
gemini-2.5-flash          ← 直接使用
google/gemini-2.5-flash   ← 自动去除 google/ 前缀
```

## 配置

编辑 `.env` 后重启 (`docker compose up -d`)。

### 认证方式（四选一）

支持四种认证模式，根据你设置的环境变量**自动检测**——只需设一个：

| 模式 | 你有… | 设置的变量 | 后端 |
|------|-------|-----------|------|
| **AI Studio** | Google AI Studio API 密钥 (`AIza...`) | `GEMINI_API_KEY` | `generativelanguage.googleapis.com` |
| **Service Account** | GCP 服务账号 JSON 密钥文件 | `GOOGLE_APPLICATION_CREDENTIALS` | `aiplatform.googleapis.com` (Vertex AI) |
| **ADC** | gcloud 生成的 `application_default_credentials.json` | `GOOGLE_APPLICATION_CREDENTIALS` + `VERTEX_PROJECT_ID` | `aiplatform.googleapis.com` (Vertex AI) |
| **API Key** | Google Cloud API 密钥 | `VERTEX_API_KEY` | `aiplatform.googleapis.com` (Vertex AI) |

#### AI Studio 模式——最简单

在 [Google AI Studio](https://aistudio.google.com/apikey) 获取 API 密钥，然后：

```bash
GEMINI_API_KEY=AIzaSy...
```

就这样，不需要 GCP 项目或 JSON 文件。支持大部分 Gemini 模型。

> **注意：** AI Studio 和 Vertex AI 是两套独立的基础设施。高峰期时，AI Studio 即使是付费密钥也可能在预览模型上返回 503 错误。如果遇到频率限制，建议切换到 Service Account 模式。

#### Service Account 模式——推荐，最稳定

使用 GCP 服务账号 JSON 密钥文件，走 Vertex AI 通道：

```bash
GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json
SA_FILE=your-key.json        # 宿主机上的文件名，挂载到容器内
VERTEX_LOCATION=us-central1  # 默认区域
```

服务账号需要的 IAM 角色：

| 角色 | 用途 |
|------|------|
| `roles/aiplatform.user` | 调用模型 |
| `roles/serviceusage.serviceUsageConsumer` | 列出模型 |

#### ADC 模式——适用于 gcloud 开发者凭据

使用 `gcloud auth application-default login` 生成的[应用默认凭据](https://cloud.google.com/docs/authentication/application-default-credentials)（`application_default_credentials.json`）。这是 Google Cloud SDK 在本地使用的标准凭据文件。

适用场景：组织策略禁止创建服务账号密钥，或者不想管理 SA JSON 文件、直接复用本地 gcloud 凭据。

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/application_default_credentials.json
VERTEX_PROJECT_ID=your-gcp-project-id   # 必填：用户凭据不携带项目 ID
VERTEX_LOCATION=global                   # 预览模型需要 global
VERTEX_API_VERSION=v1beta1               # Gemini 3+ 预览模型需要 v1beta1
```

> **说明：** ADC 模式在内部复用 `service_account` 代码路径——`google.auth.default()` 对两种凭据类型透明处理。唯一的额外要求是显式设置 `VERTEX_PROJECT_ID`，因为用户凭据不携带项目 ID。

#### API Key 模式——无需 JSON 文件的 Vertex AI

使用 Google Cloud API 密钥（不是 AI Studio 密钥）：

```bash
VERTEX_API_KEY=your-api-key
```

#### 切换认证模式

注释掉不用的密钥即可——模式按优先级自动检测：

```bash
# 使用 AI Studio：
GEMINI_API_KEY=AIzaSy...
#GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json

# 切换到 Service Account，交换注释：
#GEMINI_API_KEY=AIzaSy...
GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json
SA_FILE=your-key.json
```

然后重启：`docker compose up -d`。

### 服务器

```bash
PROXY_KEY=your-secret     # 客户端需作为 Bearer token、x-goog-api-key 请求头或 ?key= 参数发送（留空则不校验）
PORT=8086
BIND=0.0.0.0
```

### 模型列表

```bash
PUBLISHERS=google                    # 拉取哪些 publisher（默认 google,anthropic,meta）
EXTRA_MODELS=gemini-3.1-pro-preview  # 固定追加这些模型到列表中
```

`PUBLISHERS` 控制从哪些 publisher 拉取模型列表。设置 `PUBLISHERS=google` 只显示 Google 模型。

`EXTRA_MODELS` 添加 API 列表中可能没有的模型（如新上线的预览模型）。

### 按模型路由区域

部分模型仅在特定区域可用（如预览模型需要 `global`）。用 `VERTEX_LOCATION_OVERRIDES` 为特定模型指定区域：

```bash
VERTEX_LOCATION=us-central1                      # 默认区域
VERTEX_LOCATION_OVERRIDES=gemini-3.1-*=global     # 预览模型 → global
```

格式：`模式=区域`，逗号分隔。支持 `*` 和 `?` 通配符（[fnmatch](https://docs.python.org/3/library/fnmatch.html)）。按顺序匹配，首个命中生效，无匹配则回退到 `VERTEX_LOCATION`。

### 完整环境变量

| 变量 | 默认值 | 说明 |
|------|-------|------|
| `GEMINI_API_KEY` | — | AI Studio API 密钥（触发 AI Studio 模式） |
| `VERTEX_API_KEY` | — | Google Cloud API 密钥（触发 Express 模式） |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | 容器内 SA 或 ADC JSON 路径 |
| `SA_FILE` | `sa.json` | 宿主机 SA JSON 文件名（Docker 挂载用） |
| `VERTEX_PROJECT_ID` | — | GCP 项目 ID——ADC 模式必填，SA 模式可选覆盖 |
| `VERTEX_LOCATION` | `us-central1` | 默认区域（`us-central1`、`global` 等） |
| `VERTEX_LOCATION_OVERRIDES` | — | 按模型路由区域（`模式=区域,...`） |
| `VERTEX_API_VERSION` | `v1` | Vertex AI API 版本（`v1` 或 `v1beta1`，Gemini 3+ 预览模型需要 `v1beta1`） |
| `PROXY_KEY` | *（任意）* | 代理认证，支持 Bearer Token、`x-goog-api-key` 请求头或 `?key=` |
| `PORT` | `8086` | 服务端口 |
| `BIND` | `localhost` | 绑定地址 |
| `PUBLISHERS` | `google,anthropic,meta` | 模型列表拉取的 publisher |
| `EXTRA_MODELS` | — | 固定追加的额外模型 |

### 命令行参数

不用 Docker 时：

```bash
simple-vertex-bridge -p 8086 -b 0.0.0.0 -k your-secret
```

`-p/--port`、`-b/--bind`、`-k/--key`、`--auto-refresh/--no-auto-refresh`、`--filter-model-names/--no-filter-model-names`。命令行参数优先于环境变量。

## API 端点

| 端点 | 格式 | 说明 |
|------|------|------|
| `POST /v1/chat/completions` | OpenAI | 聊天补全（支持流式） |
| `GET /v1/models` | OpenAI | 列出可用模型 |
| `POST /v1/models/{model}:generateContent` | Gemini | Gemini 原生（所有认证模式） |
| `POST /v1/models/{model}:streamGenerateContent` | Gemini | Gemini 原生流式（所有认证模式） |
| `POST /v1beta/models/{model}:*` | Gemini | 同上，v1beta 前缀 |

Gemini 端点的模型路径支持 `google/model-name` 或裸 `model-name`。认证方式：`Authorization: Bearer <key>` 请求头或 `?key=<key>` 查询参数。

## 开发

参见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 开源协议

The Unlicense.
