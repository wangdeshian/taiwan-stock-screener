const RESULTS_URL = "./data/results.json";
const HISTORY_URL = "./data/history.json";

const candidateRows = document.getElementById("candidateRows");
const candidateCount = document.getElementById("candidateCount");
const latestUpdate = document.getElementById("latestUpdate");
const refreshButton = document.getElementById("refreshButton");
const searchButton = document.getElementById("searchButton");
const searchInput = document.getElementById("searchInput");
const notice = document.getElementById("notice");
const todayView = document.getElementById("todayView");
const historyView = document.getElementById("historyView");
const historyContent = document.getElementById("historyContent");
const tabToday = document.getElementById("tabToday");
const tabHistory = document.getElementById("tabHistory");
const strategyMomentumBtn = document.getElementById("strategyMomentum");
const strategyLeftBtn = document.getElementById("strategyLeft");

let latestData    = null;
let strategy      = "momentum";           // "momentum" | "left"
let candidateSets = { momentum: [], left: [] };
let rowOrder      = { momentum: [], left: [] };  // 保持列位置：以代號記住顯示順序
let expandedRows  = new Set();            // key: `${strategy}:${symbol}`
let historyLoaded = false;
let budget        = 0;   // 使用者投入預算（台幣）

const DIM_LABELS = {
  trend_score: "趨勢",
  volume_score: "量能",
  institutional_score: "法人",
  chip_score: "籌碼",
  fundamental_score: "基本面",
  industry_score: "產業",
  risk_reward_score: "風報比",
};

const LEFT_DIM_LABELS = {
  base_structure_score: "底部結構",
  short_covering_score: "空單回補",
  retail_capitulation_score: "散戶絕望",
  smart_money_score: "聰明錢",
  fundamental_safety_score: "基本面安全",
  sentiment_score: "聲量情緒",
};

const REASON_LABELS = {
  close_above_ma20: "收盤價 > MA20",
  ma_bullish_alignment: "均線多頭排列",
  ma20_uptrend: "MA20 向上",
  ma60_uptrend: "MA60 向上",
  near_60d_high: "接近 60 日高點",
  volume_expansion: "量能放大",
  high_turnover: "成交金額達標",
  institutional_net_buying: "法人買超",
  institutional_buy_ratio: "法人買超比例達標",
  obv_accumulation: "OBV 累積",
  chip_support: "籌碼支撐",
  revenue_yoy_growth: "月營收 YoY 成長",
  positive_eps: "EPS 為正",
  healthy_roe: "ROE 達標",
  strong_industry_rotation: "產業輪動強",
  risk_reward_above_min: "風報比 >= 2",
  relative_strength_20d: "20 日強於大盤",
  relative_strength_60d: "60 日強於大盤",
  // 左側潛伏策略
  low_base: "股價低基期",
  bollinger_squeeze: "布林通道極度壓縮",
  price_stabilizing: "股價止穩",
  short_covering: "借券/空單回補",
  margin_flush: "融資大減",
  day_trade_freeze: "當沖冷清",
  volume_dryup: "量能萎縮（無人問津）",
  big_holder_accumulation: "千張大戶增持",
  trust_light_buying: "投信微幅買超",
  still_profitable: "EPS 為正",
  revenue_not_collapsing: "營收未惡化",
  sentiment_freeze: "網路聲量冰點",
  observation_pool: "潛伏觀察池",
};

function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${money(value)}%`;
}

function setNotice(message = "") {
  notice.hidden = !message;
  notice.textContent = message;
}

function renderDemoBanner(isDemoSource) {
  let banner = document.getElementById("demoBanner");
  if (!isDemoSource) {
    if (banner) banner.remove();
    return;
  }
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "demoBanner";
    banner.className = "demo-banner";
    const table = todayView.querySelector(".table-wrap");
    todayView.insertBefore(banner, table);
  }
  banner.textContent = "目前顯示的是示範資料，尚未跑過真正的篩選。請到 GitHub Actions 的 Daily Stock Screener 手動執行一次。";
}

function metricCard(label, value, tone = "") {
  return `
    <div class="metric-card ${tone}">
      <span class="metric-label">${label}</span>
      <strong>${value}</strong>
    </div>`;
}

function renderScoreBreakdown(item) {
  const labels = item.strategy === "left_side" ? LEFT_DIM_LABELS : DIM_LABELS;
  return Object.entries(labels).map(([key, label]) => `
    <div class="detail-dim">
      <span class="dim-label">${label}</span>
      <span class="dim-val">${money(item[key])}</span>
    </div>
  `).join("");
}

function renderReasonTags(item) {
  return (item.reasons || []).map(reason => `
    <span class="reason-tag">${REASON_LABELS[reason] || reason}</span>
  `).join("");
}

function renderFundamentalMetrics(item) {
  return `
    <section class="detail-section">
      <h3>基本面</h3>
      <div class="metric-grid">
        ${metricCard("月營收 YoY", pct(item.revenue_yoy_pct))}
        ${metricCard("EPS", money(item.eps))}
        ${metricCard("ROE", pct(item.roe_pct))}
        ${metricCard("本益比", money(item.pe_ratio))}
        ${metricCard("PEG 估算", money(item.peg_ratio), item.peg_ratio && item.peg_ratio < 1 ? "good" : "")}
      </div>
    </section>`;
}

function renderStrengthMetrics(item) {
  return `
    <section class="detail-section">
      <h3>相對強弱</h3>
      <div class="metric-grid">
        ${metricCard("個股 20 日", pct(item.stock_return_20d_pct))}
        ${metricCard("大盤 20 日", pct(item.benchmark_return_20d_pct))}
        ${metricCard("RS 20 日", pct(item.relative_strength_20d_pct), Number(item.relative_strength_20d_pct) > 0 ? "good" : "")}
        ${metricCard("個股 60 日", pct(item.stock_return_60d_pct))}
        ${metricCard("大盤 60 日", pct(item.benchmark_return_60d_pct))}
        ${metricCard("RS 60 日", pct(item.relative_strength_60d_pct), Number(item.relative_strength_60d_pct) > 0 ? "good" : "")}
      </div>
    </section>`;
}

function renderChipMetrics(item) {
  const title = item.is_observation_pool ? "深度籌碼（觀察池，資料累積中）" : "深度籌碼（近 20 日）";
  return `
    <section class="detail-section">
      <h3>${title}</h3>
      <div class="metric-grid">
        ${metricCard("借券賣出餘額變化", pct(item.short_balance_change_pct), Number(item.short_balance_change_pct) < 0 ? "good" : "")}
        ${metricCard("融資餘額變化", pct(item.margin_balance_change_pct), Number(item.margin_balance_change_pct) < 0 ? "good" : "")}
        ${metricCard("近 5 日當沖率", pct(item.day_trade_ratio_pct))}
        ${metricCard("大戶持股增減 (8週)", item.big_holder_gain_pp === null || item.big_holder_gain_pp === undefined ? "-" : `${money(item.big_holder_gain_pp)} pp`, Number(item.big_holder_gain_pp) > 0 ? "good" : "")}
      </div>
    </section>`;
}

function renderTradePlan(item) {
  if (!item.entry_price) return "";
  return `
    <section class="detail-section">
      <h3>交易計畫</h3>
      <div class="metric-grid">
        ${metricCard("進場", money(item.entry_price))}
        ${metricCard("備用進場", money(item.alternate_entry_price))}
        ${metricCard("停損", money(item.stop_loss_price))}
        ${metricCard("目標一", money(item.target_price_1))}
        ${metricCard("目標二", money(item.target_price_2))}
        ${metricCard("建議部位", `${money(item.suggested_position_pct)}%`)}
        ${metricCard("價格來源", item.price_source || "-")}
      </div>
    </section>`;
}

// ── 利弗莫爾加碼計畫（含預算換算） ──────────────────────────────────────
function renderLivermore(item) {
  if (!item.entry_price) return "";
  const e  = Number(item.entry_price);
  const p1 = e * 1.10, p2 = e * 1.21, p3 = e * 1.331, sl0 = e * 0.90;
  const fmt = v => money(Number(v).toFixed(2));

  // 台股: 1 張 = 1000 股；手續費 0.1425%（最低 20 元）；賣出交易稅 0.3%
  function tierMeta(price, pct) {
    if (!budget) return null;
    const amount = budget * pct;
    const lots   = Math.floor(amount / (price * 1000));
    const shares = lots * 1000;
    const cost   = shares * price;
    const fee    = Math.max(20, cost * 0.001425);
    return { lots, cost, fee };
  }

  function slotHtml(price, pct, fallback) {
    const t = tierMeta(price, pct);
    if (!t) return `<span class="lv-sl">${fallback}</span>`;
    if (t.lots === 0) return `<span class="lv-sl lv-warn-text">⚠ 預算不足買 1 張</span>`;
    return `<span class="lv-sl">${fallback}　<b>${t.lots} 張</b>（${money(t.cost)} 元 ＋ 手續費 ${money(Math.round(t.fee))} 元）</span>`;
  }

  const title = budget
    ? `📐 利弗莫爾加碼計畫（預算 ${money(budget)} 元）`
    : `📐 利弗莫爾加碼計畫`;

  return `
    <section class="detail-section">
      <h3>${title}</h3>
      <div class="livermore-grid">
        <div class="lv-row lv-init">
          <span class="lv-label">底倉 20%</span>
          <span class="lv-price">進場 ${fmt(e)}</span>
          ${slotHtml(e, 0.20, `停損 ${fmt(sl0)}（總虧 ≈2%）`)}
        </div>
        <div class="lv-row lv-add">
          <span class="lv-label">加碼一 +20%</span>
          <span class="lv-price">漲至 ${fmt(p1)}</span>
          ${slotHtml(p1, 0.20, '累計 40%')}
        </div>
        <div class="lv-row lv-add">
          <span class="lv-label">加碼二 +20%</span>
          <span class="lv-price">漲至 ${fmt(p2)}</span>
          ${slotHtml(p2, 0.20, '累計 60%')}
        </div>
        <div class="lv-row lv-full">
          <span class="lv-label">加碼三 +40%</span>
          <span class="lv-price">漲至 ${fmt(p3)}</span>
          ${slotHtml(p3, 0.40, '滿倉 100%')}
        </div>
        <div class="lv-row lv-warn">
          <span class="lv-label">⚠ 任何階段</span>
          <span class="lv-price" style="color:#991b1b">從加碼價跌 10% → 立刻全清</span>
        </div>
      </div>
    </section>`;
}

function buildDetailRow(item, colSpan) {
  const tr = document.createElement("tr");
  tr.className = "detail-row";
  const td = document.createElement("td");
  td.colSpan = colSpan;
  const isLeft = item.strategy === "left_side";
  td.innerHTML = `
    <div class="detail-grid">${renderScoreBreakdown(item)}</div>
    <div class="reason-tags">${renderReasonTags(item)}</div>
    ${isLeft ? renderChipMetrics(item) : renderFundamentalMetrics(item) + renderStrengthMetrics(item)}
    ${renderTradePlan(item)}
    ${isLeft ? "" : renderLivermore(item)}
  `;
  tr.appendChild(td);
  return tr;
}

function expandKey(item) {
  return `${item.strategy === "left_side" ? "left" : "momentum"}:${item.symbol}`;
}

// 保持列位置：舊代號沿用原本順序，新出現的代號附加在最後
function applyStableOrder(strategyKey, rows) {
  const bySymbol = new Map(rows.map(row => [row.symbol, row]));
  const kept = rowOrder[strategyKey].filter(symbol => bySymbol.has(symbol));
  const known = new Set(kept);
  const appended = rows.map(row => row.symbol).filter(symbol => !known.has(symbol));
  rowOrder[strategyKey] = [...kept, ...appended];
  return rowOrder[strategyKey].map(symbol => bySymbol.get(symbol));
}

// 預算變更時重繪已展開的列
function refreshExpandedRows() {
  currentRows().forEach(item => {
    const key = expandKey(item);
    if (!expandedRows.has(key)) return;
    const oldDetail = document.getElementById(`detail-${key.replace(":", "-")}`);
    if (!oldDetail) return;
    const newDetail = buildDetailRow(item, 9);
    newDetail.id = oldDetail.id;
    oldDetail.replaceWith(newDetail);
  });
}

function currentRows() {
  const query = searchInput.value.trim().toLowerCase();
  const rows = candidateSets[strategy];
  if (!query) return rows;
  return rows.filter(item =>
    [item.symbol, item.name, item.market, item.industry]
      .some(value => String(value || "").toLowerCase().includes(query))
  );
}

function renderRows(rows) {
  candidateRows.innerHTML = "";

  if (!rows.length) {
    const message = strategy === "left"
      ? "目前沒有符合左側潛伏起手式的股票（籌碼快照累積約一個月後訊號最完整）"
      : "目前沒有符合條件的候選股";
    candidateRows.innerHTML = `<tr><td class="empty" colspan="9">${message}</td></tr>`;
    return;
  }

  rows.forEach(item => {
    const key = expandKey(item);
    const detailId = `detail-${key.replace(":", "-")}`;
    const tr = document.createElement("tr");
    tr.className = "candidate-row";
    tr.dataset.symbol = item.symbol;
    tr.innerHTML = `
      <td>${item.symbol} <span class="chevron${expandedRows.has(key) ? " open" : ""}">▶</span></td>
      <td>${item.name}</td>
      <td class="close-price">${money(item.close_price)}</td>
      <td>${item.industry || "-"}</td>
      <td class="score">${money(item.total_score)}</td>
      <td>${money(item.entry_price)}</td>
      <td>${money(item.stop_loss_price)}</td>
      <td>${money(item.target_price_1)}</td>
      <td>${money(item.risk_reward_ratio)}</td>`;

    tr.addEventListener("click", () => {
      const chevron = tr.querySelector(".chevron");
      const existing = document.getElementById(detailId);
      if (expandedRows.has(key)) {
        if (existing) existing.remove();
        expandedRows.delete(key);
        chevron.classList.remove("open");
      } else {
        const detail = buildDetailRow(item, 9);
        detail.id = detailId;
        tr.insertAdjacentElement("afterend", detail);
        expandedRows.add(key);
        chevron.classList.add("open");
      }
    });

    candidateRows.appendChild(tr);

    // 更新資料後維持原本展開狀態
    if (expandedRows.has(key)) {
      const detail = buildDetailRow(item, 9);
      detail.id = detailId;
      candidateRows.appendChild(detail);
    }
  });
}

function updateNotice() {
  if (!latestData) return;
  const messages = [];
  if (latestData.source === "live_stale") {
    messages.push(latestData.selection_note || "本次資料源暫時無法完整更新，畫面保留上一版真實篩選結果。");
  } else if (strategy === "momentum" && latestData.selection_note) {
    messages.push("目前沒有股票達到正式門檻，畫面改顯示最高分的真實排序。");
  }
  if (strategy === "left") {
    if (latestData.left_side_enabled === false) {
      messages.push("左側潛伏策略目前已停用（SCREENER_LEFT_SIDE=0）。");
    } else {
      if (latestData.left_side_universe_count) {
        messages.push(`全市場掃描 ${latestData.left_side_universe_count} 檔，籌碼起手式訊號入圍 ${latestData.left_side_shortlist_count ?? 0} 檔。`);
      }
      if (latestData.left_side_mode === "observation_pool") {
        messages.push(`目前為潛伏觀察池，籌碼歷史已累積 ${latestData.left_side_chip_dates ?? 0} 個交易日。`);
      }
      if (latestData.left_side_note) {
        messages.push(latestData.left_side_note);
      }
    }
  }
  if (!latestData.has_institutional_data) {
    messages.push("目前缺少法人資料，請確認 GitHub Secrets 已設定 FINMIND_TOKEN。");
  }
  setNotice(messages.join(" "));
}

function renderCurrent() {
  const rows = currentRows();
  candidateCount.textContent = rows.length;
  renderRows(rows);
  updateNotice();
}

function switchStrategy(next) {
  if (strategy === next) return;
  strategy = next;
  strategyMomentumBtn.classList.toggle("active", next === "momentum");
  strategyLeftBtn.classList.toggle("active", next === "left");
  renderCurrent();
}

function applySearch() {
  renderCurrent();
}

async function loadData() {
  candidateRows.innerHTML = `<tr><td class="empty" colspan="9">讀取資料中...</td></tr>`;
  setNotice("");

  try {
    const response = await fetch(`${RESULTS_URL}?t=${Date.now()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    latestData = data;

    const momentum = (data.top_candidates || []).map(item => ({ ...item, strategy: item.strategy || "momentum" }));
    const left = (data.left_side_candidates || []).map(item => ({ ...item, strategy: "left_side" }));
    candidateSets = {
      momentum: applyStableOrder("momentum", momentum),
      left: applyStableOrder("left", left),
    };

    if (data.updated_at) {
      const dt = new Date(data.updated_at);
      latestUpdate.textContent = dt.toLocaleString("zh-TW", {
        timeZone: "Asia/Taipei",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } else {
      latestUpdate.textContent = "-";
    }

    renderDemoBanner(data.source === "demo");
    renderCurrent();
  } catch (error) {
    console.error(error);
    candidateCount.textContent = "0";
    latestUpdate.textContent = "-";
    renderDemoBanner(false);
    setNotice("讀取資料失敗，請到 GitHub Actions 的 Daily Stock Screener 重新執行一次。");
    candidateRows.innerHTML = `<tr><td class="empty" colspan="9">讀取失敗</td></tr>`;
  }
}

function switchTab(tab) {
  const today = tab === "today";
  todayView.hidden = !today;
  historyView.hidden = today;
  tabToday.classList.toggle("active", today);
  tabHistory.classList.toggle("active", !today);
  refreshButton.hidden = !today;
  if (!today && !historyLoaded) loadHistory();
}

async function loadHistory() {
  historyContent.innerHTML = `<p class="empty" style="padding:20px;text-align:center;color:#64748b">讀取歷史中...</p>`;
  try {
    const response = await fetch(`${HISTORY_URL}?t=${Date.now()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const entries = await response.json();
    historyLoaded = true;
    renderHistory(entries);
  } catch {
    historyContent.innerHTML = `
      <p class="empty" style="padding:20px;text-align:center;color:#b45309">
        讀取歷史紀錄失敗，請重新執行 GitHub Actions。
      </p>`;
  }
}

function historyTable(candidates) {
  const rows = candidates.map(candidate => `
    <tr>
      <td>${candidate.symbol}</td>
      <td>${candidate.name}</td>
      <td>${candidate.industry || "-"}</td>
      <td class="score">${money(candidate.total_score)}</td>
      <td>${money(candidate.entry_price)}</td>
      <td>${money(candidate.stop_loss_price)}</td>
      <td>${money(candidate.target_price_1)}</td>
      <td>${money(candidate.risk_reward_ratio)}</td>
    </tr>
  `).join("");
  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>代號</th><th>名稱</th><th>產業</th><th>總分</th>
            <th>進場</th><th>停損</th><th>目標一</th><th>RR</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderHistory(entries) {
  if (!entries || !entries.length) {
    historyContent.innerHTML = `<p class="empty" style="padding:20px;text-align:center;color:#64748b">目前沒有歷史紀錄</p>`;
    return;
  }

  const html = entries.map((entry, idx) => {
    const dateLabel = entry.date || entry.updated_at?.slice(0, 10) || "-";
    const srcBadge = entry.source === "demo"
      ? `<span class="hist-badge demo">示範</span>`
      : `<span class="hist-badge live">真實</span>`;
    const momentum = entry.candidates || [];
    const left = entry.left_side_candidates || [];

    return `
      <div class="hist-entry">
        <button class="hist-header" type="button" aria-expanded="${idx === 0}" onclick="toggleHistEntry(this)">
          <span class="hist-date">${dateLabel}</span>
          ${srcBadge}
          <span class="hist-meta">篩選 ${entry.screened_count ?? "-"} 檔，右側 ${momentum.length} 檔${left.length ? `，左側 ${left.length} 檔` : ""}</span>
          <span class="hist-chevron ${idx === 0 ? "open" : ""}">▶</span>
        </button>
        <div class="hist-body" ${idx === 0 ? "" : "hidden"}>
          ${momentum.length === 0
            ? `<p class="empty" style="padding:12px">沒有右側動能候選股</p>`
            : `<h4 style="padding:10px 12px 0">📈 右側動能</h4>${historyTable(momentum)}`}
          ${left.length === 0
            ? ""
            : `<h4 style="padding:10px 12px 0">🪤 左側潛伏</h4>${historyTable(left)}`}
        </div>
      </div>`;
  }).join("");

  historyContent.innerHTML = `<div class="hist-list">${html}</div>`;
}

function toggleHistEntry(button) {
  const body = button.nextElementSibling;
  const chevron = button.querySelector(".hist-chevron");
  const isOpen = !body.hidden;
  body.hidden = isOpen;
  chevron.classList.toggle("open", !isOpen);
  button.setAttribute("aria-expanded", String(!isOpen));
}

tabToday.addEventListener("click", () => switchTab("today"));
tabHistory.addEventListener("click", () => switchTab("history"));
strategyMomentumBtn.addEventListener("click", () => switchStrategy("momentum"));
strategyLeftBtn.addEventListener("click", () => switchStrategy("left"));
refreshButton.addEventListener("click", loadData);
searchButton.addEventListener("click", applySearch);
searchInput.addEventListener("keydown", event => {
  if (event.key === "Enter") applySearch();
});

// ── 預算設定 ─────────────────────────────────────────────────────────────
const budgetInput = document.getElementById("budgetInput");

function applyBudget(val) {
  budget = Number(val) || 0;
  // 同步輸入框
  if (budgetInput) budgetInput.value = budget || "";
  // 更新已展開的列
  refreshExpandedRows();
}

if (budgetInput) {
  budgetInput.addEventListener("input", e => applyBudget(e.target.value));
}

document.querySelectorAll(".preset-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".preset-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    applyBudget(btn.dataset.val);
  });
});

loadData();
