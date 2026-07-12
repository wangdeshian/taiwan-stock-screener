// 讀取路徑：GitHub Pages 上的靜態 JSON（由 GitHub Actions 每日更新）
const RESULTS_URL = "./data/results.json";

const candidateRows  = document.getElementById("candidateRows");
const candidateCount = document.getElementById("candidateCount");
const latestUpdate   = document.getElementById("latestUpdate");
const refreshButton  = document.getElementById("refreshButton");
const searchButton   = document.getElementById("searchButton");
const searchInput    = document.getElementById("searchInput");
const notice         = document.getElementById("notice");

let allCandidates = [];

function money(v) {
  if (v === null || v === undefined) return "-";
  return Number(v).toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function setNotice(msg = "") {
  notice.hidden = !msg;
  notice.textContent = msg;
}

function renderRows(rows) {
  candidateRows.innerHTML = "";
  if (!rows.length) {
    candidateRows.innerHTML =
      `<tr><td class="empty" colspan="8">目前沒有符合條件的候選股</td></tr>`;
    return;
  }
  for (const item of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${item.symbol}</td>
      <td>${item.name}</td>
      <td>${item.industry || "-"}</td>
      <td class="score">${money(item.total_score)}</td>
      <td>${money(item.entry_price)}</td>
      <td>${money(item.stop_loss_price)}</td>
      <td>${money(item.target_price_1)}</td>
      <td>${money(item.risk_reward_ratio)}</td>`;
    candidateRows.appendChild(tr);
  }
}

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

async function loadData() {
  candidateRows.innerHTML =
    `<tr><td class="empty" colspan="8">讀取中...</td></tr>`;
  setNotice("");

  try {
    // 加 timestamp 避免瀏覽器快取舊資料
    const resp = await fetch(`${RESULTS_URL}?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    allCandidates = data.top_candidates || [];
    candidateCount.textContent = allCandidates.length;

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

    if (!data.has_institutional_data) {
      setNotice(
        "目前為免費模式（純技術面評分）。在 GitHub Secrets 設定 FINMIND_TOKEN 可啟用法人、基本面評分。"
      );
    }

    renderRows(allCandidates);
  } catch (err) {
    console.error(err);
    candidateCount.textContent = "0";
    latestUpdate.textContent = "-";
    setNotice(
      "尚無篩選資料。請到 GitHub Actions → Daily Stock Screener → Run workflow 手動觸發一次。"
    );
    candidateRows.innerHTML =
      `<tr><td class="empty" colspan="8">尚無資料</td></tr>`;
  }
}

refreshButton.addEventListener("click", loadData);
searchButton.addEventListener("click", applySearch);
searchInput.addEventListener("keydown", e => {
  if (e.key === "Enter") applySearch();
});

loadData();
