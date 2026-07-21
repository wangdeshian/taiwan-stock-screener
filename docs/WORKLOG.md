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

### 2026-07-21（四）| Claude
- **驗證輪結果**（run 29790897551，帶 #16 修正）：
  - ✅ 微結構資料流通：20/20 檔 microstructure_available=true；處置股欄位
    period_start/period_end 確認；`TaiwanStockConvertibleBondDaily` 確認有 close
  - ✅ 分點平行預抓：**20 檔 × 9 天只花 10 秒**（原序列版推測 1 小時的部分其實不是兇手）
  - ❌ 整輪仍 3 小時 09 分——瓶頸不在分點！log 時間戳全被 Python 緩衝擠到最後，
    無法定位慢的階段
  - ⚠ 地緣券商命名對不上：分點名稱對縣市命中率僅 48/818
- **做了**：workflow 加 `PYTHONUNBUFFERED=1`（log 每行帶真實時間戳）＋
  `PHASE xxx: +Ns` 階段計時（quotes／industry+sector+catalyst／momentum／
  left-chip-refresh／left-squeeze-scan／left-stage2-loop）；下一輪 log 直接看哪段吃掉 2.5 小時
- **做了**：CB 成交量欄位補 `unit`（FinMind CB Daily 無 volume 欄）；地緣券商命中率低時
  印雙邊名稱樣本，供設計正規化規則
- **建議下一步（急）**：看 15:10 排程輪的 PHASE 計時抓真兇；照樣本修分點↔縣市名稱正規化

### 2026-07-21（三）| Claude
- **做了**：新增「⚡ 訊號加速」——左側評分與自己前幾天比、數日內大幅跳升就標記
  （案例：2493 揚博 07-16 得 23.5 分、07-20 跳到 63.5、07-21 漲停——訊號其實提早出現，
  但被 70 分絕對門檻埋掉；3189 景碩 07-18 得 54 分同型）。門檻在 config：
  `acceleration_min_delta` 20 分／`acceleration_window_days` 5 天／`acceleration_min_score` 45
- **做了**：history.json 新增 `left_side_scores` 全表（含未進榜低分股），讓隔日比對有完整基準；
  前端列表分數旁顯示 ▲/▼ 變化徽章＋⚡ reason 標籤
- **已驗證**：pytest 59 passed、node --check 通過
- **注意**：加速訊號需要兩天以上的 history 才會開始出現；`left_side_scores` 07-21 起才有
- **建議下一步**：觀察加速訊號的命中率；考慮把「score_delta 大＋股價還在跌」再細分成獨立標記

### 2026-07-21（二）| Claude
- **做了**：催化劑自動化第一步——法說會接 TWSE/TPEx openapi（`t187ap38_L`／
  `mopsfin_t187ap38_O`，自我診斷模式印筆數＋鍵名）；民國日期解析
  `parse_roc_or_iso_date`；與手動 `catalysts.json` 合併去重（手動優先）
- **做了**：前端「近期催化」區分三態：有事件／已接但近期無事件／未接
  （靠新輸出欄位 `catalyst_event_count`）
- **已驗證**：pytest 58 passed；`node --check frontend/app.js` 通過。openapi 端點名稱
  沙箱不可外連、未實測——看下一輪 log 的 `catalyst-twse-conference` / `catalyst-tpex-conference`
  行，若 404 或鍵名對不上照 log 修
- **建議下一步**：確認法說會事件有進 results.json → 之後擴充除權息、股東會事件源

### 2026-07-21 | Claude
- **做了**：讀 07-19 首輪 log 的微結構自我診斷，修正兩個 dataset mapping bug——
  ①處置股欄位實際是 `period_start`/`period_end`（原找 start_date/end_date 對不上→全 None）；
  ②CB 日成交誤用 `TaiwanStockConvertibleBondDailyOverview`（只有發行條件、無價量），
  改以 `TaiwanStockConvertibleBondDaily` 為主＋`require_any` 欄位驗證
- **做了**：分點改平行預抓（`prefetch_broker_flows`：ThreadPoolExecutor，env
  `SCREENER_BRANCH_WORKERS` 預設 8、`SCREENER_BRANCH_TIME_BUDGET` 預設 600 秒，
  超時用部分資料）——07-19 那輪整輪跑了 3 小時 11 分，分點序列抓是主因
- **做了**：新增地緣券商命名健檢 log（分點名稱對縣市的命中率），驗證
  `TaiwanSecuritiesTraderInfo` 與分點報表的名稱是否對得起來
- **已驗證**：pytest 56 passed；07-19 log 確認四個 micro dataset 名稱全部有效
  （disposition 689 筆、CB 對照 1800 筆、分點基本資料 1010 筆）
- **注意**：微結構 0 分有一部分是**正常的**——投信作帳缺持股比例欄位不觸發、
  現在距季底 >20 天也不會觸發；處置/CB/地緣修好後要等訊號股出現才有分
- **建議下一步**：看下一輪 log 確認整輪時間 <30 分、`Branch trader-city match` 命中率、
  micro-cb-daily 的 Daily 欄位清單；之後做權重 115→100 校準

### 2026-07-19 | Claude
- **做了**：接上 V4 微結構四大資料源（Codex 骨架的「待補」清單）——處置股公告（全市場
  一次）、可轉債對照＋日成交（每檔 CB 一次）、公司縣市×分點縣市對照（地緣券商）、
  投信近 5 日淨買超；新增 `collectors/microstructure.py` 純計算模組＋8 個單元測試
- **未接**：投信持股比例（FinMind 無現成 dataset）→ 投信作帳訊號暫不會觸發，引擎缺欄位不給分
- **注意（校準提醒）**：目前左側權重總和 = **115**（微結構 15 為外加），總分封頂 100。
  微結構資料接上後門檻 70 會變相變鬆——建議下次執行觀察候選數變化，必要時配平權重回 100
  或把 candidate_score 調到 75~80
- **驗證方式**：dataset 名稱若與 FinMind 實際不符，log 會印出錯誤與成功時的欄位清單
  （自我診斷模式），照 log 修 mapping 即可
- **建議下一步**：看首輪 log 修 dataset/欄位名 → 觀察微結構訊號觸發頻率與候選數 → 校準權重

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
