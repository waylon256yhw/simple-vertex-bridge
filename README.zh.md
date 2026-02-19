# Simple Vertex Bridge

轻量 Vertex AI 反向代理，支持双认证模式和双 API 格式。

基于 [zetaloop/simple-vertex-bridge](https://github.com/zetaloop/simple-vertex-bridge) 二次开发，改进如下：

- **双认证模式**：Service Account JSON + API Key (Express)
- **双 API 格式**：OpenAI 兼容 + Gemini 原生端点
- **可配置区域**：`VERTEX_LOCATION` 环境变量（不再硬编码）
- **Docker 支持**：Dockerfile + docker-compose
- **Python 3.11+**：从 3.13 降低

[[English]](README.md)

## 认证模式

| 模式 | 触发条件 | 端点支持 | Token 管理 |
|------|---------|----------|-----------|
| **Service Account** | 设置 `GOOGLE_APPLICATION_CREDENTIALS` | OpenAI（原生透传）+ Gemini | 每 5 分钟自动刷新 |
| **API Key (Express)** | 设置 `VERTEX_API_KEY` | OpenAI（自动转换为 Gemini 格式）+ Gemini | 无需管理 |

### Service Account 模式

使用 Google Cloud 服务账号 JSON 密钥。OpenAI 兼容端点的请求直接透传到 Vertex AI 的 OpenAI 端点，零转换。

```bash
# 通过 gcloud CLI
gcloud auth application-default login

# 或通过服务账号密钥文件
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
export VERTEX_LOCATION=us-central1  # 可选，默认 us-central1
```

服务账号需要以下 IAM 角色：

| 角色 | 用途 |
|------|------|
| **Vertex AI User** (`roles/aiplatform.user`) | 调用模型端点（聊天、生成） |
| **Service Usage Consumer** (`roles/serviceusage.serviceUsageConsumer`) | 通过 `/v1/models` 列出模型 |

```bash
SA=your-sa@project.iam.gserviceaccount.com
PROJECT=your-project-id

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/serviceusage.serviceUsageConsumer"
```

### API Key 模式 (Express)

使用 Google Cloud API 密钥。由于 Vertex AI Express 端点不支持 API Key 认证的 OpenAI 格式，`/v1/chat/completions` 的请求会自动在 OpenAI 和 Gemini 格式之间转换。

```bash
export VERTEX_API_KEY=your-google-cloud-api-key
```

## API 端点

配置了 `PROXY_KEY` 时，所有端点需要 `Authorization: Bearer <PROXY_KEY>` 头。

### OpenAI 兼容

**`POST /v1/chat/completions`**

标准 OpenAI 聊天补全格式，支持流式输出。

```bash
curl http://localhost:8086/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_PROXY_KEY" \
  -d '{
    "model": "google/gemini-2.5-flash",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

支持的参数：`model`、`messages`、`stream`、`temperature`、`top_p`、`max_tokens`、`max_completion_tokens`、`stop`、`n`。

**`GET /v1/models`**

返回 OpenAI 格式的可用模型列表。

### Gemini 原生（仅 Service Account 模式）

**`POST /v1/models/{model}:generateContent`**
**`POST /v1/models/{model}:streamGenerateContent`**

Gemini API 直接透传——请求体原样转发，仅注入认证信息。

```bash
curl http://localhost:8086/v1/models/gemini-2.5-flash:generateContent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_PROXY_KEY" \
  -d '{"contents":[{"parts":[{"text":"Hello"}]}]}'
```

## 快速开始

### Docker（推荐）

1. 从示例创建 `.env`：
```bash
cp .env.example .env
```

2. 编辑 `.env`：
```bash
# Service Account 模式
GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json
VERTEX_LOCATION=us-central1
PROXY_KEY=your-secret-key

# 或 API Key 模式
# VERTEX_API_KEY=your-google-cloud-api-key
# PROXY_KEY=your-secret-key
```

3. 如果使用 Service Account 模式，将 SA JSON 文件放到项目目录，并修改 `docker-compose.yml` 中的 volume 挂载路径。

4. 启动：
```bash
docker compose up -d
```

### 直接运行

```bash
# 从 PyPI 安装运行
uvx simple-vertex-bridge -b 0.0.0.0 -k your-secret-key

# 或从源码
git clone https://github.com/zetaloop/simple-vertex-bridge.git
cd simple-vertex-bridge
uv sync && source .venv/bin/activate
python -m svbridge.main -b 0.0.0.0 -k your-secret-key
```

## 配置

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `-p`, `--port` | `8086` | 监听端口 |
| `-b`, `--bind` | `localhost` | 绑定地址 |
| `-k`, `--key` | *（任意）* | 代理认证密钥 |
| `--auto-refresh` / `--no-auto-refresh` | 启用 | 后台 Token 自动刷新（SA 模式） |
| `--filter-model-names` / `--no-filter-model-names` | 启用 | 过滤 `/v1/models` 中的常见模型名 |

命令行参数优先于环境变量。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|-------|------|
| `VERTEX_API_KEY` | — | Google Cloud API 密钥（触发 Express 模式） |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | 服务账号 JSON 文件路径 |
| `VERTEX_LOCATION` | `us-central1` | Google Cloud 区域（仅 SA 模式） |
| `PROXY_KEY` | *（任意）* | 代理认证 Bearer Token |
| `PORT` | `8086` | 服务端口 |
| `BIND` | `localhost` | 绑定地址 |
| `PUBLISHERS` | `google,anthropic,meta` | 模型列表拉取的 publisher |
| `EXTRA_MODELS` | — | 追加到 `/v1/models` 的额外模型 ID |

## 开源协议

The Unlicense.
