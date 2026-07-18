# 協作日誌（Claude ↔ Codex ↔ 開發者 交接板）

## 使用規則

1. **開工前**（不分 Claude / Codex，一律照做）：
   - `git fetch && git pull` 確認拿到最新 main
   - `git log --oneline -10` 看最近誰改了什麼
   - 讀本檔**最上面一筆**紀錄：上一位做了什麼、驗證到哪、建議的下一步
   - 有疑問先看 `CLAUDE.md`（專案記憶）與 `docs/DOMAIN_KNOWLEDGE.md`（領域知識）
2. **完工後**：在「日誌」段落**最上方**新增一筆，格式照舊條目
3. 日誌只保留最近 20 筆，太舊的刪掉（git 歷史裡都有）

---

## 日誌（新的在上面）

### 2026-07-19（一）| Codex
- **做了**：改善 V4 微結構在手機網頁的可見性：切到「左側潛伏」時自動展開第一檔；舊 `results.json` 尚未含微結構欄位時，卡片顯示「未接」而不是空白；頁首改為 V4 Decision Support System
- **已驗證**：`node --check frontend/app.js` 通過
- **發布方式**：需同步 `frontend/index.html`、`frontend/app.js` 到 `gh-pages`，讓正式網站立即可見
- **未完成／進行中**：四個 V4 collector 尚未接上，未接欄位不給分

### 2026-07-18（四）| Codex
- **做了**：接入 V4 台股微結構策略骨架：`left_side.py` 新增 `microstructure_score` 與四個子分數（投信作帳、處置出關、CB 異常、地緣券商）；前端左側明細新增「台股微結構」區塊與四個 reason 標籤；新增 `docs/V4_MICROSTRUCTURE_STRATEGIES.md`
- **做了**：照前一輪急件完成分點提速：新增 env `SCREENER_BRANCH_ANALYZE_LIMIT`（預設 20），`branch_lookback_days` 10→5；輸出 `left_side_branch_analyze_limit`、`left_side_branch_lookback_days`、`left_side_branch_analyzed_count`
- **修正**：`refresh_chip_store` 在 FinMind 已正常回補時，週末不再 fallback 寫入 TWSE 當日快照，避免非交易日多出一筆日期
- **修正**：移除重複且失敗的 `.github/workflows/pages.yml`；正式網站仍由 `screener.yml` 發布到 `gh-pages`
- **已驗證**：`python -m pytest` 48 passed；`node --check frontend/app.js` 通過
- **未完成／進行中**：四個 V4 策略的 collector 尚未全部接上；資料未接上時分數維持 0，不得用推測值補分
- **建議下一步**：依序補 collector：投信持股比例 → 處置股公告 → 可轉債對照/成交 → 公司地址與券商分點地址對照表

### 2026-07-18（三）| Codex
- **做了**：合併 Claude 與 Codex 的協作規則，確認 `main` 為唯一正式版本；更新 `AGENTS.md`，要求每次開工先 `git status`、`git fetch`、看最新 commit、`git pull --rebase --autostash`，並保留 `docs/WORKLOG.md` 交接流程
- **已驗證**：處理 `AGENTS.md` add/add rebase 衝突與 `docs/WORKLOG.md` 日誌衝突，保留 Claude 的 WORKLOG 機制、最新分點驗證紀錄，以及 Codex 的正式網站驗證規則
- **未完成／進行中**：無程式邏輯變更，未重新觸發選股 workflow
- **建議下一步**：接續 Claude 最新急件：分點提速，將 `SCREENER_BRANCH_ANALYZE_LIMIT` 預設 20，並評估 `branch_lookback_days` 10→5，目標整輪 <30 分鐘
- **地雷提醒**：若 `git push` 出現 `fetch first`，先 `git pull --rebase --autostash origin main`，檢查衝突後再推

### 2026-07-18（二）| Claude
- **做了**：分點資料**驗證完成**——17/20 檔入圍股有完整分點數據（其餘 3 檔為 ETF，正確跳過）
- **驗證細節**：集中度 8~31%（皆低於 50% 門檻，全數判為「沉寂」階段——左側潛伏股本
  該如此，等出現吸貨/紊亂時訊號才有區分度）；隔日沖佔比 0~0.13%（冷門股無隔日沖客，合理）；
  主力成本線有意義（如玉山金收盤 35.1 剛站上成本線 34.64；景碩 693 vs 成本線 773 主力尚套）
- **問題**：整輪執行 **~2 小時**（逐日抓分點 50 檔 × 10 日），放每日排程太慢
- **建議下一步（急）**：分點提速——只對入圍前 20 檔抓（env `SCREENER_BRANCH_ANALYZE_LIMIT`
  預設 20）＋ `branch_lookback_days` 10→5；目標整輪 <30 分鐘（已交辦 Codex）
- Backlog 不變：F-Score → 地雷股負向濾網 → KD/RSI 評分 → MOPS 催化劑爬蟲 → 聲量爬蟲

### 2026-07-18（一）| Claude
- **做了**：Sponsor 升級後補齊分點追蹤（`collectors/broker_flow.py`：前十大集中度/隔日沖黑名單/主力成本線/籌碼階段）＋券資比軋空訊號；修復分點 dataset 單日限制（改逐日抓、ETF 跳過）；建立本協作日誌機制
- **已驗證**：全市場批次回補 FinMind×15（58 萬筆）、千張大戶 20/20、券資比 20/20、全市場 945 檔、ROE 正常（分母改資產負債表）
- **地雷提醒**：手動觸發 workflow 驗證前先確認沒有其他輪在跑；`TaiwanStockTradingDailyReport` 不能帶 end_date

### 2026-07-13 ~ 07-17 | Claude ＋ Codex（歷史摘要）
- 雙策略上線：右側動能＋左側潛伏（兩段式漏斗：壓縮點火海選＋籌碼起手式）
- Codex 貢獻：產業欄/基本面補齊、觀察池、催化劑＋產業共振、極度壓縮門檻 5→10、workflow rebase 修正
- Claude 貢獻：左側引擎、全市場快照、壓縮點火、免費額度降級方案、多輪資料源除錯
- 重要修正：ROE 假權益科目、ETF 不吃板塊分、觀察池排除動能股、pandas 3 NAType
