"use strict";

const DIALECT_LABELS = {
  db2: "DB2",
  postgres: "PostgreSQL",
  oracle: "Oracle",
  mysql: "MySQL",
  dameng: "达梦 DM",
};

const DEFAULT_TARGETS = {
  mysql: "oracle",
  postgres: "mysql",
  oracle: "postgres",
  db2: "dameng",
  dameng: "db2",
};

const EXAMPLES = {
  mysql: `-- 最近消费最高的用户（最近 30 天）
SELECT
  u.id,
  u.user_name,
  IFNULL(SUM(o.amount), 0) AS total_amount,
  DATE_FORMAT(u.created_at, '%Y-%m-%d') AS created_date
FROM users AS u
LEFT JOIN orders AS o ON o.user_id = u.id
WHERE u.status = 1
  AND u.created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY u.id, u.user_name, u.created_at
ORDER BY total_amount DESC
LIMIT 10;`,

  postgres: `-- 近 30 天有订单的用户
WITH recent_orders AS (
  SELECT user_id, SUM(amount) AS total_amount
  FROM orders
  WHERE created_at >= NOW() - INTERVAL '30 days'
  GROUP BY user_id
)
SELECT
  u.id,
  u.email,
  COALESCE(r.total_amount, 0) AS total_amount,
  ROW_NUMBER() OVER (ORDER BY r.total_amount DESC) AS row_no
FROM users AS u
LEFT JOIN recent_orders AS r ON r.user_id = u.id
ORDER BY total_amount DESC NULLS LAST
LIMIT 20;`,

  oracle: `-- 用户订单汇总
SELECT
  u.id,
  u.user_name,
  NVL(SUM(o.amount), 0) AS total_amount,
  TO_CHAR(u.created_at, 'YYYY-MM-DD') AS created_date
FROM users u
LEFT JOIN orders o ON o.user_id = u.id
WHERE u.status = 1
GROUP BY u.id, u.user_name, u.created_at
ORDER BY total_amount DESC
FETCH FIRST 10 ROWS ONLY;`,

  db2: `-- Db2：按用户汇总近 30 天订单
SELECT
  u.id,
  u.user_name,
  COALESCE(SUM(o.amount), 0) AS total_amount,
  VARCHAR_FORMAT(u.created_at, 'YYYY-MM-DD') AS created_date
FROM users AS u
LEFT JOIN orders AS o ON o.user_id = u.id
WHERE u.status = 1
GROUP BY u.id, u.user_name, u.created_at
ORDER BY total_amount DESC
FETCH FIRST 10 ROWS ONLY
WITH UR;`,

  dameng: `-- 达梦 DM8：用户订单汇总
SELECT
  u.id,
  u.user_name,
  NVL(SUM(o.amount), 0) AS total_amount,
  TO_CHAR(u.created_at, 'YYYY-MM-DD') AS created_date
FROM users u
LEFT JOIN orders o ON o.user_id = u.id
WHERE u.status = 1
GROUP BY u.id, u.user_name, u.created_at
ORDER BY total_amount DESC
LIMIT 10;`,
};

const el = {
  workspace: document.getElementById("workspace"),
  sourceSelect: document.getElementById("source-select"),
  targetSelect: document.getElementById("target-select"),
  sourceBadge: document.getElementById("source-badge"),
  targetBadge: document.getElementById("target-badge"),
  sourceInput: document.getElementById("source-input"),
  sourceGutter: document.getElementById("source-gutter"),
  sourceCount: document.getElementById("source-count"),
  outputCode: document.getElementById("output-code"),
  outputGutter: document.getElementById("output-gutter"),
  outputCount: document.getElementById("output-count"),
  outputEmpty: document.getElementById("output-empty"),
  emptyTitle: document.getElementById("empty-title"),
  emptyHint: document.getElementById("empty-hint"),
  timing: document.getElementById("timing"),
  swapButton: document.getElementById("swap-button"),
  sampleButton: document.getElementById("sample-button"),
  clearButton: document.getElementById("clear-button"),
  prettyToggle: document.getElementById("pretty-toggle"),
  convertButton: document.getElementById("convert-button"),
  convertLabel: document.getElementById("convert-label"),
  convertSpinner: document.getElementById("convert-spinner"),
  copyButton: document.getElementById("copy-button"),
  copyLabel: document.getElementById("copy-label"),
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  notice: document.getElementById("notice"),
  noticeList: document.getElementById("notice-list"),
  errorPanel: document.getElementById("error-panel"),
  errorTitle: document.getElementById("error-title"),
  errorMessage: document.getElementById("error-message"),
  errorHint: document.getElementById("error-hint"),
  errorLocation: document.getElementById("error-location"),
  toast: document.getElementById("toast"),
};

const state = {
  busy: false,
  hasOutput: false,
  outputText: "",
  lastConvertedInput: "",
  lastSample: "",
  requestId: 0,
  toastTimer: null,
  copyTimer: null,
};

function countLines(text) {
  if (!text) return 0;
  return text.split("\n").length;
}

function formatNumber(value) {
  return Number(value).toLocaleString("zh-CN");
}

function updateGutter(gutter, lines) {
  gutter.textContent = Array.from({ length: Math.max(1, lines) }, (_, index) => index + 1).join("\n");
}

function syncDialectLabels() {
  const source = el.sourceSelect.value;
  const target = el.targetSelect.value;
  el.sourceBadge.textContent = DIALECT_LABELS[source] || source;
  el.targetBadge.textContent = DIALECT_LABELS[target] || target;
  document.title = `${DIALECT_LABELS[source] || source} → ${DIALECT_LABELS[target] || target} · SQLVIP`;
}

function updateEditorMetrics() {
  const text = el.sourceInput.value;
  const lines = countLines(text);
  el.sourceCount.textContent = `${formatNumber(lines)} 行 · ${formatNumber(text.length)} 字符`;
  updateGutter(el.sourceGutter, lines);
  el.sourceGutter.scrollTop = el.sourceInput.scrollTop;
}

function setStatus(text, kind = "") {
  el.statusText.textContent = text;
  el.statusDot.classList.remove("success", "error", "stale");
  if (kind) {
    el.statusDot.classList.add(kind);
  }
}

function markStale() {
  if (!state.hasOutput) return;
  if (el.sourceInput.value !== state.lastConvertedInput) {
    setStatus("SQL 已修改，待转换", "stale");
    el.timing.textContent = "";
  } else {
    setStatus("转换完成", "success");
  }
}

function setLoading(loading) {
  state.busy = loading;
  el.convertButton.disabled = loading;
  el.convertButton.classList.toggle("is-loading", loading);
  el.convertLabel.textContent = loading ? "转换中…" : "转换 SQL";
  el.sourceSelect.disabled = loading;
  el.targetSelect.disabled = loading;
  el.swapButton.disabled = loading;
  el.sampleButton.disabled = loading;
  el.clearButton.disabled = loading;
  el.prettyToggle.disabled = loading;
  el.workspace.setAttribute("aria-busy", String(loading));
}

function hideNotice() {
  el.notice.hidden = true;
  el.noticeList.replaceChildren();
}

function hideError() {
  el.errorPanel.hidden = true;
  el.errorHint.hidden = true;
  el.errorHint.textContent = "";
  el.errorLocation.hidden = true;
  el.errorLocation.textContent = "";
}

function setEmptyState(title, hint) {
  el.emptyTitle.textContent = title;
  el.emptyHint.textContent = hint;
  el.outputEmpty.hidden = false;
}

function clearOutput({ failed = false } = {}) {
  state.hasOutput = false;
  state.outputText = "";
  state.lastConvertedInput = "";
  el.outputCode.textContent = "";
  updateGutter(el.outputGutter, 0);
  el.outputGutter.scrollTop = 0;
  el.copyButton.disabled = true;
  el.copyLabel.textContent = "复制";
  el.timing.textContent = "";
  el.outputCode.scrollTop = 0;

  if (failed) {
    el.outputCount.textContent = "未生成结果";
    setEmptyState("转换未完成", "请根据提示修正 SQL 后重试");
    setStatus("转换失败", "error");
  } else {
    el.outputCount.textContent = "等待转换";
    setEmptyState("转换结果", "粘贴 SQL 后点击「转换 SQL」");
    setStatus("准备就绪");
  }
}

function clearAll() {
  if (state.busy) return;
  el.sourceInput.value = "";
  state.lastSample = "";
  hideError();
  hideNotice();
  clearOutput();
  updateEditorMetrics();
  el.sourceInput.focus();
}

function renderNotes(notes) {
  el.noticeList.replaceChildren();
  if (!Array.isArray(notes) || notes.length === 0) {
    el.notice.hidden = true;
    return;
  }

  for (const note of notes) {
    const item = document.createElement("li");
    item.textContent = note;
    el.noticeList.appendChild(item);
  }
  el.notice.hidden = false;
}

function normalizeApiError(payload, status) {
  if (payload && payload.error) {
    return { ...payload.error };
  }

  const detail = payload ? payload.detail : null;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
      return location ? `${location}: ${item.msg}` : item.msg;
    });
    return {
      code: "VALIDATION_ERROR",
      message: "请求参数不完整或格式不正确。",
      hint: messages.filter(Boolean).join("\n"),
    };
  }

  if (detail && typeof detail === "object") {
    return { ...detail };
  }

  if (typeof detail === "string") {
    return { code: "API_ERROR", message: detail };
  }

  return {
    code: "API_ERROR",
    message: `转换服务返回错误（HTTP ${status}）。`,
  };
}

function normalizeUnknownError(error) {
  if (error instanceof TypeError) {
    return {
      code: "NETWORK_ERROR",
      message: "无法连接 SQLVIP 服务，请确认服务正在运行。",
      hint: "在项目目录执行：python run.py",
    };
  }
  if (error && typeof error === "object" && error.message) {
    return error;
  }
  return {
    code: "NETWORK_ERROR",
    message: "转换请求失败，请稍后重试。",
    hint: String(error || ""),
  };
}

function showError(error) {
  const payload = normalizeUnknownError(error);
  const code = payload.code || "CONVERSION_ERROR";

  el.errorTitle.textContent =
    code === "PARSE_ERROR"
      ? "SQL 解析失败"
      : code === "NETWORK_ERROR"
        ? "无法连接服务"
        : "转换失败";
  el.errorMessage.textContent = payload.message || "转换失败，请稍后重试。";

  if (payload.hint) {
    el.errorHint.textContent = payload.hint;
    el.errorHint.hidden = false;
  } else {
    el.errorHint.hidden = true;
    el.errorHint.textContent = "";
  }

  if (payload.line) {
    const location = payload.column
      ? `位置：第 ${payload.line} 行，第 ${payload.column} 列`
      : `位置：第 ${payload.line} 行`;
    el.errorLocation.textContent = location;
    el.errorLocation.hidden = false;
  } else {
    el.errorLocation.hidden = true;
    el.errorLocation.textContent = "";
  }

  el.errorPanel.hidden = false;
}

function applyResult(data, inputSnapshot) {
  state.hasOutput = true;
  state.outputText = data.sql;
  state.lastConvertedInput = inputSnapshot;

  el.outputCode.textContent = data.sql;
  const lines = countLines(data.sql);
  updateGutter(el.outputGutter, lines);
  el.outputGutter.scrollTop = 0;
  el.outputCode.scrollTop = 0;
  el.outputEmpty.hidden = true;
  el.outputCount.textContent = `${formatNumber(data.statement_count)} 条语句 · ${formatNumber(lines)} 行 · ${formatNumber(data.sql.length)} 字符`;
  el.timing.textContent = `${formatNumber(data.elapsed_ms)} ms`;
  el.copyButton.disabled = false;
  el.copyLabel.textContent = "复制";
  hideError();
  renderNotes(data.notes);
  markStale();
}

async function convertSql() {
  if (state.busy) return;

  const sql = el.sourceInput.value;
  if (!sql.trim()) {
    hideNotice();
    clearOutput();
    showError({
      code: "EMPTY_SQL",
      message: "请输入需要转换的 SQL。",
      hint: "也可以点击「载入示例」快速体验。",
    });
    el.sourceInput.focus();
    return;
  }

  hideError();
  hideNotice();
  setLoading(true);
  setStatus("正在转换…");
  const requestId = ++state.requestId;

  try {
    const response = await fetch("/api/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sql,
        source: el.sourceSelect.value,
        target: el.targetSelect.value,
        pretty: el.prettyToggle.checked,
      }),
    });

    const payload = await response.json().catch(() => null);
    if (requestId !== state.requestId) return;

    if (!response.ok) {
      throw normalizeApiError(payload, response.status);
    }
    if (!payload || payload.ok !== true) {
      throw normalizeApiError(payload, response.status);
    }

    applyResult(payload, sql);
  } catch (error) {
    if (requestId !== state.requestId) return;
    clearOutput({ failed: true });
    showError(error);
  } finally {
    if (requestId === state.requestId) {
      setLoading(false);
    }
  }
}

function loadExample(resetTarget = true) {
  if (state.busy) return;
  const source = el.sourceSelect.value;
  if (resetTarget) {
    el.targetSelect.value = DEFAULT_TARGETS[source] || "oracle";
  }

  const example = EXAMPLES[source] || "";
  el.sourceInput.value = example;
  state.lastSample = example;
  state.lastConvertedInput = "";
  hideError();
  hideNotice();
  syncDialectLabels();
  updateEditorMetrics();
  void convertSql();
}

function swapDialects() {
  if (state.busy) return;

  const previousSource = el.sourceSelect.value;
  const previousTarget = el.targetSelect.value;
  const hadOutput = state.hasOutput && Boolean(state.outputText);

  el.sourceSelect.value = previousTarget;
  el.targetSelect.value = previousSource;

  if (hadOutput) {
    el.sourceInput.value = state.outputText;
    state.lastSample = "";
    state.lastConvertedInput = "";
  }

  syncDialectLabels();
  updateEditorMetrics();
  hideError();
  hideNotice();

  if (!el.sourceInput.value.trim()) {
    clearOutput();
    return;
  }

  void convertSql();
}

function toast(message) {
  el.toast.textContent = message;
  el.toast.classList.add("show");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    el.toast.classList.remove("show");
  }, 1900);
}

function fallbackCopy(text) {
  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "readonly");
  helper.style.position = "fixed";
  helper.style.left = "-9999px";
  helper.style.top = "0";
  document.body.appendChild(helper);
  helper.select();
  const copied = document.execCommand("copy");
  helper.remove();
  if (!copied) {
    throw new Error("copy failed");
  }
}

async function copyOutput() {
  if (!state.outputText) return;

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(state.outputText);
    } else {
      fallbackCopy(state.outputText);
    }
    el.copyLabel.textContent = "已复制";
    window.clearTimeout(state.copyTimer);
    state.copyTimer = window.setTimeout(() => {
      el.copyLabel.textContent = "复制";
    }, 1400);
    toast("目标 SQL 已复制到剪贴板");
  } catch {
    toast("复制失败，请手动选择目标 SQL");
  }
}

function handleSourceChange() {
  syncDialectLabels();
  const current = el.sourceInput.value;
  if (!current.trim() || current === state.lastSample) {
    loadExample(true);
    return;
  }

  state.lastSample = "";
  if (state.hasOutput) {
    markStale();
  } else {
    setStatus("准备就绪");
  }
}

function handleTargetChange() {
  syncDialectLabels();
  if (state.hasOutput && !state.busy) {
    void convertSql();
  }
}

function handleInput() {
  updateEditorMetrics();
  markStale();
}

function handleEditorKeydown(event) {
  if (event.key !== "Tab" || event.shiftKey || event.ctrlKey || event.metaKey) {
    return;
  }

  event.preventDefault();
  const start = el.sourceInput.selectionStart;
  const end = el.sourceInput.selectionEnd;
  el.sourceInput.setRangeText("  ", start, end, "end");
  updateEditorMetrics();
  markStale();
}

function handleGlobalKeydown(event) {
  if (event.isComposing) return;
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    void convertSql();
  }
}

function bindEvents() {
  el.sourceSelect.addEventListener("change", handleSourceChange);
  el.targetSelect.addEventListener("change", handleTargetChange);
  el.swapButton.addEventListener("click", swapDialects);
  el.sampleButton.addEventListener("click", () => loadExample(true));
  el.clearButton.addEventListener("click", clearAll);
  el.prettyToggle.addEventListener("change", () => {
    if (state.hasOutput && !state.busy) {
      void convertSql();
    }
  });
  el.convertButton.addEventListener("click", () => void convertSql());
  el.copyButton.addEventListener("click", () => void copyOutput());
  el.sourceInput.addEventListener("input", handleInput);
  el.sourceInput.addEventListener("keydown", handleEditorKeydown);
  el.sourceInput.addEventListener("scroll", () => {
    el.sourceGutter.scrollTop = el.sourceInput.scrollTop;
  });
  el.outputCode.addEventListener("scroll", () => {
    el.outputGutter.scrollTop = el.outputCode.scrollTop;
  });
  document.addEventListener("keydown", handleGlobalKeydown);
}

function init() {
  bindEvents();
  syncDialectLabels();
  loadExample(true);
}

document.addEventListener("DOMContentLoaded", init);
