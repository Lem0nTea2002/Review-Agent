const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const titles = {
  overview: "运行总览",
  review: "发起审查",
  tasks: "任务中心",
  skills: "Skill 注册中心",
  evolution: "演进实验室",
  github: "GitHub App",
};

const stateLabels = {
  PENDING: "等待中",
  PLANNING: "规划中",
  EXECUTING: "执行中",
  REVIEWING: "汇总中",
  SUCCESS: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};

let selectedTask = null;
let accessToken = localStorage.getItem("evoagent_token") || "";
let toastTimer = null;
let activeReviewTask = null;
let reviewPollTimer = null;
let reviewStartedAt = null;

const terminalStates = new Set(["SUCCESS", "FAILED", "CANCELLED"]);
const stageProgress = {
  PENDING: { label: "等待调度", progress: 8 },
  PLANNING: { label: "解析变更", progress: 28 },
  EXECUTING: { label: "多 Agent 审查", progress: 62 },
  REVIEWING: { label: "证据复核与汇总", progress: 88 },
  SUCCESS: { label: "审查完成", progress: 100 },
  FAILED: { label: "执行失败", progress: 100 },
  CANCELLED: { label: "已取消", progress: 100 },
};

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function formatTime(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      }).format(date);
}

function formatJson(value) {
  return JSON.stringify(value, null, 2);
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function formatDuration(start, end = Date.now()) {
  const started = start instanceof Date ? start.getTime() : new Date(start || 0).getTime();
  const finished = end instanceof Date ? end.getTime() : new Date(end || Date.now()).getTime();
  if (!started || Number.isNaN(started) || Number.isNaN(finished)) return "耗时未知";
  const seconds = Math.max(0, Math.round((finished - started) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function parseDiff(diff) {
  const text = String(diff || "").replace(/\r\n/g, "\n");
  const lines = text ? text.split("\n") : [];
  const files = [];
  let current = null;
  let added = 0;
  let removed = 0;
  let oldLine = 0;
  let newLine = 0;
  const rendered = [];

  lines.forEach((line, index) => {
    if (line.startsWith("+++ ")) {
      const rawPath = line.slice(4).trim().split("\t")[0];
      const path = rawPath.replace(/^b\//, "");
      if (path !== "/dev/null") {
        current = { path, added: 0, removed: 0 };
        files.push(current);
      }
    }
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
    }

    let kind = "context";
    let lineNumber = "";
    if (line.startsWith("+") && !line.startsWith("+++")) {
      kind = "added";
      lineNumber = newLine || "";
      newLine += 1;
      added += 1;
      if (current) current.added += 1;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      kind = "removed";
      lineNumber = oldLine || "";
      oldLine += 1;
      removed += 1;
      if (current) current.removed += 1;
    } else if (line.startsWith("@@")) {
      kind = "hunk";
    } else if (line.startsWith("diff ") || line.startsWith("---") || line.startsWith("+++")) {
      kind = "header";
    } else if (oldLine || newLine) {
      lineNumber = newLine || oldLine;
      oldLine += 1;
      newLine += 1;
    }
    if (index < 1200) rendered.push({ kind, lineNumber, text: line });
  });

  const valid = files.length > 0
    && /^--- /m.test(text)
    && /^\+\+\+ /m.test(text)
    && /^@@ /m.test(text)
    && (added + removed > 0);
  return { text, lines, files, added, removed, rendered, valid, truncated: lines.length > 1200 };
}

function renderDiffWorkspace() {
  const input = $("#diff-input");
  if (!input) return;
  const parsed = parseDiff(input.value);
  const bytes = new TextEncoder().encode(parsed.text).length;
  $("#diff-stats").textContent = `${parsed.lines.length} 行 · ${formatBytes(bytes)}`;
  $("#file-count").textContent = parsed.files.length;
  $("#added-count").textContent = parsed.added;
  $("#removed-count").textContent = parsed.removed;
  $("#review-files").innerHTML = parsed.files.length
    ? parsed.files.map((file, index) => `
        <button class="file-row ${index === 0 ? "active" : ""}" type="button" data-file-path="${escapeAttribute(file.path)}">
          <span><i>PY</i><b>${escapeHtml(file.path)}</b></span>
          <em><ins>+${file.added}</ins><del>-${file.removed}</del></em>
        </button>`).join("")
    : '<div class="pane-empty">粘贴或上传 Diff 后，这里会列出变更文件。</div>';

  $("#diff-preview").innerHTML = parsed.rendered.length
    ? parsed.rendered.map((line) => `
        <div class="diff-line ${line.kind}"><span>${escapeHtml(line.lineNumber)}</span><code>${escapeHtml(line.text) || "&nbsp;"}</code></div>`).join("")
        + (parsed.truncated ? '<div class="diff-truncated">预览仅显示前 1200 行，提交时仍会发送完整 Diff。</div>' : "")
    : '<div class="pane-empty">暂无可预览内容。</div>';

  const validation = $("#diff-validation");
  validation.className = `validation ${parsed.text ? (parsed.valid ? "valid" : "invalid") : "neutral"}`;
  validation.innerHTML = parsed.text
    ? (parsed.valid ? "<i></i>Unified Diff 格式有效" : "<i></i>缺少文件头、变更块或增删行")
    : "<i></i>等待输入 Unified Diff";
}

function setEditorMode(mode) {
  const preview = mode === "preview";
  $("#diff-input").classList.toggle("hidden", preview);
  $("#diff-preview").classList.toggle("hidden", !preview);
  $$("[data-editor-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.editorMode === mode);
  });
  if (preview) renderDiffWorkspace();
}

function severityLabel(value) {
  return { critical: "严重", high: "高危", medium: "中危", low: "低危" }[String(value).toLowerCase()] || "提示";
}

function findingHtml(finding, index) {
  const severity = String(finding.severity || "low").toLowerCase();
  const start = finding.start_line ?? finding.line ?? 0;
  const end = finding.end_line ?? finding.line ?? start;
  const location = start === end ? `L${start}` : `L${start}–${end}`;
  const source = finding.source || finding.provenance?.[0]?.source || "unknown";
  const confidence = Math.round(Number(finding.confidence || 0) * 100);
  return `<article class="finding-card severity-${escapeHtml(severity)}">
    <header>
      <span class="severity-badge">${severityLabel(severity)}</span>
      <span class="finding-source">${escapeHtml(source)}</span>
      <span class="finding-confidence">${confidence}%</span>
    </header>
    <h4>${escapeHtml(finding.title || finding.rule_id || `Finding ${index + 1}`)}</h4>
    <button class="finding-location" type="button" data-finding-path="${escapeAttribute(finding.path || "")}">
      ${escapeHtml(finding.path || "未知文件")} · ${escapeHtml(location)}
    </button>
    <p>${escapeHtml(finding.explanation || "暂无问题说明")}</p>
    <details>
      <summary>查看证据与修复建议</summary>
      <dl>
        <div><dt>证据</dt><dd><code>${escapeHtml(finding.evidence || "未提供")}</code></dd></div>
        <div><dt>建议</dt><dd>${escapeHtml(finding.fix || "未提供")}</dd></div>
        <div><dt>验证</dt><dd>${escapeHtml(finding.test || "未提供")}</dd></div>
      </dl>
    </details>
  </article>`;
}

function reportHtml(task) {
  const state = String(task?.state || "PENDING").toUpperCase();
  const report = task?.report;
  if (state === "FAILED") {
    return `<div class="review-message error"><b>审查执行失败</b><span>${escapeHtml(task.error || "未返回错误详情")}</span></div>`;
  }
  if (state === "CANCELLED") {
    return '<div class="review-message cancelled"><b>任务已取消</b><span>任务已在安全检查点停止。</span></div>';
  }
  if (!report) {
    const stage = stageProgress[state] || stageProgress.PENDING;
    return `<div class="review-message running"><span class="review-spinner"></span><b>${stage.label}</b><span>任务 ${escapeHtml(task?.id || "").slice(0, 8)} 正在执行</span></div>`;
  }
  const findings = report.findings || [];
  if (!findings.length) {
    return `<div class="review-message clean"><b>未发现可执行问题</b><span>${escapeHtml(report.summary || "所有质量门禁均已通过。")}</span></div>`;
  }
  return findings.map(findingHtml).join("");
}

function traceHtml(task) {
  const trace = task?.trace || [];
  if (!trace.length) return '<div class="pane-empty">当前还没有执行轨迹。</div>';
  return trace.map((event) => `
    <div class="trace-row state-${escapeHtml(String(event.state || "").toLowerCase())}">
      <i></i><span><b>${escapeHtml(stateLabels[event.state] || event.state)}</b><small>${escapeHtml(event.message || "")}</small></span>
      <time>${escapeHtml(formatTime(event.created_at))}</time>
    </div>`).join("");
}

function collaborationHtml(task) {
  const messages = task?.collaboration || [];
  if (!messages.length) return '<div class="pane-empty">当前还没有 Agent 交接记录。</div>';
  return messages.map((message) => `
    <div class="audit-row">
      <span><b>${escapeHtml(message.sender || "agent")}</b><i>→</i><b>${escapeHtml(message.recipient || "report")}</b></span>
      <em>${escapeHtml(message.kind || "message")}</em>
      <small>${escapeHtml(formatTime(message.created_at))}</small>
    </div>`).join("");
}

function renderPipeline(task) {
  const state = String(task?.state || "PENDING").toUpperCase();
  const order = { PENDING: -1, PLANNING: 0, EXECUTING: 1, REVIEWING: 4, SUCCESS: 5 };
  const reached = order[state] ?? -1;
  $$("#review-pipeline span[data-stage]").forEach((node, index) => {
    node.classList.toggle("done", state === "SUCCESS" || index < reached);
    node.classList.toggle("active", !terminalStates.has(state) && index === Math.max(0, reached));
    node.classList.toggle("failed", state === "FAILED" && index === Math.max(0, reached));
  });
  $("#review-pipeline").style.setProperty("--progress", `${(stageProgress[state] || stageProgress.PENDING).progress}%`);
}

function renderReviewTask(task) {
  activeReviewTask = task?.id || task?.task_id || activeReviewTask;
  const normalized = { ...task, id: task?.id || task?.task_id || activeReviewTask };
  const state = String(normalized.state || "PENDING").toUpperCase();
  const report = normalized.report;
  const risk = String(report?.risk || (state === "FAILED" ? "critical" : "neutral")).toLowerCase();
  const stage = stageProgress[state] || stageProgress.PENDING;
  const findingCount = report?.findings?.length || 0;
  $("#review-task-meta").innerHTML = `
    <span class="risk-orb ${escapeHtml(risk)}">${report ? findingCount : "…"}</span>
    <span><b>${escapeHtml(stage.label)}</b><small>${report ? `${findingCount} 个 Finding · 风险 ${escapeHtml(risk)}` : `任务 ${escapeHtml(normalized.id || "").slice(0, 8)}`}</small></span>`;
  $("#review-result").classList.remove("empty");
  $("#review-result").innerHTML = reportHtml(normalized);
  $("#review-trace").innerHTML = traceHtml(normalized);
  $("#review-audit").innerHTML = collaborationHtml(normalized);
  renderPipeline(normalized);
  const started = normalized.created_at || reviewStartedAt;
  const ended = terminalStates.has(state) ? normalized.updated_at : Date.now();
  $("#review-runtime").textContent = `${stage.label} · ${formatDuration(started, ended)}`;
  $("#cancel-review").classList.toggle("hidden", terminalStates.has(state) || normalized.cancel_requested);
  $("#resume-review").classList.toggle("hidden", state !== "FAILED");
}

function renderTaskReport(task) {
  const report = task?.report;
  const state = String(task?.state || "PENDING").toUpperCase();
  const findings = report?.findings || [];
  const summary = report
    ? `<div class="task-report-summary">
        <span class="risk-orb ${escapeHtml(String(report.risk || "neutral").toLowerCase())}">${findings.length}</span>
        <span><b>${escapeHtml(report.summary || "审查完成")}</b><small>${escapeHtml(report.repository || task.repository || "")} · ${escapeHtml(stateLabels[state] || state)}</small></span>
      </div>`
    : `<div class="task-report-summary"><span class="risk-orb neutral">…</span><span><b>${escapeHtml(stateLabels[state] || state)}</b><small>${escapeHtml(task.error || "任务尚未生成报告")}</small></span></div>`;
  return summary + (report ? reportHtml(task) : traceHtml(task));
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("json") ? await response.json() : await response.text();

  if (response.status === 401) {
    $("#login-overlay").classList.remove("hidden");
    $("#logout").classList.add("hidden");
  }
  if (!response.ok) {
    const message = typeof data === "object" ? data.error || data.detail : data;
    throw new Error(message || response.statusText || "请求失败");
  }
  return data;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 2600);
}

function setButtonBusy(button, busy, busyText) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.textContent = busyText;
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

function show(view, updateHash = true) {
  if (!titles[view]) view = "overview";
  $$(".view").forEach((element) => element.classList.remove("active"));
  $$(".nav-item").forEach((element) => {
    const active = element.dataset.view === view;
    element.classList.toggle("active", active);
    element.setAttribute("aria-current", active ? "page" : "false");
  });
  $(`#view-${view}`).classList.add("active");
  $("#page-title").textContent = titles[view];
  document.title = `${titles[view]} · Review Agent`;
  if (updateHash) history.replaceState(null, "", `#${view}`);

  if (view === "tasks") loadTasks();
  if (view === "skills") loadSkills();
  if (view === "evolution") loadFailures();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$$(".nav-item").forEach((button) => button.addEventListener("click", () => show(button.dataset.view)));
$$("[data-jump]").forEach((button) => button.addEventListener("click", () => show(button.dataset.jump)));
window.addEventListener("hashchange", () => show(location.hash.slice(1), false));

function taskRows(tasks) {
  if (!tasks?.length) {
    return '<div class="empty-state"><span><b>还没有审查任务</b>提交一个 Diff 开始首次审查</span></div>';
  }
  return tasks.map((task) => {
    const state = String(task.state || "PENDING").toUpperCase();
    const repository = escapeHtml(task.repository || "未命名仓库");
    const pr = task.pull_request ? `PR #${escapeHtml(task.pull_request)}` : "手动审查";
    return `
      <button class="task-row" data-task="${escapeHtml(task.id)}" type="button">
        <span class="task-main">
          <span class="task-glyph">PR</span>
          <span class="task-copy">
            <span class="task-name">${repository}</span>
            <span class="task-meta"><span>${pr}</span><span>${escapeHtml(formatTime(task.created_at))}</span></span>
          </span>
        </span>
        <span class="status state-${state.toLowerCase()}">${stateLabels[state] || escapeHtml(state)}</span>
      </button>`;
  }).join("");
}

function bindTasks(root) {
  $$("[data-task]", root).forEach((row) => row.addEventListener("click", () => openTask(row.dataset.task)));
}

function stopReviewPolling() {
  clearTimeout(reviewPollTimer);
  reviewPollTimer = null;
}

async function fetchReviewTask(id, keepPolling = false) {
  try {
    const task = await api(`/v1/tasks/${encodeURIComponent(id)}`);
    renderReviewTask(task);
    if (keepPolling && !terminalStates.has(String(task.state).toUpperCase())) {
      reviewPollTimer = setTimeout(() => fetchReviewTask(id, true), 1200);
    } else if (terminalStates.has(String(task.state).toUpperCase())) {
      stopReviewPolling();
      loadDashboard();
      if (task.state === "SUCCESS") toast("审查完成，报告已生成");
    }
    return task;
  } catch (error) {
    stopReviewPolling();
    $("#review-result").classList.remove("empty");
    $("#review-result").innerHTML = `<div class="review-message error"><b>任务状态读取失败</b><span>${escapeHtml(error.message)}</span></div>`;
    throw error;
  }
}

function watchReviewTask(id) {
  stopReviewPolling();
  activeReviewTask = id;
  reviewStartedAt = new Date();
  fetchReviewTask(id, true).catch(() => {});
}

function statCard(label, value, note, style, icon) {
  return `<article class="stat ${style}">
    <div class="stat-head"><span>${label}</span><i>${icon}</i></div>
    <b>${value}</b><small>${note}</small>
  </article>`;
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    $("#system-status").textContent = `${data.queue} · ${data.orchestrator}`;
    const stats = data.stats || {};
    const rate = Math.round(Number(stats.success_rate || 0) * 100);
    $("#stats").innerHTML = [
      statCard("总任务", stats.tasks_total ?? 0, "累计审查任务", "", "Σ"),
      statCard("已完成", stats.tasks_success ?? 0, "通过质量门禁", "success", "✓"),
      statCard("失败", stats.tasks_failed ?? 0, "需要进一步处理", "failed", "!"),
      statCard("成功率", `${rate}%`, "全部任务成功率", "rate", "%"),
      statCard("待处理案例", stats.unresolved_failure_cases ?? 0, "未解决反馈", "feedback", "•"),
      statCard("活跃 Skills", stats.active_skill_versions ?? 0, "当前生效版本", "skills", "◇"),
    ].join("");
    $("#recent-tasks").innerHTML = taskRows((data.tasks || []).slice(0, 5));
    bindTasks($("#recent-tasks"));
  } catch (error) {
    $("#system-status").textContent = "服务连接异常";
    $("#stats").innerHTML = '<div class="empty-state"><span><b>暂时无法读取数据</b>请检查服务状态后重试</span></div>';
    $("#recent-tasks").innerHTML = '<div class="empty-state"><span>数据加载失败</span></div>';
    toast(error.message);
  }
}

async function loadTasks() {
  const root = $("#all-tasks");
  root.innerHTML = '<div class="list-loading"></div><div class="list-loading"></div>';
  try {
    const data = await api("/api/tasks");
    root.innerHTML = taskRows(data.tasks || []);
    bindTasks(root);
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><span>任务加载失败</span></div>';
    toast(error.message);
  }
}

async function openTask(id) {
  if (!$("#view-tasks").classList.contains("active")) show("tasks");
  selectedTask = null;
  $("#create-fix").classList.add("hidden");
  $("#task-report").innerHTML = '<div class="pane-empty">正在加载任务报告…</div>';
  try {
    const task = await api(`/v1/tasks/${encodeURIComponent(id)}`);
    selectedTask = id;
    $$("[data-task]").forEach((row) => row.classList.toggle("selected", row.dataset.task === id));
    $("#task-report").innerHTML = renderTaskReport(task);
    $("#create-fix").classList.toggle("hidden", !(task.report && task.pull_request));
  } catch (error) {
    $("#task-report").innerHTML = `<div class="review-message error"><b>任务加载失败</b><span>${escapeHtml(error.message)}</span></div>`;
  }
}

async function loadSkills() {
  const root = $("#skill-list");
  root.innerHTML = '<div class="skill-card loading"></div><div class="skill-card loading"></div>';
  try {
    const data = await api("/api/skills");
    const skills = data.skills || [];
    root.innerHTML = skills.length ? skills.map((skill) => `
      <article class="skill-card">
        <span class="skill-label">${skill.sandboxed ? "SANDBOXED SKILL" : "ACTIVE SKILL"}</span>
        <h3>${escapeHtml(skill.name)}</h3>
        <p>${escapeHtml(skill.description || "暂无能力描述")}</p>
        <span class="skill-meta"><i></i>v${escapeHtml(skill.version)} · ${escapeHtml(skill.source)}</span>
      </article>`).join("") : '<div class="empty-state"><span><b>尚未加载 Skill</b>扫描目录以加载可用能力</span></div>';
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><span>Skills 加载失败</span></div>';
    toast(error.message);
  }
}

async function loadFailures() {
  try {
    const [failuresData, status, runsData] = await Promise.all([
      api("/api/failures"),
      api("/v1/evolution/status"),
      api("/v1/evolution/runs?limit=5"),
    ]);
    $("#evolution-status").textContent = formatJson(status);
    const cases = failuresData.cases || [];
    const runs = runsData.runs || [];
    const failureHtml = cases.length
      ? cases.slice(0, 8).map((item) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">FC</span><span class="task-copy">
              <span class="task-name">${escapeHtml(item.category)}</span>
              <span class="task-meta">${escapeHtml(item.task_id)}</span>
            </span></span>
            <span class="status ${item.resolved ? "state-success" : "state-pending"}">${item.resolved ? "已解决" : "待处理"}</span>
          </div>`).join("")
      : '<div class="empty-state"><span><b>暂无失败反馈</b>系统当前没有未处理案例</span></div>';
    const historyHtml = runs.length
      ? `<p class="eyebrow" style="margin:20px 0 4px">RECENT EVALUATIONS</p>${runs.map((run) => `
          <div class="task-row">
            <span class="task-main"><span class="task-glyph">V${escapeHtml(run.candidate_version)}</span><span class="task-copy">
              <span class="task-name">${escapeHtml(run.decision)}</span>
              <span class="task-meta">${Number(run.candidate_score).toFixed(3)} vs ${Number(run.baseline_score).toFixed(3)}</span>
            </span></span>
          </div>`).join("")}`
      : "";
    $("#failure-list").innerHTML = failureHtml + historyHtml;
  } catch (error) {
    $("#evolution-status").textContent = "暂时无法读取评测状态。";
    $("#failure-list").innerHTML = '<div class="empty-state"><span>反馈加载失败</span></div>';
    toast(error.message);
  }
}

$("#diff-input").addEventListener("input", renderDiffWorkspace);
$$('[data-editor-mode]').forEach((button) => button.addEventListener("click", () => {
  setEditorMode(button.dataset.editorMode);
}));

$("#upload-diff").addEventListener("click", () => $("#diff-file").click());
$("#diff-file").addEventListener("change", async (event) => {
  const [file] = event.currentTarget.files || [];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    toast("Diff 文件超过 2 MB，请缩小后重试");
    event.currentTarget.value = "";
    return;
  }
  $("#diff-input").value = await file.text();
  setEditorMode("edit");
  renderDiffWorkspace();
  toast(`已载入 ${file.name}`);
});

$$('[data-review-tab]').forEach((button) => button.addEventListener("click", () => {
  const tab = button.dataset.reviewTab;
  $$("[data-review-tab]").forEach((item) => item.classList.toggle("active", item === button));
  $$("[data-review-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.reviewPanel === tab));
}));

$("#review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  const body = { repository: values.get("repository"), diff: values.get("diff") };
  const parsed = parseDiff(body.diff);
  if (!parsed.valid) {
    setEditorMode("edit");
    $("#diff-input").focus();
    toast("请提供包含文件头、变更块和增删行的 Unified Diff");
    return;
  }
  if (values.get("pull_request")) body.pull_request = Number(values.get("pull_request"));
  const asyncQuery = values.get("async") ? "?async=true" : "";
  const output = $("#review-result");
  output.classList.remove("empty");
  output.innerHTML = '<div class="review-message running"><span class="review-spinner"></span><b>正在创建审查任务</b><span>正在校验输入与仓库权限</span></div>';
  setButtonBusy(button, true, "正在提交…");
  stopReviewPolling();
  try {
    const data = await api(`/v1/reviews${asyncQuery}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    activeReviewTask = data.task_id;
    reviewStartedAt = new Date();
    renderReviewTask(data);
    if (data.report) {
      await fetchReviewTask(data.task_id, false);
    } else {
      watchReviewTask(data.task_id);
    }
    toast("审查任务已成功提交");
    loadDashboard();
  } catch (error) {
    output.innerHTML = `<div class="review-message error"><b>提交失败</b><span>${escapeHtml(error.message)}</span></div>`;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#cancel-review").addEventListener("click", async () => {
  if (!activeReviewTask) return;
  const button = $("#cancel-review");
  setButtonBusy(button, true, "正在取消…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(activeReviewTask)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!data.cancel_requested) throw new Error("任务不存在或已经结束");
    button.classList.add("hidden");
    toast("已请求在安全检查点取消任务");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#resume-review").addEventListener("click", async () => {
  if (!activeReviewTask) return;
  const button = $("#resume-review");
  setButtonBusy(button, true, "正在恢复…");
  try {
    await api(`/v1/tasks/${encodeURIComponent(activeReviewTask)}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    watchReviewTask(activeReviewTask);
    toast("任务已从最近检查点重新入队");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#create-fix").addEventListener("click", async () => {
  if (!selectedTask) return;
  const taskId = selectedTask;
  const installationIdInput = prompt("GitHub App installation_id（使用 PAT 可留空）", "");
  if (installationIdInput === null) return;
  const rawInstallationId = installationIdInput.trim();
  if (rawInstallationId && !/^\d+$/.test(rawInstallationId)) {
    toast("installation_id 必须是正整数");
    return;
  }
  const installationId = rawInstallationId ? Number(rawInstallationId) : null;
  const button = $("#create-fix");
  setButtonBusy(button, true, "正在创建…");
  try {
    const data = await api(`/v1/tasks/${encodeURIComponent(taskId)}/fix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ installation_id: installationId }),
    });
    if (selectedTask === taskId) {
      $("#task-report").innerHTML = `<div class="review-message clean"><b>修复分支已创建</b><span>${escapeHtml(data.branch || data.url || formatJson(data))}</span></div>`;
    }
    toast("修复分支已创建");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#reload-skills").addEventListener("click", async () => {
  const button = $("#reload-skills");
  setButtonBusy(button, true, "正在扫描…");
  try {
    await api("/v1/skills/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await loadSkills();
    toast("Skills 已重新加载");
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#evolution-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在评测…");
  try {
    const data = await api("/v1/evolution/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: values.get("skill_name"), prompt: values.get("prompt") }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("新旧版本回放评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#auto-evolve").addEventListener("click", async () => {
  const button = $("#auto-evolve");
  setButtonBusy(button, true, "正在生成…");
  try {
    const data = await api("/v1/evolution/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_name: "llm-review" }),
    });
    $("#evolution-result").classList.remove("empty");
    $("#evolution-result").textContent = formatJson(data);
    toast("反馈候选评测已完成");
    loadFailures();
  } catch (error) {
    toast(error.message);
  } finally {
    setButtonBusy(button, false);
  }
});

$("#refresh").addEventListener("click", async () => {
  const view = location.hash.slice(1) || "overview";
  if (view === "overview") await loadDashboard();
  else if (view === "review" && activeReviewTask) await fetchReviewTask(activeReviewTask, false);
  else if (view === "review") renderDiffWorkspace();
  else if (view === "tasks") await loadTasks();
  else if (view === "skills") await loadSkills();
  else if (view === "evolution") await loadFailures();
  else await loadDashboard();
  toast("数据已刷新");
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form);
  const values = new FormData(form);
  setButtonBusy(button, true, "正在登录…");
  try {
    const data = await api("/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: values.get("username"),
        password: values.get("password"),
        tenant_id: values.get("tenant_id"),
      }),
    });
    accessToken = data.access_token;
    localStorage.setItem("evoagent_token", accessToken);
    $("#login-overlay").classList.add("hidden");
    $("#logout").classList.remove("hidden");
    $("#login-error").textContent = "";
    await loadDashboard();
  } catch (error) {
    $("#login-error").textContent = error.message;
  } finally {
    setButtonBusy(button, false);
  }
});

$("#logout").addEventListener("click", () => {
  accessToken = "";
  localStorage.removeItem("evoagent_token");
  $("#login-overlay").classList.remove("hidden");
  $("#logout").classList.add("hidden");
});

if (accessToken) $("#logout").classList.remove("hidden");
renderDiffWorkspace();
show(location.hash.slice(1) || "overview", false);
loadDashboard();
