const API_BASE_URL =
  localStorage.getItem("TAIWAN_STOCK_API_BASE_URL") || "http://127.0.0.1:8000";

const DEMO_DASHBOARD = {
  latest_update: "2026-07-12",
  top_candidates: [
    {
      symbol: "2454",
      name: "MediaTek",
      market: "TWSE",
      industry: "Semiconductor",
      total_score: 91.75,
      entry_price: 1479.2,
      stop_loss_price: 1428.54,
      target_price_1: 1580.53,
      risk_reward_ratio: 2,
    },
    {
      symbol: "6488",
      name: "GlobalWafers",
      market: "TPEx",
      industry: "Semiconductor",
      total_score: 91.75,
      entry_price: 548.26,
      stop_loss_price: 529.13,
      target_price_1: 586.53,
      risk_reward_ratio: 2,
    },
    {
      symbol: "2330",
      name: "TSMC",
      market: "TWSE",
      industry: "Semiconductor",
      total_score: 86.75,
      entry_price: 913.45,
      stop_loss_price: 884.2,
      target_price_1: 971.95,
      risk_reward_ratio: 2,
    },
  ],
};

const TEXT = {
  empty: "\u76ee\u524d\u6c92\u6709\u5019\u9078\u80a1",
  loading: "\u8b80\u53d6\u4e2d...",
  apiFallback:
    "\u5c1a\u672a\u9023\u63a5 FastAPI \u5f8c\u7aef\uff0c\u76ee\u524d\u986f\u793a demo \u5019\u9078\u80a1\u3002",
  searchFallback:
    "\u5c1a\u672a\u9023\u63a5 FastAPI \u5f8c\u7aef\uff0c\u641c\u5c0b\u7d50\u679c\u4f86\u81ea demo \u8cc7\u6599\u3002",
  search: "\u641c\u5c0b",
};

const candidateRows = document.getElementById("candidateRows");
const candidateCount = document.getElementById("candidateCount");
const latestUpdate = document.getElementById("latestUpdate");
const refreshButton = document.getElementById("refreshButton");
const searchButton = document.getElementById("searchButton");
const searchInput = document.getElementById("searchInput");
const notice = document.getElementById("notice");

function money(value) {
  if (value === null || value === undefined) return "-";
  return Number(value).toLocaleString("zh-TW", {
    maximumFractionDigits: 2,
  });
}

function setNotice(message = "") {
  notice.hidden = !message;
  notice.textContent = message;
}

function renderRows(rows) {
  candidateRows.innerHTML = "";
  if (!rows.length) {
    candidateRows.innerHTML = `<tr><td class="empty" colspan="8">${TEXT.empty}</td></tr>`;
    return;
  }
  for (const item of rows) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.symbol}</td>
      <td>${item.name}</td>
      <td>${item.industry || "-"}</td>
      <td class="score">${money(item.total_score)}</td>
      <td>${money(item.entry_price)}</td>
      <td>${money(item.stop_loss_price)}</td>
      <td>${money(item.target_price_1)}</td>
      <td>${money(item.risk_reward_ratio)}</td>
    `;
    candidateRows.appendChild(row);
  }
}

function renderDashboard(data, message = "") {
  const rows = data.top_candidates || [];
  candidateCount.textContent = String(rows.length);
  latestUpdate.textContent = data.latest_update || "-";
  setNotice(message);
  renderRows(rows);
}

async function loadDashboard() {
  candidateRows.innerHTML = `<tr><td class="empty" colspan="8">${TEXT.loading}</td></tr>`;
  try {
    const response = await fetch(`${API_BASE_URL}/dashboard`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderDashboard(await response.json());
  } catch {
    renderDashboard(DEMO_DASHBOARD, TEXT.apiFallback);
  }
}

async function searchStocks() {
  const q = searchInput.value.trim().toLowerCase();
  if (!q) {
    await loadDashboard();
    return;
  }
  try {
    const response = await fetch(`${API_BASE_URL}/search?q=${encodeURIComponent(q)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const rows = await response.json();
    candidateCount.textContent = String(rows.length);
    latestUpdate.textContent = TEXT.search;
    setNotice("");
    renderRows(
      rows.map((item) => ({
        ...item,
        total_score: null,
        entry_price: null,
        stop_loss_price: null,
        target_price_1: null,
        risk_reward_ratio: null,
      })),
    );
  } catch {
    const rows = DEMO_DASHBOARD.top_candidates.filter((item) => {
      return [item.symbol, item.name, item.market, item.industry].some((value) =>
        String(value || "").toLowerCase().includes(q),
      );
    });
    candidateCount.textContent = String(rows.length);
    latestUpdate.textContent = TEXT.search;
    setNotice(TEXT.searchFallback);
    renderRows(rows);
  }
}

refreshButton.addEventListener("click", loadDashboard);
searchButton.addEventListener("click", searchStocks);
searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchStocks();
});

loadDashboard();
