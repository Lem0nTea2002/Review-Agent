# Review Agent

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="assets/readme/hero-mobile.svg">
    <img src="assets/readme/hero.svg" width="100%" alt="Review Agent：从 PR Diff 到可追踪 Finding 的受控审查流水线">
  </picture>
</p>

<p align="center"><strong>把 PR Diff 转换为可定位、可质疑、可复现、可验证的结构化 Finding。</strong></p>

<p align="center">
  <code>Python 3.11</code> · <code>LangGraph</code> · <code>SQLite / PostgreSQL</code> · <code>Redis Streams</code> · <code>OpenTelemetry</code>
</p>

Review Agent 是一个面向 Pull Request 的多 Agent 代码审查与审阅器评测平台。Harness 管理任务生命周期、预算、checkpoint、失败恢复和审计，Skill 承载可替换的审查能力。为兼容既有 Python API 与配置，代码包和环境变量沿用 `evoagent` 与 `EVOAGENT_*` 命名。

> [!IMPORTANT]
> 默认模式使用确定性本地规则审阅器，工作区审阅保持只读，GitHub 评论回写保持关闭。仓库公开指标来自 **100 例 synthetic-controlled Diff**，用于验证评测链路与指标形态；`production_activation_allowed=false`，真实 PR 生产效果仍待独立评测。

[产品界面](#产品界面) · [验证证据](#验证证据) · [工作机制](#工作机制) · [快速开始](#快速开始) · [输入与输出](#输入与输出) · [评测门禁](#评测门禁) · [配置与集成](#配置与集成)

## 产品界面

<p align="center">
  <img src="assets/readme/review-workbench.png" width="100%" alt="Review Agent 三栏审查工作台：变更文件、Diff、结构化 Finding 与多 Agent 执行阶段">
</p>

<p align="center"><sub>真实本地审查结果：变更文件与 Diff 保持只读，Finding 保留位置、证据、风险等级和来源。</sub></p>

## 它解决什么

- **输入边界**：Unified Diff、GitHub Webhook，以及白名单内的
  `workspace`、`range`、`commit`。
- **审查过程**：Planner 分派任务，并行 Specialist 产出证据，Critic 与 Test
  质疑结果，Fix Gate 与 Verifier 完成决策。
- **可消费结果**：包含 `file`、`line`、`source`、`provenance`、修复建议和
  测试建议的结构化 Finding。

平台将“发现问题”和“相信问题”拆成独立阶段：每条高风险 Finding 都要绑定新增代码行、匹配证据，并通过静态复现与最终验证。

## 验证证据

### 本地闭环验收

[`docs/evidence/rook-evoagent-local-acceptance.json`](docs/evidence/rook-evoagent-local-acceptance.json) 记录了一次可审计的本地回环验收：

| 验收项 | 记录结果 |
|---|---|
| 运行范围 | `local-loopback` |
| 模型 Provider | `local`，`provider_calls=0` |
| 同步 / 异步任务 | `SUCCESS` / `SUCCESS` |
| Finding 来源 | `local-rules`、`open-code-review` |
| 目标工作区 | `workspace_unchanged=true` |
| 审计记录 | `audit_recorded=true` |
| 受控限制 | `fake-1.8.5` OCR；未执行 GitHub 写操作 |

这份证据覆盖本地 JWT、同步/异步 Review、统一 Finding、Dashboard、审计和只读边界。验收范围限定为本地链路，真实模型审阅与 GitHub 写入仍待独立验收。

### 受控离线评测

[`evaluation_data/pr_diff_100.jsonl`](evaluation_data/pr_diff_100.jsonl) 包含 100 例 synthetic-controlled PR-like Diff：40 个风险样本、60 个干净样本，并按仓库隔离为 8 个 validation 仓库和 2 个 holdout 仓库。可复现断言位于 [`tests/test_evaluation_harness.py`](tests/test_evaluation_harness.py)。

| 指标 | LocalRule 基线 | 多 Agent 候选 |
|---|---:|---:|
| F1 | 71.43% | 82.50% |
| 高风险召回率 | — | 94.74% |
| 干净样本准确率 | — | 91.67% |
| Safe Fix Rate | — | 78.79% |
| 执行成功率 | — | 100.00% |

量化条件通过后，生产激活门仍保持关闭。数据生成逻辑、Reviewer 构造和指标目标见 [`evoagent/evaluation_benchmark.py`](evoagent/evaluation_benchmark.py)。

## 工作机制

```text
PR Diff / Webhook / Workspace
              │
              ▼
          Planner
              │ assignments
              ▼
  Parallel Specialists / Skills
              │ evidence
              ▼
       Critic → Test Agent
              │ accepted + reproducible
              ▼
         Synthesizer
              │ structured Finding
              ▼
      Fix Gate → Verifier
              │
              ▼
   Report + Trace + Checkpoint
```

1. **Harness 接管任务**：校验 Diff、限制步数与时长、持久化状态和 checkpoint。
2. **Planner 建立计划**：识别文件、语言和风险域，为 Specialist 生成审查分工。
3. **Specialist 并行取证**：安全、可靠性、AI 和动态 Skill 独立输出候选 Finding。
4. **Critic 与 Test 交叉验证**：检查新增行位置、引用证据、解释、修复建议和复现条件。
5. **Synthesizer 仲裁**：按 `path + line + rule_id` 去重，调整置信度并排序风险。
6. **Fix Gate 与 Verifier 决策**：检查修复建议安全性，高风险项必须具备可复现证据。

关键实现入口：[`evoagent/harness.py`](evoagent/harness.py)、[`evoagent/agents.py`](evoagent/agents.py)、[`evoagent/models.py`](evoagent/models.py)。

## 快速开始

项目使用 Python 3.11。以下命令启动默认本地规则模式，`provider_calls=0`：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt

$env:EVOAGENT_AUTH_SECRET = '<至少 32 字节随机值>'
$env:EVOAGENT_BOOTSTRAP_ADMIN_USERNAME = 'admin'
$env:EVOAGENT_BOOTSTRAP_ADMIN_PASSWORD = '<至少 10 个字符的密码>'

.\.venv\Scripts\python -m evoagent
```

服务默认监听 `127.0.0.1:8080`。打开 `http://127.0.0.1:8080/` 使用管理员账号登录，或在另一个终端提交首个同步 Review：

```powershell
$session = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/auth/login `
  -ContentType 'application/json' `
  -Body (@{ username='admin'; password='<你的密码>' } | ConvertTo-Json)

$headers = @{ Authorization = "Bearer $($session.access_token)" }
$diff = "diff --git a/app.py b/app.py`n--- a/app.py`n+++ b/app.py`n@@ -1 +1,2 @@`n+password = 'secret'`n+eval(user_input)"

$task = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/reviews `
  -Headers $headers -ContentType 'application/json' `
  -Body (@{ repository='demo/api'; pull_request=12; diff=$diff } | ConvertTo-Json)

$task
```

运行离线测试：

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

## 输入与输出

### 支持的输入

- Unified Diff：通过 JSON API 创建同步或异步审查任务。
- GitHub `pull_request` Webhook：支持 `opened`、`reopened`、`synchronize`。
- 白名单 Git 工作区：支持 `workspace`、`range`、`commit`，客户端只提交项目别名。
- 可组合 Reviewer：本地规则、OpenAI-compatible 模型和 Alibaba OpenCodeReview 1.8.5。

### Finding 契约

每条 Finding 包含：

```json
{
  "rule_id": "SEC-EVAL",
  "severity": "critical",
  "path": "app.py",
  "line": 2,
  "evidence": "eval(user_input)",
  "fix": "使用受约束的解析器替代动态执行",
  "test": "加入恶意输入回归测试",
  "source": "local-rules",
  "provenance": [{ "source": "local-rules" }]
}
```

JSON API 与 Markdown 报告使用同一结构化结果；任务同时保留状态迁移、Agent 消息、checkpoint 和审计记录。

### Rook 受控修复闭环

[Rook](https://github.com/Lem0nTea2002/Rook) 可以通过受控 HTTP 客户端读取本服务的 Finding，再把用户选中的问题交给本地 Coding Agent。该集成默认保持 Review Agent 侧只读，由 Rook 侧执行本地确认和修复。

## 能力地图

| 领域 | 能力 |
|---|---|
| Review | Unified Diff 解析；多 Agent 协作；本地、模型、OCR 多来源 Finding 合并；JSON / Markdown 报告 |
| Harness | 任务状态机；步数与时长预算；LangGraph 编排；持久化 checkpoint；取消与断点续跑 |
| Reliability | Redis Streams ACK；Worker 租约；指数退避；死信队列；Webhook delivery 幂等与重放窗口 |
| Governance | JWT；RBAC；租户/仓库隔离；HMAC Webhook 校验；不可变管理审计；Skill manifest 与签名校验 |
| Repair | 独立修复分支；保守规则；编译/测试门禁；灰度与影子流量配置；版本激活与回滚 |
| Evaluation | 版本化 train/validation/holdout；新旧版本回放；受保护指标；数据与提示词 SHA-256 指纹 |
| Operations | Web Dashboard；Prometheus；OpenTelemetry；持久化告警；SQLite 或 PostgreSQL；进程内队列或 Redis |

## 评测门禁

提示词候选使用同一批 Diff 与当前版本进行回放，计算 Precision、Recall、F1、严重级别正确率、高风险召回率、干净样本准确率和执行成功率。调用失败按漏报或失败的干净样本计入指标。

候选激活需要同时满足：

1. validation 达到最小提升；
2. holdout 的分数、Precision、Recall 和高风险召回率满足非退化约束；
3. validation 与 holdout 样本数量达到下限；
4. 运行记录、数据集指纹和提示词指纹完成持久化；
5. 激活权限与生产门禁显式允许。

缺少模型配置或有效样本时，候选保存为 `deferred`。隐藏集只持久化聚合指标，样本修订使用新名称，重复提交相同内容保持幂等。

相关变量：

| 变量 | 用途 |
|---|---|
| `EVOAGENT_EVAL_MIN_CASES` | validation 最少样本数 |
| `EVOAGENT_EVAL_MIN_HOLDOUT_CASES` | holdout 最少样本数 |
| `EVOAGENT_EVAL_MAX_CASES` | 每个数据分区单次最大回放数 |
| `EVOAGENT_EVAL_MIN_IMPROVEMENT` | validation 最小提升 |
| `EVOAGENT_EVAL_MAX_METRIC_REGRESSION` | 受保护指标最大允许退化，默认 `0` |

## 配置与集成

<details>
<summary><strong>模型 Provider</strong></summary>

默认 `EVOAGENT_LLM_PROVIDER=local`，仅执行确定性本地规则 Agent。

```powershell
# DeepSeek 官方 API
$env:EVOAGENT_LLM_PROVIDER = 'deepseek'
$env:EVOAGENT_DEEPSEEK_API_KEY = '<deepseek-api-key>'

# OpenRouter 免费路由
$env:EVOAGENT_LLM_PROVIDER = 'openrouter-deepseek-free'
$env:EVOAGENT_OPENROUTER_API_KEY = '<openrouter-api-key>'

# 任意 OpenAI Chat Completions 兼容端点
$env:EVOAGENT_LLM_PROVIDER = 'custom'
$env:EVOAGENT_LLM_BASE_URL = 'https://example.com/v1'
$env:EVOAGENT_LLM_API_KEY = '<token>'
$env:EVOAGENT_LLM_MODEL = '<model-name>'
```

密钥只通过环境变量读取。OpenRouter 免费模型具有速率和可用性限制。

</details>

<details>
<summary><strong>白名单工作区与 OpenCodeReview</strong></summary>

服务端白名单文件将项目别名映射到绝对路径，客户端只提交已登记的项目别名：

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

# 只预览仓库解析与文件边界
node $env:EVOAGENT_OCR_COMMAND review --repo D:\path\to\repo --preview
```

工作区 API 支持 `workspace`、`range` 和 `commit`；`reviewers` 可选择 `local`、`ocr` 或两者。OCR 被显式请求但不可用、版本不符或输出非法时返回稳定错误码。合并后的 Finding 保留 `source`、行区间、原代码、建议代码和 `provenance`。OpenCodeReview 是独立安装的 Apache-2.0 第三方组件。

</details>

<details>
<summary><strong>GitHub Webhook 与评论回写</strong></summary>

Webhook 地址为 `POST /webhooks/github`，事件选择 **Pull requests**：

```powershell
$env:EVOAGENT_GITHUB_WEBHOOK_SECRET = '<webhook-secret>'
$env:EVOAGENT_GITHUB_TOKEN = '<fine-grained-token>'
```

默认只返回审查结果。评论回写需要显式启用：

```powershell
$env:EVOAGENT_AUTO_POST_REVIEW = 'true'
```

Token 在只读模式需要目标仓库 Pull requests 读权限，评论回写模式需要写权限。Webhook delivery 提供幂等处理、重放时间窗和评论 upsert。

GitHub App 配置入口：

- Setup URL：`<公网地址>/github/setup`
- Webhook URL：`<公网地址>/webhooks/github`
- Webhook event：Pull request
- Repository permissions：Contents `Read & write`、Pull requests `Read & write`、Metadata `Read-only`

自动修复只覆盖可确定安全的规则，提交进入新的 `evoagent/fix-pr-*` 分支，源分支保持不变。

</details>

<details>
<summary><strong>PostgreSQL、Redis 与可观测性</strong></summary>

本地默认使用 SQLite 和进程内队列。设置以下变量可切换持久化数据库、队列和 Trace 导出：

```powershell
$env:EVOAGENT_DATABASE_URL = 'postgresql://user:password@127.0.0.1:5432/evoagent'
$env:EVOAGENT_REDIS_URL = 'redis://127.0.0.1:6379/0'
$env:EVOAGENT_OTEL_ENDPOINT = 'http://127.0.0.1:4318'
```

仓库同时提供 `Dockerfile` 与 `docker-compose.yml`。完整变量及默认值见 [`.env.example`](.env.example)。

</details>

## API 概览

| 领域 | 端点 |
|---|---|
| 健康与观测 | `GET /health`、`GET /metrics`、`GET /api/alerts`、`GET /api/audit` |
| 鉴权 | `POST /v1/auth/login` |
| Review | `POST /v1/reviews`、`POST /v1/reviews/workspace`、`GET /v1/tasks/{id}`、`GET /v1/tasks/{id}/report` |
| 生命周期 | `POST /v1/tasks/{id}/cancel`、`POST /v1/tasks/{id}/resume`、`POST /v1/tasks/{id}/feedback` |
| 修复 | `POST /v1/tasks/{id}/fix` |
| Evaluation | `GET/POST /v1/evaluation/cases`、`POST /v1/evolution/auto`、`POST /v1/evolution/propose`、`GET /v1/evolution/runs` |
| Skill | `POST /v1/skills/reload`、`POST /v1/skills/{name}/versions/{version}/activate` |
| Queue | `GET /api/queue/dead-letters`、`POST /v1/queue/dead-letters/replay` |
| GitHub | `POST /webhooks/github` |

`POST /v1/reviews` 的 Diff 默认上限为 1 MiB；单任务默认最多 8 步、120 秒。所有限制均可在 [`.env.example`](.env.example) 中查看。
