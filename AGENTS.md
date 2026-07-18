# AI 協作者必讀（Codex / Claude 共用）

本專案由多個 AI 協作者（Claude、Codex）與開發者接力開發。GitHub
`wangdeshian/taiwan-stock-screener` 的 `main` 分支是唯一正式版本。
動手前必須先確認工作副本是最新狀態，不能覆蓋對方剛完成的修改。

## 開工儀式（每次都要）

```bash
git status --short
git fetch origin
git log --oneline --decorate -10
git pull --rebase --autostash origin main
git status --short
```

接著閱讀：

1. `docs/WORKLOG.md` 最上面一筆：上一位做了什麼、驗證到哪、建議下一步
2. `CLAUDE.md`：專案架構、資料源限制、已踩過的坑
3. `docs/DOMAIN_KNOWLEDGE.md`：評分模型、因子、權重與領域知識

如果使用者說「Claude 有修改」、「Codex 有修改」、「網頁沒有看到改動」或「幫我發布」，先額外檢查：

```bash
git log --oneline -10
git show --stat HEAD
```

必要時看指定檔案 diff。若工作區有未提交修改，先判斷來源，不得任意覆蓋、還原或刪除。

## 完工儀式（每次都要）

1. 後端或選股邏輯：跑 `pytest`
2. 前端 JavaScript：跑 `node --check frontend/app.js`
3. 正式網站或資料發布：觸發或檢查 `Daily Stock Screener`，再驗證
   `https://wangdeshian.github.io/taiwan-stock-screener/data/results.json`
4. 在 `docs/WORKLOG.md` 最上方新增一筆：做了什麼／驗證狀態／建議下一步／新踩的雷
5. commit message 要說清楚動機，不只寫做了什麼；不要把無關變更混在一起

## 鐵律

- 前端資料更新時**列不可跳動**：以代號保持列序、展開狀態與滾動位置
- 手動觸發 `Daily Stock Screener` 前，先確認沒有其他輪正在執行
- FinMind `TaiwanStockTradingDailyReport` 一次只能查一天，不得帶 `end_date`
- ETF/ETN 不是產業，不參與板塊共振與分點分析
- Token 只放 GitHub Secrets 或本機 `.env`，永不入庫，也不要貼到聊天內容

