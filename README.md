# Review Agent

Review Agent 是一个面向 Pull Request 的多 Agent 代码审查与审阅器评测平台。它把任务生命周期、预算、失败恢复和审计放在 Harness 中，把具体能力封装为可替换的 Skill。为保持 Python API 与既有配置兼容，代码包和环境变量继续使用 `evoagent` 与 `EVOAGENT_*` 命名。

[Rook](https://github.com/Lem0nTea2002/Rook) 可通过受控 HTTP 客户端调用本服务，并将选中的 Finding 重新交给本地 Coding Agent 修复。

当前支持：

- 审查统一 diff，输出结构化问题、修复建议和测试建议
- GitHub `pull_request` webhook（`opened`、`reopened`、`synchronize`）
- OpenAI 兼容模型；未配置模型时自动使用确定性的本地规则审查器
- SQLite 保存任务状态、执行轨迹和最终报告
- JSON API 与 Markdown 报告
- webhook HMAC-SHA256 签名校验，以及可选的 GitHub PR 评论回写
- Web 管理台、任务 Dashboard 与 Prometheus 指标
- 安全、可靠性、AI 和动态 Skill Agent 并行协作
- 独立分支上的保守型自动修复提交
- PostgreSQL、Redis 生产模式
- 失败案例回流、提示词评测、版本激活与回滚
- LangGraph 节点编排、持久化 checkpoint 与任务断点续跑
- Redis Streams ACK、Worker 租约、指数退避重试和死信队列
- Webhook delivery 幂等、重放时间窗与评论 upsert
- 用户登录、RBAC、租户/仓库隔离和不可变管理审计
- 动态 Skill manifest 校验、签名校验和隔离进程沙箱
- 自动修复后的编译/测试门禁、灰度发布与影子流量
- OpenTelemetry Trace、Prometheus 指标和持久化告警
- 白名单 Git 工作区、提交或 Ref 区间的只读审阅
- Alibaba OpenCodeReview 1.8.5 可替换审阅后端，并保留 Finding 来源

## 快速开始

项目使用 Python 3.11。先安装锁定范围内的运行依赖，并配置本地管理员：

```powershell
python -m pip install -r requirements.txt
$env:EVOAGENT_AUTH_SECRET = '<至少 32 字节随机值>'
$env:EVOAGENT_BOOTSTRAP_ADMIN_USERNAME = 'admin'
$env:EVOAGENT_BOOTSTRAP_ADMIN_PASSWORD = '<至少 10 个字符的密码>'
python -m evoagent
```

服务默认监听 `127.0.0.1:8080`。启动后打开 `http://127.0.0.1:8080/` 登录管理台。API 先登录并携带 Bearer Token：

```powershell
$session = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/auth/login `
  -ContentType 'application/json' `
  -Body (@{username='admin'; password='<你的密码>'} | ConvertTo-Json)
$headers = @{Authorization="Bearer $($session.access_token)"}
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/reviews `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body (@{
    repository = 'demo/api'
    pull_request = 12
    diff = "diff --git a/app.py b/app.py`n--- a/app.py`n+++ b/app.py`n@@ -1 +1,2 @@`n+password = 'secret'`n+eval(user_input)"
  } | ConvertTo-Json)
```

查询任务：

```powershell
Invoke-RestMethod -Headers $headers http://127.0.0.1:8080/v1/tasks/<task-id>
Invoke-WebRequest -Headers $headers http://127.0.0.1:8080/v1/tasks/<task-id>/report
```

## 工作区审阅与 OpenCodeReview

项目路径只由服务端白名单文件解析。客户端提交项目别名，不能提交绝对路径：

```json
{
  "rook": "D:\\absolute\\path\\to\\Rook"
}
```

```powershell
Push-Location tools/ocr
npm ci --ignore-scripts
Pop-Location
$env:EVOAGENT_PROJECTS_FILE = '.data/projects.json'
$env:EVOAGENT_OCR_COMMAND = 'tools/ocr/node_modules/@alibaba-group/open-code-review/bin/ocr.js'
$env:EVOAGENT_OCR_VERSION = '1.8.5'

# 零模型调用：只验证仓库解析和待审文件边界
node $env:EVOAGENT_OCR_COMMAND review --repo D:\path\to\repo --preview
```

工作区 API 支持 `workspace`、`range` 和 `commit`。`reviewers` 可选择 `local`、
`ocr` 或两者；OCR 被显式请求但不可用、版本不符或输出非法时返回稳定错误码。

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/reviews/workspace `
  -Headers $headers -ContentType 'application/json' `
  -Body (@{
    project = 'rook'
    target = 'workspace'
    reviewers = @('local', 'ocr')
    background = $true
  } | ConvertTo-Json)
```

该链路不修改目标仓库。统一 Finding 会保留 `source`、行区间、建议代码、原代码和
`provenance`。OpenCodeReview 仍是独立安装的 Apache-2.0 第三方组件。

本地 JWT、同步/异步 Review、统一 Finding、审计、Dashboard 和只读边界的零模型调用
验收结果见 [`docs/evidence/rook-evoagent-local-acceptance.json`](docs/evidence/rook-evoagent-local-acceptance.json)。

运行测试：

```powershell
python -m unittest discover -s tests -v
```

## 模型配置

默认 `EVOAGENT_LLM_PROVIDER=local`，此时只运行确定性的本地规则 Agent，不会调用大模型。

DeepSeek 官方 API（按 Token 计费）：

```powershell
$env:EVOAGENT_LLM_PROVIDER = 'deepseek'
$env:EVOAGENT_DEEPSEEK_API_KEY = '<deepseek-api-key>'
python -m evoagent
```

通过 OpenRouter 使用有速率限制、可用性可能变化的 DeepSeek 免费模型：

```powershell
$env:EVOAGENT_LLM_PROVIDER = 'openrouter-deepseek-free'
$env:EVOAGENT_OPENROUTER_API_KEY = '<openrouter-api-key>'
python -m evoagent
```

如果指定的免费 DeepSeek 版本下线，可将 `EVOAGENT_LLM_MODEL` 改为 OpenRouter 当前提供的其他 `:free` 模型，或把 Provider 改为 `openrouter-free` 让免费路由自动选择可用模型。

任意其他 OpenAI Chat Completions 兼容端点使用 `custom`：

```powershell
$env:EVOAGENT_LLM_PROVIDER = 'custom'
$env:EVOAGENT_LLM_BASE_URL = 'https://example.com/v1'
$env:EVOAGENT_LLM_API_KEY = '<token>'
$env:EVOAGENT_LLM_MODEL = '<model-name>'
```

密钥只通过环境变量读取，不要提交到仓库。

## 评测与提示词进化

服务启动时会建立基础验证集和隐藏回归集。候选提示词不会接受调用方提供的“回归分数”作为上线依据，而是：

1. 使用当前提示词和候选提示词分别回放同一批验证 Diff；
2. 计算精确率、召回率、F1、严重级别正确率、高风险召回率、干净样本正确率和执行成功率；调用失败会按漏报或失败的干净样本计分；
3. 候选必须在验证集达到最小提升，并通过隐藏集的分数、精确率、召回率和高风险召回率非退化门禁；
4. 没有配置大模型，或验证集、隐藏集样本不足时只保存候选，状态为 `deferred`；
5. 评测记录包含提示词和数据集 SHA-256 指纹，隐藏集只持久化聚合指标，不暴露案例明细；
6. 没有新增有效反馈信号时不会重复创建内容相同的候选版本；
7. 所有评测运行、版本、指标和激活决定均持久化，可回滚。

可通过 `POST /v1/evaluation/cases` 增加版本化样本，`split` 支持 `train`、`validation` 和 `holdout`。样本名称和内容绑定且不可覆盖；修订样本必须使用新名称，重复提交相同内容则保持幂等。期望结果可选填 `rule_id`，用于避免“同一行但错误类别”的结果被算作命中。`POST /v1/evolution/auto` 会从未解决反馈生成候选并执行同样的真实回放门禁。

相关门禁可通过以下环境变量调整：

- `EVOAGENT_EVAL_MIN_CASES`：验证集最少样本数；
- `EVOAGENT_EVAL_MIN_HOLDOUT_CASES`：隐藏集最少样本数；
- `EVOAGENT_EVAL_MAX_CASES`：每个数据分区单次最多回放样本数；
- `EVOAGENT_EVAL_MIN_IMPROVEMENT`：验证集最小分数提升；
- `EVOAGENT_EVAL_MAX_METRIC_REGRESSION`：受保护指标允许的最大退化，默认 `0`。

## GitHub Webhook

Webhook 地址为 `POST /webhooks/github`，事件选择 **Pull requests**。建议配置：

```powershell
$env:EVOAGENT_GITHUB_WEBHOOK_SECRET = '<webhook-secret>'
$env:EVOAGENT_GITHUB_TOKEN = '<fine-grained-token>'
```

默认只返回审查结果，不向 GitHub 写入内容。要自动发布 PR 评论，需显式启用：

```powershell
$env:EVOAGENT_AUTO_POST_REVIEW = 'true'
```

Token 至少需要目标仓库 Pull requests 的读权限；启用评论回写时需要写权限。Webhook 下载 PR diff 时优先使用 payload 中的 `diff_url`。

### GitHub App 安装

在 GitHub Developer settings 创建 GitHub App：

- Setup URL：`<公网地址>/github/setup`
- Webhook URL：`<公网地址>/webhooks/github`
- Webhook event：Pull request
- Repository permissions：Contents `Read & write`、Pull requests `Read & write`、Metadata `Read-only`

下载 App 私钥后配置 `EVOAGENT_GITHUB_APP_ID`、`EVOAGENT_GITHUB_APP_SLUG`、`EVOAGENT_GITHUB_PRIVATE_KEY_PATH` 和 webhook secret。管理台的 GitHub App 页面会进入正式安装流程。

自动修复只覆盖可确定安全的规则，例如调试输出、`shell=True` 和硬编码 Python 凭据；结果始终提交到新的 `evoagent/fix-pr-*` 分支，不直接修改源分支。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 |
| `POST` | `/v1/auth/login` | 登录并获取租户绑定的短期 Bearer Token |
| `POST` | `/v1/reviews` | 创建同步审查任务 |
| `POST` | `/v1/reviews?async=true` | 创建异步审查任务 |
| `POST` | `/v1/reviews/workspace` | 按项目别名创建只读 workspace/range/commit 审阅 |
| `GET` | `/v1/reviewers/ocr/status` | 校验 OpenCodeReview 配置与固定版本 |
| `GET` | `/v1/tasks/{id}` | 获取状态、轨迹和报告 |
| `GET` | `/v1/tasks/{id}/report` | 获取 Markdown 报告 |
| `POST` | `/v1/tasks/{id}/fix` | 创建自动修复分支和提交 |
| `POST` | `/v1/tasks/{id}/feedback` | 回流误报、漏报或坏修复 |
| `POST` | `/v1/tasks/{id}/cancel` | 请求取消任务 |
| `POST` | `/v1/tasks/{id}/resume` | 从最近 checkpoint 续跑任务 |
| `POST` | `/webhooks/github` | 接收 GitHub PR webhook |
| `POST` | `/v1/skills/reload` | 动态重新加载 Skill |
| `POST` | `/v1/evolution/auto` | 从失败案例生成并评测提示词版本 |
| `POST` | `/v1/evolution/propose` | 评测指定提示词候选版本 |
| `GET/POST` | `/v1/evaluation/cases` | 查询或增加版本化评测样本 |
| `GET` | `/v1/evolution/status` | 查询模型与评测门禁就绪状态 |
| `GET` | `/v1/evolution/runs` | 查询持久化的新旧版本评测记录 |
| `POST` | `/v1/skills/{name}/versions/{version}/activate` | 激活或回滚版本 |
| `GET` | `/metrics` | Prometheus 文本指标 |
| `GET` | `/api/alerts` | 查询租户告警 |
| `GET` | `/api/audit` | 查询租户审计日志 |
| `GET` | `/api/queue/dead-letters` | 查询死信任务 |
| `POST` | `/v1/queue/dead-letters/replay` | 重放死信任务 |
| `GET/POST` | `/api/deployments/llm-review`、`/v1/deployments/llm-review` | 查询或配置灰度/影子发布 |

`POST /v1/reviews` 的 `diff` 最大默认 1 MiB；单任务默认最多 8 步、120 秒。可通过环境变量调整，详见 `.env.example`。
