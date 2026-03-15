const healthPill = document.getElementById("healthPill");
const summaryBox = document.getElementById("summaryBox");
const riskBox = document.getElementById("riskBox");
const discoverResults = document.getElementById("discoverResults");
const reviewResults = document.getElementById("reviewResults");
const positionsOpenBox = document.getElementById("positionsOpenBox");
const positionsHistoryBox = document.getElementById("positionsHistoryBox");
const pnlBox = document.getElementById("pnlBox");
const pnlSummaryBox = document.getElementById("pnlSummaryBox");
const autoBox = document.getElementById("autoBox");
const autoSlotsBox = document.getElementById("autoSlotsBox");
const autoStatusNote = document.getElementById("autoStatusNote");
const autoDecisionBox = document.getElementById("autoDecisionBox");
const autoProfileBox = document.getElementById("autoProfileBox");
const settingsBox = document.getElementById("settingsBox");
const settingsNotice = document.getElementById("settingsNotice");
const dashboardPositions = document.getElementById("dashboardPositions");
const dashboardAutoSlots = document.getElementById("dashboardAutoSlots");
const dashboardAutoProfile = document.getElementById("dashboardAutoProfile");
const reviewSelection = document.getElementById("reviewSelection");

let latestReviewPayload = null;
let refreshTimers = [];
let latestPositionsById = new Map();
let latestPositionsByContract = new Map();
let currentReviewAsset = { symbol: "", name: "", contract: "" };
let formsBootstrapped = false;
let autoActionPending = "";
let autoMonitorTimer = null;
let autoMonitorInFlight = false;
const appDefaults = {
  defaultBudgetSol: 0.02,
  budgetSolMax: 0.1,
  defaultMode: "paper",
  riskMode: "normal",
  rankingType: "combined",
};

function authHeaders() {
  const token = localStorage.getItem("hms-api-token") || "local-dev-token";
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const url = method === "GET" ? `${path}${path.includes("?") ? "&" : "?"}_=${Date.now()}` : path;
  const response = await fetch(url, {
    cache: "no-store",
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || JSON.stringify(payload));
  }
  return payload;
}

function fmt(value, digits = 4) {
  if (value === null || value === undefined || value === "") return "-";
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return num.toFixed(digits);
}

function fmtDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function formatCountdown(seconds) {
  const total = Math.max(0, Math.ceil(Number(seconds) || 0));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  if (mins > 0) {
    return `${mins}m ${String(secs).padStart(2, "0")}s`;
  }
  return `${secs}s`;
}

function badge(text) {
  return `<span class="badge">${text || "-"}</span>`;
}

function metric(label, value, cls = "") {
  return `<div class="metric"><div class="metric-label">${label}</div><div class="metric-value ${cls}">${value}</div></div>`;
}

function statusBanner(status, message) {
  const normalized = status || "info";
  const cls = ["success", "failed", "error", "pending"].includes(normalized) ? normalized : "info";
  const titleMap = {
    success: "Trade Success",
    failed: "Trade Failed",
    error: "Request Error",
    pending: "Waiting For Final Status",
    info: "Review Status",
  };
  return `
    <div class="status-banner ${cls}">
      <div class="status-banner-title">${titleMap[cls]}</div>
      <div class="status-banner-text">${message}</div>
    </div>
  `;
}

function renderMetrics(container, items) {
  container.innerHTML = items.map((item) => metric(item.label, item.value, item.cls || "")).join("");
}

function renderTable(rows, columns, emptyMessage = "No data") {
  if (!rows || !rows.length) {
    return `<div class="empty">${emptyMessage}</div>`;
  }
  const head = columns.map((column) => `<th>${column.label}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((column) => `<td>${column.render(row)}</td>`).join("")}</tr>`).join("");
  return `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function rankingLabel(value) {
  const labels = {
    combined: "combined",
    hotpicks: "hotpicks",
    top_gainers: "top_gainers",
  };
  return labels[value] || value || "-";
}

function humanizeRiskReason(value) {
  const text = String(value || "");
  const exact = {
    "security.highRisk": "High risk flag from security API",
    "security.cannotSellAll": "Token may not be fully sellable",
    "security.tax>10": "Buy or sell tax is too high",
    "security.tax>5": "Buy or sell tax is elevated",
    "security.freezeAuth": "Freeze authority is still enabled",
    "security.mintAuth": "Mint authority is still enabled",
    "lp.unlocked": "Liquidity pool is not clearly locked",
    "holders.top10": "Top holders are too concentrated",
    "holders.top10.elevated": "Top holder concentration is elevated",
    "holders.insider": "Insider allocation is too high",
    "holders.insider.elevated": "Insider allocation is elevated",
    "holders.sniper": "Sniper allocation is too high",
    "holders.sniper.elevated": "Sniper allocation is elevated",
    "dev.holder>limit": "Developer wallet allocation is too high",
    "dev.rugHistory": "Developer rug history is too risky",
    "community.lowSocials": "Social presence looks weak",
    "smartMoney.concentrated": "Buy flow is concentrated in too few wallets",
    "flow.sellPressure": "Recent flow shows heavy sell pressure",
    "Multi-ranking candidate": "Appears across multiple official rankings",
    "Strong liquidity": "Liquidity is strong for this strategy mode",
    "Healthy holder base": "Holder count is healthy",
    "Positive buy pressure": "Recent flow favors buyers",
    "Visible social presence": "Project has visible social channels",
    "Best remaining score": "Best score among the remaining candidates",
  };
  if (exact[text]) {
    return exact[text];
  }
  if (text.startsWith("liquidity<")) {
    return `Liquidity is below the required threshold (${text.replace("liquidity<", "")} USD).`;
  }
  if (text.startsWith("holders<")) {
    return `Holder count is below the required threshold (${text.replace("holders<", "")}).`;
  }
  if (text.startsWith("sources<")) {
    return `Not enough official ranking sources matched this token (${text.replace("sources<", "")} required).`;
  }
  if (text.startsWith("security.riskCount=")) {
    return `Security API reported multiple risk flags (${text.replace("security.riskCount=", "")}).`;
  }
  return text.replaceAll("_", " ");
}

function humanizeReasonList(items) {
  return (items || []).map((item) => humanizeRiskReason(item));
}

function humanizeAutoAction(value) {
  const text = String(value || "");
  const exact = {
    started: "Auto started",
    paused: "Auto paused",
    paused_rate_limited: "Paused by rate limit protection",
    paused_breaker: "Paused by circuit breaker",
    paused_api_errors: "Paused after repeated API errors",
    paused_network: "Paused by upstream network instability",
    paused_daily_loss_limit: "Paused by daily loss limit",
    paused_consecutive_losses: "Paused after consecutive losses",
    buy_guard_daily_loss: "Waiting for the daily-loss guard to clear",
    buy_guard_consecutive_losses: "Waiting for the consecutive-loss guard to clear",
    crashed: "Stopped after an unexpected error",
    slot_limit_reached: "Slot limit reached",
    reserve_floor_reached: "Reserve floor reached",
    budget_exhausted: "Deployable budget exhausted",
    discover_no_candidate: "No eligible candidate found",
    discover_retry_wait: "Retrying discover after a rate limit",
    discover_retry_exhausted: "Skipped this discover cycle after repeated rate limits",
    candidate_blocked: "Top candidate blocked by risk checks",
    positions_refreshed: "Refreshing open positions",
    quote_retry_wait: "Retrying quote after a rate limit",
    submitted_buy: "Submitted buy order",
    buy_success: "Buy completed",
    waiting_for_order_finality: "Waiting for order finality",
    waiting_for_free_slot: "Waiting for a free slot",
    waiting_for_budget_release: "Waiting for deployable budget",
    monitoring_existing_positions: "Monitoring existing positions",
    waiting_for_next_discovery: "Waiting for the next discover cycle",
    scanning_rankings: "Scanning official rankings",
    idle: "Idle",
  };
  if (exact[text]) {
    return exact[text];
  }
  if (text.startsWith("recovered_")) {
    return `Recovered pending order with status ${text.replace("recovered_", "")}`;
  }
  if (text.startsWith("sell_")) {
    const reason = text.replace(/^sell_/, "");
    const sellReasons = {
      stop_loss: "Sold due to stop loss",
      recover_cost_basis: "Sold enough to recover initial cost basis",
      take_profit_half: "Sold half position to take profit",
      leave_moonbag: "Reduced to moonbag size after a strong move",
      time_exit: "Exited because the holding window expired",
    };
    return sellReasons[reason] || `Sold due to ${reason.replaceAll("_", " ")}`;
  }
  return text ? text.replaceAll("_", " ") : "-";
}

function setButtonsBusy(buttons, busy, busyLabelMap = new Map()) {
  buttons.filter(Boolean).forEach((button) => {
    if (!button) return;
    if (busy) {
      if (!button.dataset.originalLabel) {
        button.dataset.originalLabel = button.textContent || "";
      }
      button.disabled = true;
      button.textContent = busyLabelMap.get(button) || button.dataset.originalLabel;
      return;
    }
    button.disabled = false;
    if (button.dataset.originalLabel) {
      button.textContent = button.dataset.originalLabel;
    }
  });
}

function syncAutoButtons(autoStatus = {}) {
  const startButton = document.getElementById("startAutoButton");
  const stopButton = document.getElementById("stopAutoButton");
  if (!startButton || !stopButton) return;

  const running = Boolean(autoStatus.runtimeAlive ?? autoStatus.running);
  const pendingStart = autoActionPending === "start";
  const pendingStop = autoActionPending === "stop";

  if (pendingStart) {
    startButton.disabled = true;
    stopButton.disabled = true;
    startButton.textContent = "Starting...";
    stopButton.textContent = "Stop Auto";
    return;
  }
  if (pendingStop) {
    startButton.disabled = true;
    stopButton.disabled = true;
    startButton.textContent = "Start Auto";
    stopButton.textContent = "Stopping...";
    return;
  }

  startButton.textContent = "Start Auto";
  stopButton.textContent = "Stop Auto";
  startButton.disabled = running;
  stopButton.disabled = !running;
}

function percentText(value) {
  if (value === null || value === undefined || value === "") return "-";
  return `${fmt(Number(value) * 100, 1)}%`;
}

function takeProfitStageLabel(value) {
  const labels = {
    entry: "Entry",
    cost_basis_recovered: "Cost basis recovered",
    half_taken: "Half take-profit done",
    moonbag: "Moonbag only",
  };
  return labels[value] || value || "-";
}

function autoStatusExplanation(autoStatus) {
  if (autoStatus.nextStepMessage) {
    const clsMap = {
      paused_rate_limited: "pending",
      paused_network: "error",
      paused_daily_loss_limit: "failed",
      paused_consecutive_losses: "failed",
      buy_guard_daily_loss: "info",
      buy_guard_consecutive_losses: "info",
      waiting_for_order_finality: "pending",
      waiting_for_free_slot: "info",
      waiting_for_budget_release: "info",
      monitoring_existing_positions: "info",
      waiting_for_next_discovery: "info",
      scanning_rankings: "info",
      idle: "info",
    };
    return { cls: clsMap[autoStatus.nextStep] || "info", text: autoStatus.nextStepMessage };
  }
  if (autoStatus.pausedReason === "rate_limited") {
    return { cls: "pending", text: "Auto paused because the upstream API hit rate limits. Existing positions stay visible, but new buys wait until requests stabilize." };
  }
  if (autoStatus.pausedReason === "network_unstable" || autoStatus.pausedReason === "api_errors_burst") {
    return { cls: "error", text: "Auto paused because the upstream network became unstable. The agent stops opening new positions until connectivity is healthy again." };
  }
  if (autoStatus.lastAction === "daily_loss_limit_guard") {
    return { cls: "info", text: "The daily-loss guard is active. Auto stays on and will keep managing positions, but it will wait before opening a new buy." };
  }
  if (autoStatus.lastAction === "max_consecutive_losses_guard") {
    return { cls: "info", text: "The consecutive-loss guard is active. Auto remains enabled and will keep managing positions, but new buys are temporarily blocked." };
  }
  if (autoStatus.lastAction === "slot_limit_reached") {
    return { cls: "info", text: `All portfolio slots are occupied (${autoStatus.slotsUsed || 0}/${autoStatus.slotsMax || 0}). Auto is managing existing positions instead of opening a new one.` };
  }
  if (autoStatus.lastAction === "reserve_floor_reached") {
    return { cls: "info", text: `The configured reserve is protecting ${fmt(autoStatus.reserveSolBalance, 3)} SOL, so there is no deployable budget left for a fresh buy.` };
  }
  if (autoStatus.lastAction === "budget_exhausted") {
    return { cls: "info", text: "The deployable auto budget is fully used by current positions. Free budget or close a slot before opening another trade." };
  }
  if (autoStatus.lastAction === "discover_no_candidate") {
    return { cls: "info", text: `No candidate passed the current ${autoStatus.riskMode || "normal"} filter from ${rankingLabel(autoStatus.rankingType)}.` };
  }
  if (autoStatus.lastAction === "discover_retry_wait") {
    return { cls: "pending", text: "Discover hit a temporary rate limit. Auto is retrying this cycle once before moving on." };
  }
  if (autoStatus.lastAction === "discover_retry_exhausted") {
    return { cls: "info", text: "Discover stayed rate limited after one retry, so this cycle was skipped and auto will try again on the next loop." };
  }
  if (autoStatus.lastAction === "candidate_blocked") {
    return { cls: "failed", text: "The top ranked candidate was blocked by risk checks, so auto skipped the buy." };
  }
  if (autoStatus.lastAction === "quote_retry_wait") {
    return { cls: "pending", text: "The quote endpoint was rate limited. Auto is waiting briefly and retrying once before entering cooldown." };
  }
  if (autoStatus.lastAction === "positions_refreshed") {
    return { cls: "info", text: "Auto is refreshing current positions and monitoring exit thresholds." };
  }
  if (String(autoStatus.lastAction || "").startsWith("sell_")) {
    return { cls: "success", text: `${humanizeAutoAction(autoStatus.lastAction)}.` };
  }
  if (autoStatus.lastAction === "buy_success" || autoStatus.lastAction === "submitted_buy") {
    return { cls: "success", text: "Auto has an active buy flow in progress or just completed one successfully." };
  }
  if (autoStatus.running) {
    return { cls: "info", text: "Auto is running and will keep scanning official rankings for the next allowed opportunity." };
  }
  return { cls: "info", text: "Auto is idle. Start it when you want the agent to manage ranked candidates automatically." };
}

function terminalStatus(status) {
  return ["success", "failed", "refunded"].includes(status || "");
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function clearAutoMonitor() {
  if (autoMonitorTimer) {
    window.clearTimeout(autoMonitorTimer);
    autoMonitorTimer = null;
  }
}

function shouldMonitorAuto(autoStatus = {}) {
  return Boolean(autoStatus.running || autoStatus.cooldownActive);
}

async function pollAutoRuntime() {
  if (autoMonitorInFlight) return;
  autoMonitorInFlight = true;
  try {
    await Promise.all([refreshSummary(), refreshPositions(), refreshPnl(), refreshAuto()]);
  } finally {
    autoMonitorInFlight = false;
  }
}

function scheduleAutoMonitor(autoStatus = {}) {
  clearAutoMonitor();
  if (!shouldMonitorAuto(autoStatus)) {
    return;
  }
  const delay = autoStatus.cooldownActive ? 1000 : 4000;
  autoMonitorTimer = window.setTimeout(async () => {
    await pollAutoRuntime();
  }, delay);
}

function formatSummary(summary) {
  const latestTrade = summary.latestTrade || {};
  const latestPnl = summary.latestPnl || {};
  const autoStatus = summary.autoStatus || {};
  const slotsUsed = Number(autoStatus.slotsUsed ?? autoStatus.openPositions ?? 0);
  const slotsMax = Number(autoStatus.slotsMax ?? 0);
  const slotLabel = slotsMax > 0 ? `${slotsUsed} / ${slotsMax}` : String(slotsUsed);
  renderMetrics(summaryBox, [
    { label: "Mode", value: summary.settings?.defaultMode || "-" },
    { label: "Default Ranking", value: rankingLabel(summary.settings?.rankingType) },
    { label: "Default Risk Mode", value: summary.settings?.riskMode || "-" },
    { label: "Latest Side", value: latestTrade.side || "-" },
    { label: "Latest Token", value: latestTrade.token_symbol || "-" },
    { label: "Latest Status", value: latestTrade.status || "-" },
    { label: "Realized SOL", value: fmt(latestPnl.realized, 6), cls: Number(latestPnl.realized) >= 0 ? "good" : "bad" },
    { label: "Unrealized SOL", value: fmt(latestPnl.unrealized, 6), cls: Number(latestPnl.unrealized) >= 0 ? "good" : "bad" },
    { label: "Total SOL", value: fmt(latestPnl.total, 6), cls: Number(latestPnl.total) >= 0 ? "good" : "bad" },
    { label: "Open Positions", value: summary.positions?.length ?? 0 },
    { label: "Auto Slots", value: slotLabel },
    { label: "Auto Available", value: fmt(autoStatus.availableBudgetSol, 3), cls: Number(autoStatus.availableBudgetSol) > 0 ? "good" : "bad" },
  ]);
  dashboardPositions.innerHTML = renderTable(
    summary.positions || [],
    [
      { label: "Token", render: (row) => row.token_symbol || "-" },
      { label: "Amount", render: (row) => fmt(row.amount, 4) },
      { label: "Entry", render: (row) => fmt(row.entry_price_sol, 6) },
      { label: "Current", render: (row) => fmt(row.current_price_sol, 6) },
      { label: "Value", render: (row) => fmt(row.market_value_sol, 6) },
      { label: "Stage", render: (row) => badge(row.take_profit_stage || "-") },
    ],
    "No open positions"
  );
  formatSlotCards(dashboardAutoSlots, autoStatus.slotPositions || [], "Dashboard auto slots will appear here after auto status loads.");
  formatDashboardStrategySnapshot(autoStatus.strategyProfile || {});
}

function formatAuto(autoStatus) {
  const slotsUsed = Number(autoStatus.slotsUsed ?? autoStatus.openPositions ?? 0);
  const slotsMax = Number(autoStatus.slotsMax ?? 0);
  const slotLabel = slotsMax > 0 ? `${slotsUsed} / ${slotsMax}` : String(slotsUsed);
  const cooldownActive = Boolean(autoStatus.cooldownActive);
  const cooldownRemaining = Number(autoStatus.cooldownRemainingSec ?? 0);
  syncAutoButtons(autoStatus);
  renderMetrics(riskBox, [
    { label: "Auto Running", value: autoStatus.running ? "Yes" : "No", cls: autoStatus.running ? "good" : "" },
    { label: "Next Step", value: humanizeAutoAction(autoStatus.nextStep || autoStatus.lastAction) },
    { label: "Cooldown", value: cooldownActive ? formatCountdown(cooldownRemaining) : "-", cls: cooldownActive ? "bad" : "" },
    { label: "Risk Mode", value: autoStatus.riskMode || "-" },
    { label: "Ranking Filter", value: rankingLabel(autoStatus.rankingType) },
    { label: "Pause Reason", value: autoStatus.pausedReason || "-" },
    { label: "Today PnL", value: fmt(autoStatus.todayPnlSol, 6), cls: Number(autoStatus.todayPnlSol) >= 0 ? "good" : "bad" },
    { label: "Portfolio Slots", value: slotLabel },
    { label: "Reserve SOL", value: fmt(autoStatus.reserveSolBalance, 3) },
    { label: "Available Budget", value: fmt(autoStatus.availableBudgetSol, 3), cls: Number(autoStatus.availableBudgetSol) > 0 ? "good" : "bad" },
  ]);
  renderMetrics(autoBox, [
    { label: "Status", value: autoStatus.running ? "AUTO LIVE ENABLED" : "Stopped", cls: autoStatus.running ? "good" : "" },
    { label: "Next Step", value: humanizeAutoAction(autoStatus.nextStep || autoStatus.lastAction) },
    { label: "Cooldown Remaining", value: cooldownActive ? formatCountdown(cooldownRemaining) : "-", cls: cooldownActive ? "bad" : "" },
    { label: "Ranking Filter", value: rankingLabel(autoStatus.rankingType) },
    { label: "Risk Mode", value: autoStatus.riskMode || "-" },
    { label: "Last Action", value: humanizeAutoAction(autoStatus.lastAction) },
    { label: "Pause Reason", value: autoStatus.pausedReason || "-" },
    { label: "Open Positions", value: autoStatus.openPositions ?? 0 },
    { label: "Slots Used", value: slotLabel },
    { label: "Budget", value: fmt(autoStatus.budgetSol, 3) },
    { label: "After Reserve", value: fmt(autoStatus.deployableBudgetSol, 3) },
    { label: "Deployed", value: fmt(autoStatus.deployedBudgetSol, 3) },
    { label: "Next Slot Cap", value: fmt(autoStatus.nextSlotBudgetSol, 3), cls: Number(autoStatus.nextSlotBudgetSol) > 0 ? "good" : "" },
    { label: "Consecutive Losses", value: autoStatus.consecutiveLosses ?? 0, cls: Number(autoStatus.consecutiveLosses) > 0 ? "bad" : "" },
    { label: "Last Order", value: autoStatus.lastOrderId || "-" },
    { label: "Last Tx", value: autoStatus.lastTxId || "-" },
    { label: "Realized", value: fmt(autoStatus.realizedPnlSol, 6), cls: Number(autoStatus.realizedPnlSol) >= 0 ? "good" : "bad" },
    { label: "Unrealized", value: fmt(autoStatus.unrealizedPnlSol, 6), cls: Number(autoStatus.unrealizedPnlSol) >= 0 ? "good" : "bad" },
  ]);
  if (autoStatusNote) {
    const note = autoStatusExplanation(autoStatus);
    autoStatusNote.innerHTML = statusBanner(note.cls, note.text);
  }
  formatStrategyProfile(autoStatus.strategyProfile || {});
  formatAutoDecision(autoStatus.decisionSnapshot || {});
  formatSlotCards(autoSlotsBox, autoStatus.slotPositions || [], "No slot data yet.");
  scheduleAutoMonitor(autoStatus);
}

function formatStrategyProfile(profile) {
  if (!autoProfileBox) return;
  if (!profile || !Object.keys(profile).length) {
    autoProfileBox.innerHTML = `<div class="empty">No strategy profile available yet.</div>`;
    return;
  }
  const baseMinLiquidity = Number(profile.baseMinLiquidityUsd ?? profile.minLiquidityUsd);
  const effectiveMinLiquidity = Number(profile.minLiquidityUsd);
  renderMetrics(autoProfileBox, [
    { label: "Risk Mode", value: profile.riskMode || "-" },
    { label: "Base Min Liquidity", value: `$${fmt(baseMinLiquidity, 0)}` },
    {
      label: "Effective Min Liquidity",
      value: `$${fmt(effectiveMinLiquidity, 0)}`,
      cls: effectiveMinLiquidity !== baseMinLiquidity ? "good" : "",
    },
    { label: "Max Slots", value: profile.maxOpenPositions ?? "-" },
    { label: "Slot Budget", value: percentText(profile.slotBudgetFraction) },
    { label: "Stop Loss", value: percentText(profile.stopLossPct) },
    { label: "Cost Basis Take Profit", value: percentText(profile.takeProfitCostBasisPct) },
    { label: "Half Take Profit", value: percentText(profile.takeProfitHalfPct) },
    { label: "Moonbag Trigger", value: percentText(profile.moonbagTriggerPct) },
    { label: "Moonbag Size", value: percentText(profile.moonbagFraction) },
    { label: "Max Hold", value: `${fmt(profile.maxHoldHours, 1)}h` },
    { label: "Time Exit Gain Cap", value: percentText(profile.timeExitMaxGainPct) },
    { label: "Min Holders", value: fmt(profile.minHolders, 0) },
    { label: "Min Social Links", value: fmt(profile.minSocialLinks, 0) },
    { label: "Min Ranking Sources", value: fmt(profile.minSourceCount, 0) },
    { label: "Max Top10 Holders", value: percentText((profile.maxTop10HolderPercent || 0) / 100) },
    { label: "Max Insider", value: percentText((profile.maxInsiderHolderPercent || 0) / 100) },
    { label: "Max Sniper", value: percentText((profile.maxSniperHolderPercent || 0) / 100) },
    { label: "Max Dev Allocation", value: percentText((profile.maxDevHolderPercent || 0) / 100) },
    { label: "Max Dev Rug History", value: percentText((profile.maxDevRugPercent || 0) / 100) },
  ]);
}

function formatDashboardStrategySnapshot(profile) {
  if (!dashboardAutoProfile) return;
  if (!profile || !Object.keys(profile).length) {
    dashboardAutoProfile.innerHTML = `<div class="empty">No strategy snapshot available yet.</div>`;
    return;
  }
  const baseMinLiquidity = Number(profile.baseMinLiquidityUsd ?? profile.minLiquidityUsd);
  const effectiveMinLiquidity = Number(profile.minLiquidityUsd);
  renderMetrics(dashboardAutoProfile, [
    { label: "Risk Mode", value: profile.riskMode || "-" },
    { label: "Slots", value: profile.maxOpenPositions ?? "-" },
    { label: "Base Min Liquidity", value: `$${fmt(baseMinLiquidity, 0)}` },
    { label: "Effective Min Liquidity", value: `$${fmt(effectiveMinLiquidity, 0)}` },
    { label: "Stop Loss", value: percentText(profile.stopLossPct) },
    { label: "Recover Cost Basis", value: percentText(profile.takeProfitCostBasisPct) },
    { label: "Half Take Profit", value: percentText(profile.takeProfitHalfPct) },
    { label: "Moonbag Trigger", value: percentText(profile.moonbagTriggerPct) },
    { label: "Max Hold", value: `${fmt(profile.maxHoldHours, 1)}h` },
  ]);
}

function renderReasonList(items, emptyMessage = "None") {
  if (!items || !items.length) {
    return `<div class="slot-subtitle">${emptyMessage}</div>`;
  }
  return `<ul class="decision-list">${humanizeReasonList(items).map((item) => `<li>${item}</li>`).join("")}</ul>`;
}

function decisionCard(item, kind = "shortlisted") {
  const blocked = kind === "blocked" || (item.blockedReasons && item.blockedReasons.length);
  return `
    <article class="decision-card ${blocked ? "blocked" : ""}">
      <div class="decision-head">
        <div>
          <div class="decision-title">${item.symbol || "-"}${item.selected ? " · Selected" : ""}</div>
          <div class="decision-subtitle">${item.name || "-"}${item.contract ? ` · ${item.contract}` : ""}</div>
        </div>
        ${badge(`score ${fmt(item.score, 2)}`)}
      </div>
      <div class="metric-grid">
        ${metric("Sources", (item.sources || []).join(", ") || "-")}
        ${metric("Status", blocked ? "Blocked" : "Eligible", blocked ? "bad" : "good")}
        ${metric("Narrative", fmt(item.narrativeScore, 2))}
        ${metric("Community", fmt(item.communityScore, 2))}
        ${metric("Smart Money", fmt(item.smartMoneyScore, 2))}
      </div>
      <div class="decision-section">
        <div class="decision-label">${blocked ? "Why blocked" : "Why chosen"}</div>
        ${renderReasonList(blocked ? item.whyBlocked : item.whyChosen, blocked ? "No explicit block reason." : "Best available candidate.")}
      </div>
      ${
        !blocked && item.warnings?.length
          ? `<div class="decision-section"><div class="decision-label">Warnings</div>${renderReasonList(item.warnings)}</div>`
          : ""
      }
    </article>
  `;
}

function formatAutoDecision(snapshot) {
  if (!autoDecisionBox) return;
  const selected = snapshot.selected;
  const blocked = snapshot.blocked || [];
  const shortlisted = (snapshot.shortlisted || []).filter((item) => !item.selected);
  if (!selected && !blocked.length && !shortlisted.length) {
    autoDecisionBox.innerHTML = `<div class="empty">No auto decision snapshot yet. Start auto or wait for the next discover cycle.</div>`;
    return;
  }
  const selectedBlock = selected
    ? `
      <div class="stack">
        <div class="panel-note"><strong>Decision Context:</strong> ${rankingLabel(snapshot.rankingType)} · ${snapshot.riskMode || "normal"} mode</div>
        ${decisionCard(selected, selected.blockedReasons?.length ? "blocked" : "shortlisted")}
      </div>
    `
    : `<div class="panel-note"><strong>Decision Context:</strong> ${rankingLabel(snapshot.rankingType)} · ${snapshot.riskMode || "normal"} mode</div>`;
  const shortlistedBlock = shortlisted.length
    ? `
      <div class="stack">
        <div class="decision-label">Other eligible candidates</div>
        <div class="decision-grid">${shortlisted.map((item) => decisionCard(item, "shortlisted")).join("")}</div>
      </div>
    `
    : "";
  const blockedBlock = blocked.length
    ? `
      <div class="stack">
        <div class="decision-label">Blocked candidates</div>
        <div class="decision-grid">${blocked.map((item) => decisionCard(item, "blocked")).join("")}</div>
      </div>
    `
    : "";
  autoDecisionBox.innerHTML = `<div class="stack">${selectedBlock}${shortlistedBlock}${blockedBlock}</div>`;
}

function formatSlotCards(container, slots, emptyMessage) {
  if (!container) return;
  if (!slots.length) {
    container.innerHTML = `<div class="empty">${emptyMessage}</div>`;
    return;
  }
  container.innerHTML = slots
    .map((slot) => {
      if (slot.state !== "open") {
        return `
          <article class="slot-card empty">
            <div class="slot-head">
              <div class="slot-token">Slot ${slot.slot}</div>
              ${badge("Empty")}
            </div>
            <div class="slot-subtitle">Ready for the next candidate if budget and reserve checks allow it.</div>
          </article>
        `;
      }
      const pnl = Number(slot.unrealizedPnlSol || 0);
      return `
        <article class="slot-card">
          <div class="slot-head">
            <div>
              <div class="slot-token">Slot ${slot.slot} · ${slot.tokenSymbol || "-"}</div>
              <div class="slot-subtitle">${slot.tokenContract || "-"}</div>
            </div>
            ${badge(takeProfitStageLabel(slot.takeProfitStage || "entry"))}
          </div>
          <div class="slot-stats">
            <div class="slot-stat">
              <div class="slot-stat-label">Amount</div>
              <div class="slot-stat-value">${fmt(slot.amount, 4)}</div>
            </div>
            <div class="slot-stat">
              <div class="slot-stat-label">Cost Basis</div>
              <div class="slot-stat-value">${fmt(slot.costBasisSol, 6)} SOL</div>
            </div>
            <div class="slot-stat">
              <div class="slot-stat-label">Market Value</div>
              <div class="slot-stat-value">${fmt(slot.marketValueSol, 6)} SOL</div>
            </div>
            <div class="slot-stat">
              <div class="slot-stat-label">Unrealized</div>
              <div class="slot-stat-value ${pnl >= 0 ? "good" : "bad"}">${fmt(slot.unrealizedPnlSol, 6)} SOL</div>
            </div>
          </div>
          <div class="slot-subtitle">Opened ${fmtDateTime(slot.openedAt)} · Stage: ${takeProfitStageLabel(slot.takeProfitStage || "entry")}</div>
        </article>
      `;
    })
    .join("");
}

function candidateCard(row) {
  const candidate = row.candidate || {};
  const contract = candidate.contract || "";
  const warnings = row.warnings?.length ? humanizeReasonList(row.warnings).join(", ") : "None";
  const blocked = row.blocked_reasons?.length ? humanizeReasonList(row.blocked_reasons).join(", ") : "No";
  const sources = row.sources?.length ? row.sources.join(", ") : "official";
  return `
    <article class="candidate-card">
      <div class="candidate-head">
        <div>
          <div class="candidate-title">${candidate.symbol || "-"}</div>
          <div class="candidate-subtitle">${candidate.name || "-"}${contract ? ` · ${contract}` : ""}</div>
        </div>
        ${badge(blocked === "No" ? "Ready" : "Blocked")}
      </div>
      <div class="candidate-meta">
        ${metric("Score", fmt(row.score, 2))}
        ${metric("Liquidity", `$${fmt(row.liquidity_usd, 0)}`)}
        ${metric("Holders", fmt(row.holders, 0))}
        ${metric("Sources", sources)}
      </div>
      <div class="candidate-line"><strong>Warnings:</strong> ${warnings}</div>
      <div class="candidate-line"><strong>Blocked:</strong> ${blocked}</div>
      <div class="candidate-actions">
        <button type="button" data-action="use-candidate" data-contract="${contract}" data-symbol="${candidate.symbol || ""}" data-name="${candidate.name || ""}">
          Use In Review
        </button>
      </div>
    </article>
  `;
}

function formatDiscover(data) {
  const sortedCandidates = [...(data.candidates || [])].sort((left, right) => Number(right.score || 0) - Number(left.score || 0));
  const recommended = data.recommended;
  const topBlock = recommended
    ? `<div class="panel-note"><strong>Recommended:</strong> ${recommended.candidate?.symbol || "-"} · score ${fmt(recommended.score, 2)} · sources ${recommended.sources?.join(", ") || "official"}</div>`
    : `<div class="empty">No recommended candidate yet.</div>`;
  const cards = sortedCandidates.length
    ? `<div class="candidate-list">${sortedCandidates.map((row) => candidateCard(row)).join("")}</div>`
    : `<div class="empty">No candidates</div>`;
  discoverResults.innerHTML = `<div class="stack"><div class="panel-note"><strong>Filter:</strong> ${rankingLabel(data.rankingType)}</div>${topBlock}${cards}</div>`;
}

function formatReview(data) {
  const amountLabel = data.side === "sell" ? "Token Amount" : "Amount";
  reviewResults.innerHTML = `
    <div class="stack">
      ${statusBanner("info", "Review the quote and execution mode before sending the order.")}
      <div class="metric-grid">
        ${metric("Mode", data.mode || "-")}
        ${metric("Side", data.side || "-")}
        ${metric(amountLabel, fmt(data.amount, 4))}
        ${metric("Market", data.quote?.market || "-")}
        ${metric("Estimated Output", fmt(data.quote?.toAmount, 4))}
        ${metric("Price Impact", `${fmt(data.quote?.priceImpact, 4)}%`, Number(data.quote?.priceImpact) > 3 ? "bad" : "")}
      </div>
      <div class="panel-note">
        <strong>Execution:</strong> ${
          data.mode === "paper"
            ? "Simulation only"
            : data.mode === "semi_auto_live"
              ? "Prepare in wallet"
              : "Auto signing with local key"
        }
      </div>
      ${
        data.handoff
          ? `<div class="panel-note"><strong>Handoff:</strong> wallet ${data.handoff.walletAddress || "-"} · market ${data.handoff.market || "-"} · feature ${data.handoff.feature || "normal_gas"}</div>`
          : ""
      }
    </div>
  `;
}

function formatExecution(data, note = "") {
  const statusClass = data.status === "success" ? "good" : data.status === "failed" ? "bad" : "";
  const bannerState = data.status === "success" ? "success" : data.status === "failed" ? "failed" : "pending";
  reviewResults.innerHTML = `
    <div class="stack">
      ${statusBanner(bannerState, note || "Order submitted. The UI will keep refreshing until data settles.")}
      <div class="metric-grid">
        ${metric("Mode", data.mode || "-")}
        ${metric("Status", data.status || "-", statusClass)}
        ${metric("Order ID", data.orderId || "-")}
        ${metric("Tx ID", data.txId || "-")}
        ${metric("Client Trade", data.clientTradeId || "-")}
      </div>
      ${
        data.handoff
          ? `<div class="panel-note"><strong>Wallet Action Required:</strong> ${data.handoff.message || "Sign and submit in your wallet, then return and refresh order status."}</div>`
          : ""
      }
    </div>
  `;
}

function formatPositions(rows) {
  latestPositionsById = new Map((rows || []).map((row) => [String(row.id), row]));
  latestPositionsByContract = new Map();
  (rows || []).forEach((row) => {
    if (row.status === "open" && !latestPositionsByContract.has(row.token_contract)) {
      latestPositionsByContract.set(row.token_contract, row);
    }
  });
  const openColumns = [
    {
      label: "Token",
      render: (row) =>
        `${row.token_symbol || "-"}<div class="cell-subtext">Position #${row.id || "-"}</div>`,
    },
    { label: "Status", render: (row) => badge(row.status || "-") },
    { label: "Amount", render: (row) => fmt(row.amount, 4) },
    { label: "Entry", render: (row) => fmt(row.entry_price_sol, 6) },
    { label: "Current", render: (row) => fmt(row.current_price_sol, 6) },
    { label: "Market Value", render: (row) => fmt(row.market_value_sol, 6) },
    { label: "Realized", render: (row) => fmt(row.realized_pnl_sol, 6) },
    { label: "Mode", render: (row) => row.mode || "-" },
    {
      label: "Action",
      render: (row) =>
        row.status === "open"
          ? `<button type="button" class="small-button" data-action="sell-position" data-position-id="${row.id}" data-contract="${row.token_contract}" data-mode="${row.mode || "paper"}" data-symbol="${row.token_symbol || ""}">Sell</button>`
          : "-",
    },
  ];
  const historyColumns = [
    {
      label: "Token",
      render: (row) =>
        `${row.token_symbol || "-"}<div class="cell-subtext">Position #${row.id || "-"}</div>`,
    },
    { label: "Status", render: (row) => badge(row.status || "-") },
    { label: "Amount", render: (row) => fmt(row.amount, 4) },
    { label: "Entry", render: (row) => fmt(row.entry_price_sol, 6) },
    { label: "Realized", render: (row) => fmt(row.realized_pnl_sol, 6) },
    { label: "Mode", render: (row) => row.mode || "-" },
    { label: "Opened At", render: (row) => fmtDateTime(row.opened_at) },
    { label: "Closed At", render: (row) => fmtDateTime(row.closed_at) },
  ];
  const openRows = (rows || []).filter((row) => row.status === "open");
  const historyRows = (rows || []).filter((row) => row.status !== "open");
  positionsOpenBox.innerHTML = renderTable(
    openRows,
    openColumns,
    "No open positions"
  );
  positionsHistoryBox.innerHTML = renderTable(
    historyRows,
    historyColumns,
    "No closed position history yet"
  );
}

function formatPnl(payload) {
  const rows = payload.rows || [];
  const summary = payload.summary || {};
  if (pnlSummaryBox) {
    renderMetrics(pnlSummaryBox, [
      { label: "Open Positions", value: summary.openPositions ?? 0 },
      { label: "Closed Positions", value: summary.closedPositions ?? 0 },
      { label: "Winning Positions", value: summary.profitablePositions ?? 0, cls: Number(summary.profitablePositions) > 0 ? "good" : "" },
      { label: "Losing Positions", value: summary.losingPositions ?? 0, cls: Number(summary.losingPositions) > 0 ? "bad" : "" },
      { label: "Realized SOL", value: fmt(summary.realizedPnlSol, 6), cls: Number(summary.realizedPnlSol) >= 0 ? "good" : "bad" },
      { label: "Unrealized SOL", value: fmt(summary.unrealizedPnlSol, 6), cls: Number(summary.unrealizedPnlSol) >= 0 ? "good" : "bad" },
      { label: "Total SOL", value: fmt(summary.totalPnlSol, 6), cls: Number(summary.totalPnlSol) >= 0 ? "good" : "bad" },
    ]);
  }
  pnlBox.innerHTML = renderPnlTokenTable(rows);
}

function renderPnlTokenTable(rows) {
  if (!rows || !rows.length) {
    return `<div class="empty">No token PnL yet</div>`;
  }
  const head = ["Token", "Status", "Amount", "Cost Basis", "Market Value", "Realized", "Unrealized", "Total"]
    .map((label) => `<th>${label}</th>`)
    .join("");
  const body = rows
    .map(
      (row) => `
        <tr>
          <td>${row.token_symbol || "-"}</td>
          <td>${badge(row.status || "-")}</td>
          <td>${fmt(row.amount, 4)}</td>
          <td>${fmt(row.cost_basis_sol, 6)}</td>
          <td>${fmt(row.market_value_sol, 6)}</td>
          <td>${fmt(row.realized, 6)}</td>
          <td>${fmt(row.unrealized, 6)}</td>
          <td>${fmt(row.total, 6)}</td>
        </tr>
      `
    )
    .join("");
  return `<table class="data-table pnl-token-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function formatSettings(data) {
  renderMetrics(settingsBox, [
    { label: "Wallet", value: data.walletAddress || "-" },
    { label: "Default Budget", value: fmt(data.defaultBudgetSol, 3) },
    { label: "Budget Max", value: fmt(data.budgetSolMax, 3) },
    { label: "Default Mode", value: data.defaultMode || "-" },
    { label: "Risk Mode", value: data.riskMode || "-" },
    { label: "Ranking Filter", value: rankingLabel(data.rankingType) },
    { label: "Private Key", value: data.privateKeyConfigured ? "Configured" : "Missing", cls: data.privateKeyConfigured ? "good" : "bad" },
    { label: "API Token", value: data.apiTokenConfigured ? "Configured" : "Missing" },
    { label: "Min Liquidity", value: `$${fmt(data.minLiquidityUsd, 0)}` },
    { label: "Stop Loss", value: percentText(data.stopLossPct) },
    { label: "Half Take Profit", value: percentText(data.takeProfitHalfPct) },
    { label: "Reserve SOL", value: fmt(data.reserveSolBalance, 3) },
  ]);
}

function showSettingsNotice(message, cls = "") {
  settingsNotice.innerHTML = message ? `<div class="panel-note ${cls}">${message}</div>` : "";
}

function showAutoNotice(status, message) {
  if (!autoStatusNote) return;
  autoStatusNote.innerHTML = message ? statusBanner(status, message) : "";
}

function applySettingsToForms(data, { force = false } = {}) {
  if (!data) return;
  appDefaults.defaultBudgetSol = Number(data.defaultBudgetSol || appDefaults.defaultBudgetSol || 0.02);
  appDefaults.budgetSolMax = Number(data.budgetSolMax || appDefaults.budgetSolMax || 0.1);
  appDefaults.defaultMode = data.defaultMode || appDefaults.defaultMode || "paper";
  appDefaults.riskMode = data.riskMode || appDefaults.riskMode || "normal";
  appDefaults.rankingType = data.rankingType || appDefaults.rankingType || "combined";

  syncGlobalBudgetLimits(appDefaults.budgetSolMax);
  syncDefaultRiskMode(appDefaults.riskMode);
  syncDefaultRankingType(appDefaults.rankingType);

  const autoForm = document.getElementById("autoForm");
  if (autoForm && (force || !formsBootstrapped)) {
    autoForm.budgetSol.value = appDefaults.defaultBudgetSol;
    autoForm.riskMode.value = appDefaults.riskMode;
    autoForm.rankingType.value = appDefaults.rankingType;
  }

  const reviewForm = document.getElementById("reviewForm");
  if (reviewForm && (force || !formsBootstrapped)) {
    reviewForm.mode.value = appDefaults.defaultMode;
    if (reviewForm.side.value !== "sell") {
      reviewForm.tradeAmount.value = appDefaults.defaultBudgetSol;
      reviewForm.tradeAmount.max = String(appDefaults.budgetSolMax);
    }
  }

  formsBootstrapped = true;
}

function updateReviewSelection() {
  const namePart = currentReviewAsset.name ? ` · ${currentReviewAsset.name}` : "";
  reviewSelection.innerHTML = currentReviewAsset.contract
    ? `<strong>Selected Token:</strong> ${currentReviewAsset.symbol || "-"}${namePart} · ${currentReviewAsset.contract}`
    : "No token selected yet.";
}

function updateReviewAmountField(force = false) {
  const reviewForm = document.getElementById("reviewForm");
  const label = document.getElementById("reviewAmountLabel");
  const amountInput = reviewForm.tradeAmount;
  if (reviewForm.side.value === "sell") {
    label.textContent = "Token Amount";
    amountInput.step = "0.000001";
    amountInput.min = "0.000001";
    amountInput.max = "";
    const selectedId = reviewForm.positionId.value;
    const position = latestPositionsById.get(String(selectedId)) || latestPositionsByContract.get(reviewForm.tokenContract.value);
    if (position && (force || Number(amountInput.value || 0) <= 0)) {
      amountInput.value = String(position.amount || 0);
    }
  } else {
    reviewForm.positionId.value = "";
    label.textContent = "Budget (SOL)";
    amountInput.step = "0.001";
    amountInput.min = "0.001";
    amountInput.max = String(appDefaults.budgetSolMax || document.querySelector('#settingsForm [name="budgetSolMax"]')?.value || "0.1");
    if (force || Number(amountInput.value || 0) <= 0) {
      amountInput.value = String(appDefaults.defaultBudgetSol || 0.02);
    }
  }
}

function setReviewAsset(asset = {}) {
  currentReviewAsset = {
    symbol: asset.symbol || "",
    name: asset.name || "",
    contract: asset.contract || "",
  };
  updateReviewSelection();
}

function buildReviewPayload() {
  const form = document.getElementById("reviewForm");
  const amount = Number(form.tradeAmount.value);
  const payload = {
    mode: form.mode.value,
    side: form.side.value,
    tokenContract: form.tokenContract.value,
  };
  if (form.side.value === "sell") {
    if (form.positionId.value) {
      payload.positionId = Number(form.positionId.value);
    }
    payload.tokenAmount = amount;
  } else {
    payload.budgetSol = amount;
  }
  return payload;
}

function fillReviewForSell(positionId, contract, mode, symbol = "") {
  const reviewForm = document.getElementById("reviewForm");
  const position = latestPositionsById.get(String(positionId)) || latestPositionsByContract.get(contract) || {};
  reviewForm.positionId.value = positionId || "";
  reviewForm.mode.value = mode || "paper";
  reviewForm.side.value = "sell";
  reviewForm.tokenContract.value = contract;
  setReviewAsset({ symbol, name: position.token_symbol || "", contract });
  updateReviewAmountField(true);
  reviewResults.innerHTML = statusBanner(
    "info",
    `Sell request loaded for ${symbol || contract || "-"} from Position #${positionId || "-"}. You can review or execute it now.`
  );
  setActiveTab("review");
}

function setActiveTab(tabName) {
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === tabName);
  });
}

async function refreshHealth() {
  try {
    const payload = await api("/health", { headers: authHeaders() });
    healthPill.textContent = payload.ok ? "Backend Healthy" : "Backend Down";
  } catch (error) {
    healthPill.textContent = `Health Error: ${error.message}`;
  }
}

async function refreshSummary() {
  try {
    const payload = await api("/api/summary");
    formatSummary(payload.data);
    formatAuto(payload.data.autoStatus || {});
  } catch (error) {
    summaryBox.innerHTML = `<div class="empty">${error.message}</div>`;
    if (dashboardAutoSlots) {
      dashboardAutoSlots.innerHTML = "";
    }
    if (dashboardAutoProfile) {
      dashboardAutoProfile.innerHTML = "";
    }
  }
}

async function refreshPositions() {
  try {
    const payload = await api("/api/positions");
    formatPositions(payload.data);
  } catch (error) {
    positionsOpenBox.innerHTML = `<div class="empty">${error.message}</div>`;
    positionsHistoryBox.innerHTML = `<div class="empty">${error.message}</div>`;
  }
}

async function refreshPnl() {
  try {
    const payload = await api("/api/pnl");
    formatPnl(payload.data);
  } catch (error) {
    if (pnlSummaryBox) {
      pnlSummaryBox.innerHTML = `<div class="empty">${error.message}</div>`;
    }
    pnlBox.innerHTML = `<div class="empty">${error.message}</div>`;
  }
}

async function refreshAuto() {
  try {
    const payload = await api("/api/auto/status");
    formatAuto(payload.data);
  } catch (error) {
    autoBox.innerHTML = `<div class="empty">${error.message}</div>`;
    if (autoSlotsBox) {
      autoSlotsBox.innerHTML = "";
    }
    if (autoStatusNote) {
      autoStatusNote.innerHTML = "";
    }
    if (autoDecisionBox) {
      autoDecisionBox.innerHTML = "";
    }
    if (autoProfileBox) {
      autoProfileBox.innerHTML = "";
    }
  }
}

async function refreshSettings() {
  try {
    const payload = await api("/api/settings");
    formatSettings(payload.data);
    const form = document.getElementById("settingsForm");
    form.walletAddress.value = payload.data.walletAddress || "";
    form.defaultBudgetSol.value = payload.data.defaultBudgetSol || 0.02;
    form.budgetSolMax.value = payload.data.budgetSolMax || 0.1;
    form.defaultMode.value = payload.data.defaultMode || "paper";
    form.riskMode.value = payload.data.riskMode || "normal";
    form.rankingType.value = payload.data.rankingType || "combined";
    form.minLiquidityUsd.value = payload.data.minLiquidityUsd || 60000;
    form.stopLossPct.value = payload.data.stopLossPct || 0.12;
    form.takeProfitCostBasisPct.value = payload.data.takeProfitCostBasisPct || 0.45;
    form.takeProfitHalfPct.value = payload.data.takeProfitHalfPct || 0.9;
    form.moonbagTriggerPct.value = payload.data.moonbagTriggerPct || 1.8;
    form.moonbagFraction.value = payload.data.moonbagFraction || 0.1;
    form.maxHoldHours.value = payload.data.maxHoldHours || 18;
    form.timeExitMaxGainPct.value = payload.data.timeExitMaxGainPct || 0.1;
    form.discoverInterval.value = payload.data.discoverInterval || 90;
    form.orderPollInterval.value = payload.data.orderPollInterval || 8;
    form.orderPollMax.value = payload.data.orderPollMax || 6;
    form.autoDailyLossLimitSol.value = payload.data.autoDailyLossLimitSol || 0.03;
    form.autoMaxConsecutiveLosses.value = payload.data.autoMaxConsecutiveLosses || 2;
    form.reserveSolBalance.value = payload.data.reserveSolBalance || 0.02;
    applySettingsToForms(payload.data);
  } catch (error) {
    settingsBox.innerHTML = `<div class="empty">${error.message}</div>`;
  }
}

function syncGlobalBudgetLimits(budgetSolMax) {
  const maxValue = String(budgetSolMax || 0.1);
  document.querySelector('#reviewForm [name="tradeAmount"]').max = maxValue;
  document.querySelector('#autoForm [name="budgetSol"]').max = maxValue;
  document.querySelector('#settingsForm [name="defaultBudgetSol"]').max = maxValue;
}

function syncDefaultRiskMode(riskMode) {
  const discoverRisk = document.querySelector('#discoverForm [name="riskMode"]');
  const autoRisk = document.querySelector('#autoForm [name="riskMode"]');
  if (discoverRisk) discoverRisk.value = riskMode || "normal";
  if (autoRisk) autoRisk.value = riskMode || "normal";
}

function syncDefaultRankingType(rankingType) {
  const discoverRanking = document.querySelector('#discoverForm [name="rankingType"]');
  const autoRanking = document.querySelector('#autoForm [name="rankingType"]');
  if (discoverRanking) discoverRanking.value = rankingType || "combined";
  if (autoRanking) autoRanking.value = rankingType || "combined";
}

async function refreshAllViews() {
  await Promise.all([refreshSummary(), refreshPositions(), refreshPnl(), refreshAuto(), refreshSettings()]);
}

function clearScheduledRefreshes() {
  refreshTimers.forEach((timer) => window.clearTimeout(timer));
  refreshTimers = [];
}

function scheduleFollowUpRefreshes() {
  clearScheduledRefreshes();
  [1500, 4000, 8000].forEach((delay) => {
    const timer = window.setTimeout(() => {
      refreshAllViews();
    }, delay);
    refreshTimers.push(timer);
  });
}

async function pollOrderUntilSettled(orderId) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const payload = await api(`/api/orders/${orderId}`);
    const remote = payload.data.remote?.data || payload.data.remote || {};
    const status = remote.status || payload.data.db?.status || "unknown";
    if (terminalStatus(status)) {
      await refreshAllViews();
      formatExecution(
        {
          mode: payload.data.db?.mode,
          status,
          orderId,
          txId: remote.txs?.[0]?.txId || payload.data.db?.tx_id || "",
          clientTradeId: payload.data.db?.client_trade_id || "",
        },
        `Order finished with status ${status}.`
      );
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1600));
  }
}

async function discoverWithRetry(payload) {
  let lastError = null;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      if (attempt === 1) {
        discoverResults.innerHTML = statusBanner("pending", "Finding candidates from official rankings. This may take a few seconds.");
      } else {
        discoverResults.innerHTML = statusBanner("pending", "The first request was unstable. Retrying discover automatically.");
      }
      return await api("/api/discover", { method: "POST", body: JSON.stringify(payload) });
    } catch (error) {
      lastError = error;
      if (attempt < 2) {
        await sleep(1200);
      }
    }
  }
  throw lastError;
}

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => setActiveTab(button.dataset.tab));
});

document.getElementById("reviewForm").addEventListener("input", () => {
  latestReviewPayload = null;
});

document.querySelector('#reviewForm [name="side"]').addEventListener("change", () => updateReviewAmountField(true));
document.querySelector('#reviewForm [name="tokenContract"]').addEventListener("input", () => {
  const reviewForm = document.getElementById("reviewForm");
  const contract = document.querySelector('#reviewForm [name="tokenContract"]').value;
  const position = latestPositionsByContract.get(contract) || {};
  if (!position || String(position.id || "") !== String(reviewForm.positionId.value || "")) {
    reviewForm.positionId.value = "";
  }
  if (position.token_symbol) {
    setReviewAsset({ symbol: position.token_symbol, name: position.token_symbol, contract });
  } else if (currentReviewAsset.contract !== contract) {
    setReviewAsset({ contract });
  }
  updateReviewAmountField(false);
});

document.getElementById("discoverForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const formData = new FormData(form);
  const submitButton = form.querySelector('button[type="submit"]');
  const payload = {
    rankingType: formData.get("rankingType"),
    riskMode: formData.get("riskMode"),
  };
  try {
    submitButton.disabled = true;
    submitButton.textContent = "Finding…";
    const result = await discoverWithRetry(payload);
    formatDiscover(result.data);
    if (result.data.recommended?.candidate?.contract) {
      document.querySelector('#reviewForm [name="tokenContract"]').value = result.data.recommended.candidate.contract;
    }
  } catch (error) {
    discoverResults.innerHTML = `${statusBanner("failed", `Find candidates failed after retry: ${error.message}`)}<div class="panel-note">You can retry the same filter again when the upstream interface stabilizes.</div>`;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Find Candidates";
  }
});

discoverResults.addEventListener("click", (event) => {
  const button = event.target.closest('[data-action="use-candidate"]');
  if (!button) return;
  const contract = button.dataset.contract || "";
  const symbol = button.dataset.symbol || "";
  const name = button.dataset.name || "";
  const reviewForm = document.getElementById("reviewForm");
  reviewForm.side.value = "buy";
  reviewForm.positionId.value = "";
  reviewForm.tokenContract.value = contract;
  setReviewAsset({ symbol, name, contract });
  updateReviewAmountField(true);
  reviewResults.innerHTML = `<div class="panel-note"><strong>Loaded for review:</strong> ${symbol || contract || "-"}${contract ? ` · ${contract}` : ""}</div>`;
  setActiveTab("review");
});

document.getElementById("positions").addEventListener("click", async (event) => {
  const button = event.target.closest('[data-action="sell-position"]');
  if (!button) return;
  const positionId = button.dataset.positionId || "";
  const contract = button.dataset.contract || "";
  const mode = button.dataset.mode || "paper";
  const symbol = button.dataset.symbol || "";
  fillReviewForSell(positionId, contract, mode, symbol);
  if (!window.confirm(`Sell open position for ${symbol || contract || "this token"} now?`)) {
    return;
  }
  try {
    const result = await api(`/api/positions/${encodeURIComponent(positionId || contract)}/sell?mode=${encodeURIComponent(mode)}`, {
      method: "POST",
      body: "{}",
    });
    formatExecution(
      result.data,
      result.data.status === "success"
        ? `Sell completed for ${symbol || contract || "-"}.`
        : `Sell submitted for ${symbol || contract || "-"}. Waiting for final status.`
    );
    setActiveTab("review");
    await refreshAllViews();
    scheduleFollowUpRefreshes();
    if (result.data.orderId && !terminalStatus(result.data.status)) {
      await pollOrderUntilSettled(result.data.orderId);
    }
  } catch (error) {
    reviewResults.innerHTML = `${statusBanner("error", error.message)}`;
    setActiveTab("review");
  }
});

document.getElementById("prepareButton").addEventListener("click", async () => {
  const payload = buildReviewPayload();
  const prepareButton = document.getElementById("prepareButton");
  const executeButton = document.getElementById("executeButton");
  setButtonsBusy([prepareButton, executeButton], true, new Map([[prepareButton, "Preparing..."], [executeButton, "Execute"]]));
  try {
    const result = await api("/api/order/prepare", { method: "POST", body: JSON.stringify(payload) });
    latestReviewPayload = payload;
    formatReview(result.data);
  } catch (error) {
    reviewResults.innerHTML = `<div class="empty">${error.message}</div>`;
  } finally {
    setButtonsBusy([prepareButton, executeButton], false);
  }
});

document.getElementById("executeButton").addEventListener("click", async () => {
  const payload = latestReviewPayload || buildReviewPayload();
  const prepareButton = document.getElementById("prepareButton");
  const executeButton = document.getElementById("executeButton");
  setButtonsBusy([prepareButton, executeButton], true, new Map([[executeButton, "Executing..."], [prepareButton, "Prepare"]]));
  try {
    const result = await api("/api/order/execute", { method: "POST", body: JSON.stringify(payload) });
    formatExecution(result.data);
    await refreshAllViews();
    scheduleFollowUpRefreshes();
    if (result.data.orderId && !terminalStatus(result.data.status)) {
      await pollOrderUntilSettled(result.data.orderId);
    }
  } catch (error) {
    reviewResults.innerHTML = `<div class="empty">${error.message}</div>`;
  } finally {
    setButtonsBusy([prepareButton, executeButton], false);
  }
});

document.getElementById("dashboardRefreshButton").addEventListener("click", refreshAllViews);
document.getElementById("refreshPositionsButton").addEventListener("click", refreshPositions);
document.getElementById("refreshPnlButton").addEventListener("click", refreshPnl);

document.getElementById("startAutoButton").addEventListener("click", async () => {
  if (autoActionPending) return;
  const formData = new FormData(document.getElementById("autoForm"));
  const payload = {
    rankingType: formData.get("rankingType"),
    budgetSol: Number(formData.get("budgetSol")),
    riskMode: formData.get("riskMode"),
  };
  autoActionPending = "start";
  syncAutoButtons();
  showAutoNotice("pending", "Starting auto mode and loading the latest runtime state. The console will refresh when the backend confirms the new status.");
  try {
    await api("/api/auto/start", { method: "POST", body: JSON.stringify(payload) });
    await refreshAllViews();
    scheduleFollowUpRefreshes();
  } catch (error) {
    showAutoNotice("error", `Auto start failed: ${error.message}`);
    autoBox.innerHTML = `<div class="empty">${error.message}</div>`;
  } finally {
    autoActionPending = "";
    await refreshAuto();
  }
});

document.getElementById("stopAutoButton").addEventListener("click", async () => {
  if (autoActionPending) return;
  autoActionPending = "stop";
  syncAutoButtons();
  showAutoNotice("pending", "Stopping auto mode and waiting for the latest runtime state.");
  try {
    await api("/api/auto/stop", { method: "POST", body: "{}" });
    await refreshAllViews();
  } catch (error) {
    showAutoNotice("error", `Auto stop failed: ${error.message}`);
    autoBox.innerHTML = `<div class="empty">${error.message}</div>`;
  } finally {
    autoActionPending = "";
    await refreshAuto();
  }
});

document.getElementById("settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(event.target);
  const payload = {
    walletAddress: formData.get("walletAddress"),
    defaultBudgetSol: Number(formData.get("defaultBudgetSol")),
    budgetSolMax: Number(formData.get("budgetSolMax")),
    defaultMode: formData.get("defaultMode"),
    riskMode: formData.get("riskMode"),
    rankingType: formData.get("rankingType"),
    minLiquidityUsd: Number(formData.get("minLiquidityUsd")),
    stopLossPct: Number(formData.get("stopLossPct")),
    takeProfitCostBasisPct: Number(formData.get("takeProfitCostBasisPct")),
    takeProfitHalfPct: Number(formData.get("takeProfitHalfPct")),
    moonbagTriggerPct: Number(formData.get("moonbagTriggerPct")),
    moonbagFraction: Number(formData.get("moonbagFraction")),
    maxHoldHours: Number(formData.get("maxHoldHours")),
    timeExitMaxGainPct: Number(formData.get("timeExitMaxGainPct")),
    discoverInterval: Number(formData.get("discoverInterval")),
    orderPollInterval: Number(formData.get("orderPollInterval")),
    orderPollMax: Number(formData.get("orderPollMax")),
    autoDailyLossLimitSol: Number(formData.get("autoDailyLossLimitSol")),
    autoMaxConsecutiveLosses: Number(formData.get("autoMaxConsecutiveLosses")),
    reserveSolBalance: Number(formData.get("reserveSolBalance")),
  };
  try {
    showSettingsNotice("");
    const result = await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    formatSettings(result.data);
    applySettingsToForms(result.data, { force: true });
    await refreshAllViews();
    showSettingsNotice("Settings saved and applied to the live runtime.");
  } catch (error) {
    showSettingsNotice(error.message, "bad");
  }
});

document.getElementById("clearPaperButton").addEventListener("click", async () => {
  if (!window.confirm("Clear all paper-mode orders, trades, positions, and PnL snapshots?")) {
    return;
  }
  try {
    const result = await api("/api/paper/clear", { method: "POST", body: "{}" });
    const cleared = result.data.cleared || {};
    showSettingsNotice(
      `Paper data cleared. Orders ${cleared.orders || 0}, trades ${cleared.trades || 0}, positions ${cleared.positions || 0}, pnl snapshots ${cleared.pnlSnapshots || 0}.`
    );
    await refreshAllViews();
  } catch (error) {
    showSettingsNotice(error.message, "bad");
  }
});

document.getElementById("clearHistoryButton").addEventListener("click", async () => {
  if (!window.confirm("Clear all orders, trades, positions, PnL snapshots, and runtime history across all modes? Settings will be kept.")) {
    return;
  }
  try {
    showSettingsNotice("");
    const result = await api("/api/history/clear", { method: "POST", body: "{}" });
    const cleared = result.data.cleared || {};
    await refreshAllViews();
    showSettingsNotice(
      `All history cleared. Orders ${cleared.orders || 0}, trades ${cleared.trades || 0}, positions ${cleared.positions || 0}, pnl snapshots ${cleared.pnlSnapshots || 0}, risk events ${cleared.riskEvents || 0}, api events ${cleared.apiEvents || 0}.`
    );
  } catch (error) {
    showSettingsNotice(error.message, "bad");
  }
});

updateReviewSelection();

async function initializeApp() {
  await refreshHealth();
  await refreshSettings();
  updateReviewAmountField(true);
  await Promise.all([refreshSummary(), refreshPositions(), refreshPnl(), refreshAuto()]);
}

initializeApp();
