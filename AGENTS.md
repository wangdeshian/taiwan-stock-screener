# AI 協作者必讀（Codex / Claude 共用）

本專案由多個 AI 協作者（Claude、Codex）與開發者接力開發。**動手前先完成開工儀式**：

## 開工儀式（每次都要）

1. `git fetch && git pull`——確認在最新 main 上工作
2. `git log --oneline -10`——看最近的提交，避免重工或蓋掉別人的修正
3. 讀 `docs/WORKLOG.md` **最上面一筆**——上一位做了什麼、驗證到哪、建議的下一步
4. 讀 `CLAUDE.md`——專案架構、資料源地雷（很多是實際踩過的坑，別重蹈覆轍）
5. 開發評分模型/因子前，讀 `docs/DOMAIN_KNOWLEDGE.md`——領域知識與實作狀態標記

## 完工儀式（每次都要）

1. `pytest` 全綠；改過前端要 `node --check frontend/app.js`
2. 在 `docs/WORKLOG.md` 最上方新增一筆：做了什麼／驗證狀態／建議下一步／新踩的雷
3. 提交訊息寫清楚動機，不只寫做了什麼

## 鐵律

- 前端資料更新時**列不可跳動**（以代號保持列序與展開狀態）
- 手動觸發 Daily Stock Screener 前，先確認沒有其他輪正在執行
- FinMind `TaiwanStockTradingDailyReport` 一次只能查一天（不得帶 end_date）
- ETF/ETN 不是產業，不參與板塊共振與分點分析
- Token 只放 GitHub Secrets／`.env`，永不入庫
