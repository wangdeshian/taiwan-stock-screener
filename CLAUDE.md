# AI 協作者專案記憶（Claude / Codex 必讀）

## 這個專案是什麼

台股 AI 選股分析平台：GitHub Actions 每交易日 15:10（台北時間）執行
`scripts/run_screener.py` → 產出 `frontend/data/*.json` → 發布到 GitHub Pages 靜態前端。
FastAPI/SQLite 部分是 sample-mode 骨架，生產路徑是上述靜態管線。

## 領域知識庫

**開發評分模型、因子、權重前，先讀 `docs/DOMAIN_KNOWLEDGE.md`**。
開發者會持續貼入財經文獻整理，AI 協作者負責去重、歸章節、標記實作狀態（✅🔶⬜）後提交。

## 雙策略架構

- **右側動能**（`scoring/engine.py`）：趨勢/量能/法人/籌碼/基本面/產業/風報比，門檻 55（live）
- **左側潛伏**（`scoring/left_side.py`）：底部結構 20／壓縮點火 10／空單回補 15／散戶絕望 10／
  聰明錢 15／基本面安全 10／催化劑 10／產業共振 10，門檻 70；權重與門檻全在 `config.yaml`
- 左側兩段式漏斗：①布林壓縮點火海選（yfinance 批次）＋籌碼起手式（`market_chip.py` 滾動快照）
  → ②入圍前 50 檔完整評分。觀察池為最後備援（排除動能重疊股）

## 資料源限制（重要，別重蹈覆轍）

- **FinMind 免費(register)等級**：全市場日期模式與 `TaiwanStockHoldingSharesPer`（股權分散表）
  回 400「Your level is register」；額度 600/hr，一小時內連跑兩輪篩選就會打光（候選數會掉）。
  Backer $699/月即解鎖，程式碼已寫好自動切換，不用改。
- **FinMind 財報科目陷阱**：損益表 dataset 裡的 `EquityAttributableToOwnersOfParent` 是
  「綜合損益歸屬」不是權益餘額；ROE 分母只能取自 `TaiwanStockBalanceSheet`。
  每輪執行會把兩個 dataset 的科目清單印進 log（自我診斷），科目異動先查 log 再改 mapping。
- **TWSE**：openapi（`openapi.twse.com.tw`）在 GitHub Actions 可用；`www.twse.com.tw` 的
  rwd 端點會回非 JSON（擋雲端 IP），別用。TWT93U/TWTB4U 的 openapi 路徑不存在。
- **TPEx** openapi 偶發 5xx，已有重試；失敗時左側範圍剩上市股。
- **ETF/ETN 不是產業**：產業資金共振已排除，別再讓 ETF 吃板塊加分。
- 催化劑資料源：目前讀 `frontend/data/catalysts.json`（手動維護），格式
  `[{"symbol","event_type","event_date"}]`；MOPS 爬蟲未實作。

## 工程慣例

- 測試：`pytest`（CI 只跑這個）；改前端後跑 `node --check frontend/app.js`
- 前端鐵律：資料更新時**列不可跳動**——以代號保持列序、展開狀態，滾動位置不變
- 手動觸發 workflow 驗證時注意 FinMind 額度：兩輪間隔至少 70 分鐘
- workflow 會提交 `frontend/data/`（results/history/chip_history/sector_history）
