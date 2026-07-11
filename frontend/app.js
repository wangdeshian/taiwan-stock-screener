const API_BASE_URL =
  localStorage.getItem("TAIWAN_STOCK_API_BASE_URL") || "http://127.0.0.1:8000";

const candidateRows = document.getElementById("candidateRows");
const candidateCount = document.getElementById("candidateCount");
const latestUpdate = document.getElementById("latestUpdate");
const refreshButton = document.getElementById("refreshButton");
const searchButton = document.getElementById("searchButton");
const searchInput = document.getElementById("searchInput");

function money(value) {
  if (value === null || value === undefined) return "-";
  return Number(value).toLocaleString("zh-TW", {
    maximumFractionDigits: 2,
  });
}

function renderRows(rows) {
  candidateRows.innerHTML = "";
  if (!rows.length) {
    candidateRows.innerHTML = `<tr><td class="empty" colspan="8">目前沒有候選股</td></tr>`;
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

async function loadDashboard() {
  candidateRows.innerHTML = `<tr><td class="empty" colspan="8">讀取中...</td></tr>`;
  try {
    const response = await fetch(`${API_BASE_URL}/dashboard`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const rows = data.top_candidates || [];
    candidateCount.textContent = String(rows.length);
    latestUpdate.textContent = data.latest_update || "-";
    renderRows(rows);
  } catch (error) {
    candidateRows.innerHTML = `<tr><td class="error" colspan="8">API 連線失敗：${error.message}</td></tr>`;
  }
}

async function searchStocks() {
  const q = searchInput.value.trim();
  if (!q) {
    await loadDashboard();
    return;
  }
  try {
    const response = await fetch(`${API_BASE_URL}/search?q=${encodeURIComponent(q)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const rows = await response.json();
    candidateCount.textContent = String(rows.length);
    latestUpdate.textContent = "search";
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
  } catch (error) {
    candidateRows.innerHTML = `<tr><td class="error" colspan="8">搜尋失敗：${error.message}</td></tr>`;
  }
}

refreshButton.addEventListener("click", loadDashboard);
searchButton.addEventListener("click", searchStocks);
searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchStocks();
});

loadDashboard();
