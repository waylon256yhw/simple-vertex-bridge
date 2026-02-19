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
gemini-2.5-flash          ← 自动补全为 google/gemini-2.5-flash
google/gemini-2.5-flash   ← 直接使用
```

## 配置

编辑 `.env` 后重启 (`docker compose up -d`)。

### 认证方式（二选一）

**Service Account**（推荐）——使用 JSON 密钥文件：

```bash
GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json
SA_FILE=your-key.json        # 宿主机上的文件名，挂载到容器内
VERTEX_LOCATION=us-central1  # 或 global（用于最新预览模型）
```

服务账号需要的 IAM 角色：

| 角色 | 用途 |
|------|------|
| `roles/aiplatform.user` | 调用模型 |
| `roles/serviceusage.serviceUsageConsumer` | 列出模型 |

**API Key**（更简单，功能更少）——使用 Google Cloud API 密钥：

```bash
VERTEX_API_KEY=your-api-key
```

### 服务器

```bash
PROXY_KEY=your-secret     # 客户端需作为 Bearer token 发送（留空则不校验）
PORT=8086
BIND=0.0.0.0
```

### 模型列表

```bash
PUBLISHERS=google                  # 拉取哪些 publisher（默认 google,anthropic,meta）
EXTRA_MODELS=gemini-3.1-pro-preview  # 固定追加这些模型（自动补 google/ 前缀）
```

`PUBLISHERS` 控制从哪些 publisher 拉取模型列表。设置 `PUBLISHERS=google` 只显示 Google 模型。

`EXTRA_MODELS` 添加 API 列表中可能没有的模型（如新上线的预览模型）。不含斜杠的模型名自动补 `google/` 前缀。

### 完整环境变量

| 变量 | 默认值 | 说明 |
|------|-------|------|
| `VERTEX_API_KEY` | — | API 密钥（触发 Express 模式） |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | 容器内 SA JSON 路径 |
| `SA_FILE` | `sa.json` | 宿主机 SA JSON 文件名（Docker 挂载用） |
| `VERTEX_LOCATION` | `us-central1` | 区域（`us-central1`、`global` 等） |
| `PROXY_KEY` | *（任意）* | 代理认证 Bearer Token |
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
| `POST /v1/models/{model}:generateContent` | Gemini | Gemini 原生（仅 SA 模式） |
| `POST /v1/models/{model}:streamGenerateContent` | Gemini | Gemini 原生流式（仅 SA 模式） |

## 开发

参见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## 开源协议

The Unlicense.
