const RESULTS_URL = "./data/results.json";
const HISTORY_URL  = "./data/history.json";

const candidateRows  = document.getElementById("candidateRows");
const candidateCount = document.getElementById("candidateCount");
const latestUpdate   = document.getElementById("latestUpdate");
const refreshButton  = document.getElementById("refreshButton");
const searchButton   = document.getElementById("searchButton");
const searchInput    = document.getElementById("searchInput");
const notice         = document.getElementById("notice");
const todayView      = document.getElementById("todayView");
const historyView    = document.getElementById("historyView");
const historyContent = document.getElementById("historyContent");
const tabToday       = document.getElementById("tabToday");
const tabHistory     = document.getElementById("tabHistory");

let allCandidates = [];
let expandedRows  = new Set();

// ── 維度標籤中文對照 ──────────────────────────────────────────────────────
const DIM_LABELS = {
  trend_score:         "趨勢",
  volume_score:        "量能",
  institutional_score: "法人",
  chip_score:          "籌碼",
  fundamental_score:   "基本面",
  industry_score:      "產業",
  risk_reward_score:   "風報比",
};

const REASON_LABELS = {
  close_above_ma20:      "收盤>MA20",
  ma_bullish_alignment:  "均線多頭排列",
  ma20_uptrend:          "MA20上升",
  ma60_uptrend:          "MA60上升",
  near_60d_high:         "近60日高點",
  volume_expansion:      "爆量",
  high_turnover:         "高成交額",
  institutional_net_buying: "法人買超",
  institutional_buy_ratio:  "法人比例達標",
  obv_accumulation:      "OBV蓄積",
  chip_support:          "籌碼支撐",
  revenue_yoy_growth:    "營收年增",
  positive_eps:          "EPS>0",
  healthy_roe:           "ROE健康",
  strong_industry_rotation: "強勢產業",
  risk_reward_above_min: "風報比≥2",
};

// ── 工具函式 ──────────────────────────────────────────────────────────────
function money(v) {
  if (v === null || v === undefined) return "-";
  return Number(v).toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function setNotice(msg = "") {
  notice.hidden = !msg;
  notice.textContent = msg;
}

// ── Demo 橫幅 ─────────────────────────────────────────────────────────────
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
    const today = document.getElementById("todayView");
    const table = today.querySelector(".table-wrap");
    today.insertBefore(banner, table);
  }
  banner.textContent = "⚠ 目前顯示的是示範資料，尚未跑過真正的篩選。請到 GitHub Actions → Daily Stock Screener → Run workflow 觸發一次。";
}

// ── 展開列（分數明細）────────────────────────────────────────────────────
function buildDetailRow(item, colSpan) {
  const tr = document.createElement("tr");
  tr.className = "detail-row";
  const td = document.createElement("td");
  td.colSpan = colSpan;

  // 維度分數
  const dimHtml = Object.entries(DIM_LABELS).map(([key, label]) => {
    const val = item[key] ?? "-";
    return `<div class="detail-dim">
      <span class="dim-label">${label}</span>
      <span class="dim-val">${money(val)}</span>
    </div>`;
  }).join("");

  // 原因標籤
  const reasons = (item.reasons || []).map(r =>
    `<span class="reason-tag">${REASON_LABELS[r] || r}</span>`
  ).join("");

  // 進場計畫
  const plan = item.alternate_entry_price
    ? `<div style="margin-top:8px;font-size:12px;color:#64748b">
         備用進場 ${money(item.alternate_entry_price)} ／
         目標二 ${money(item.target_price_2)} ／
         建議倉位 ${money(item.suggested_position_pct)}%
       </div>`
    : "";

  // 利弗莫爾金字塔加碼計畫
  const livermore = item.entry_price ? (() => {
    const e = Number(item.entry_price);
    const p1 = e * 1.10;
    const p2 = e * 1.21;
    const p3 = e * 1.331;
    const sl0 = e * 0.90;
    const fmt = v => money(v.toFixed(2));
    return `
      <div class="livermore-box">
        <div class="livermore-title">📐 利弗莫爾加碼計畫</div>
        <div class="livermore-grid">
          <div class="lv-row lv-init">
            <span class="lv-label">底倉 20%</span>
            <span class="lv-price">進場 ${fmt(e)}</span>
            <span class="lv-sl">停損 ${fmt(sl0)}（總虧 ≈2%）</span>
          </div>
          <div class="lv-row lv-add">
            <span class="lv-label">加碼一 +20%</span>
            <span class="lv-price">漲至 ${fmt(p1)}</span>
            <span class="lv-sl">累計 40%</span>
          </div>
          <div class="lv-row lv-add">
            <span class="lv-label">加碼二 +20%</span>
            <span class="lv-price">漲至 ${fmt(p2)}</span>
            <span class="lv-sl">累計 60%</span>
          </div>
          <div class="lv-row lv-full">
            <span class="lv-label">加碼三 +40%</span>
            <span class="lv-price">漲至 ${fmt(p3)}</span>
            <span class="lv-sl">滿倉 100%</span>
          </div>
          <div class="lv-row lv-warn">
            <span class="lv-label">⚠ 任何階段</span>
            <span class="lv-price" style="color:#991b1b">從加碼價跌 10% → 立刻全清</span>
          </div>
        </div>
      </div>`;
  })() : "";

  td.innerHTML = `
    <div class="detail-grid">${dimHtml}</div>
    <div class="reason-tags">${reasons}</div>
    ${plan}
    ${livermore}
  `;
  tr.appendChild(td);
  return tr;
}

// ── 主表格渲染 ────────────────────────────────────────────────────────────
function renderRows(rows) {
  candidateRows.innerHTML = "";
  expandedRows.clear();

  if (!rows.length) {
    candidateRows.innerHTML =
      `<tr><td class="empty" colspan="8">目前沒有符合條件的候選股</td></tr>`;
    return;
  }

  rows.forEach((item, idx) => {
    // 主列
    const tr = document.createElement("tr");
    tr.className = "candidate-row";
    tr.dataset.idx = idx;
    tr.innerHTML = `
      <td>${item.symbol} <span class="chevron">▶</span></td>
      <td>${item.name}</td>
      <td>${item.industry || "-"}</td>
      <td class="score">${money(item.total_score)}</td>
      <td>${money(item.entry_price)}</td>
      <td>${money(item.stop_loss_price)}</td>
      <td>${money(item.target_price_1)}</td>
      <td>${money(item.risk_reward_ratio)}</td>`;

    tr.addEventListener("click", () => {
      const chevron = tr.querySelector(".chevron");
      if (expandedRows.has(idx)) {
        // 收起
        const detail = document.getElementById(`detail-${idx}`);
        if (detail) detail.remove();
        expandedRows.delete(idx);
        chevron.classList.remove("open");
      } else {
        // 展開
        const detail = buildDetailRow(item, 8);
        detail.id = `detail-${idx}`;
        tr.insertAdjacentElement("afterend", detail);
        expandedRows.add(idx);
        chevron.classList.add("open");
      }
    });

    candidateRows.appendChild(tr);
  });
}

// ── 搜尋 ──────────────────────────────────────────────────────────────────
function applySearch() {
  const q = searchInput.value.trim().toLowerCase();
  const filtered = q
    ? allCandidates.filter(item =>
        [item.symbol, item.name, item.market, item.industry]
          .some(v => String(v || "").toLowerCase().includes(q))
      )
    : allCandidates;
  candidateCount.textContent = filtered.length;
  renderRows(filtered);
}

// ── 資料載入 ──────────────────────────────────────────────────────────────
async function loadData() {
  candidateRows.innerHTML =
    `<tr><td class="empty" colspan="8">讀取中...</td></tr>`;
  setNotice("");

  try {
    const resp = await fetch(`${RESULTS_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    allCandidates = data.top_candidates || [];
    candidateCount.textContent = allCandidates.length;

    if (data.updated_at) {
      const dt = new Date(data.updated_at);
      latestUpdate.textContent = dt.toLocaleString("zh-TW", {
        timeZone: "Asia/Taipei",
        month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      });
    } else {
      latestUpdate.textContent = "-";
    }

    // Demo 橫幅
    renderDemoBanner(data.source === "demo");

    if (data.selection_note) {
      setNotice("目前沒有股票達到嚴格門檻，畫面顯示真實資料中的最高分排名。");
    }

    // 法人資料提示
    if (!data.has_institutional_data) {
      setNotice("目前為免費模式（純技術面評分）。在 GitHub Secrets 設定 FINMIND_TOKEN 可啟用法人、基本面評分。");
    }

    renderRows(allCandidates);
  } catch (err) {
    console.error(err);
    candidateCount.textContent = "0";
    latestUpdate.textContent = "-";
    renderDemoBanner(false);
    setNotice("尚無篩選資料。請到 GitHub Actions → Daily Stock Screener → Run workflow 手動觸發一次。");
    candidateRows.innerHTML =
      `<tr><td class="empty" colspan="8">尚無資料</td></tr>`;
  }
}

// ── Tab 切換 ──────────────────────────────────────────────────────────────
let historyLoaded = false;

function switchTab(tab) {
  if (tab === "today") {
    todayView.hidden = false;
    historyView.hidden = true;
    tabToday.classList.add("active");
    tabHistory.classList.remove("active");
    refreshButton.hidden = false;
  } else {
    todayView.hidden = true;
    historyView.hidden = false;
    tabToday.classList.remove("active");
    tabHistory.classList.add("active");
    refreshButton.hidden = true;
    if (!historyLoaded) loadHistory();
  }
}

tabToday.addEventListener("click", () => switchTab("today"));
tabHistory.addEventListener("click", () => switchTab("history"));

// ── 歷史記錄 ─────────────────────────────────────────────────────────────
async function loadHistory() {
  historyContent.innerHTML =
    `<p class="empty" style="padding:20px;text-align:center;color:#64748b">讀取中...</p>`;
  try {
    const resp = await fetch(`${HISTORY_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const entries = await resp.json();
    historyLoaded = true;
    renderHistory(entries);
  } catch {
    historyContent.innerHTML =
      `<p class="empty" style="padding:20px;text-align:center;color:#b45309">
        尚無歷史記錄。每次 GitHub Actions 執行後會自動累積。
      </p>`;
  }
}

function renderHistory(entries) {
  if (!entries || !entries.length) {
    historyContent.innerHTML =
      `<p class="empty" style="padding:20px;text-align:center;color:#64748b">
        尚無歷史記錄。
      </p>`;
    return;
  }

  const html = entries.map((entry, idx) => {
    const dateLabel = entry.date || entry.updated_at?.slice(0, 10) || "—";
    const srcBadge = entry.source === "demo"
      ? `<span class="hist-badge demo">示範</span>`
      : `<span class="hist-badge live">真實</span>`;
    const count = (entry.candidates || []).length;

    const rows = (entry.candidates || []).map(c => `
      <tr>
        <td>${c.symbol}</td>
        <td>${c.name}</td>
        <td>${c.industry || "—"}</td>
        <td class="score">${money(c.total_score)}</td>
        <td>${money(c.entry_price)}</td>
        <td>${money(c.stop_loss_price)}</td>
        <td>${money(c.target_price_1)}</td>
        <td>${money(c.risk_reward_ratio)}</td>
      </tr>`).join("");

    return `
      <div class="hist-entry">
        <button class="hist-header" type="button" aria-expanded="${idx === 0}"
                onclick="toggleHistEntry(this)">
          <span class="hist-date">${dateLabel}</span>
          ${srcBadge}
          <span class="hist-meta">篩選 ${entry.screened_count ?? "—"} 支 → ${count} 支入選</span>
          <span class="hist-chevron ${idx === 0 ? "open" : ""}">▶</span>
        </button>
        <div class="hist-body" ${idx === 0 ? "" : 'hidden'}>
          ${count === 0
            ? `<p class="empty" style="padding:12px">本次無推薦股票</p>`
            : `<div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>代號</th><th>名稱</th><th>產業</th><th>積分</th>
                      <th>進場</th><th>停損</th><th>目標一</th><th>RR</th>
                    </tr>
                  </thead>
                  <tbody>${rows}</tbody>
                </table>
              </div>`
          }
        </div>
      </div>`;
  }).join("");

  historyContent.innerHTML = `<div class="hist-list">${html}</div>`;
}

function toggleHistEntry(btn) {
  const body = btn.nextElementSibling;
  const chevron = btn.querySelector(".hist-chevron");
  const isOpen = !body.hidden;
  body.hidden = isOpen;
  chevron.classList.toggle("open", !isOpen);
  btn.setAttribute("aria-expanded", String(!isOpen));
}

refreshButton.addEventListener("click", loadData);
searchButton.addEventListener("click", applySearch);
searchInput.addEventListener("keydown", e => {
  if (e.key === "Enter") applySearch();
});

loadData();
