# Taiwan Stock AI Screener V3

台股 AI 選股分析平台 V3，定位為決策輔助系統，不包含券商登入、自動下單、改單、刪單、庫存同步或即時成交回報。

## 功能

- FastAPI 後端
- SQLite MVP 資料庫，保留 PostgreSQL 擴充空間
- TWSE、TPEx、FinMind、MOPS、FRED collector 介面
- 技術指標：MA、EMA、RSI、MACD、KD、ATR、Bollinger Bands、OBV、ADX、VWAP、量比
- 雙策略 100 分 AI 評分模型：
  - 右側動能：趨勢、量能、法人、籌碼、基本面、產業、風報比
  - 左側潛伏：底部結構（低基期＋布林通道壓縮）、空單回補（借券賣出餘額大減）、
    散戶絕望（融資大減＋當沖冷清＋量能萎縮）、聰明錢（千張大戶增持＋投信微幅買超）、
    基本面安全、網路聲量情緒（預留欄位，爬蟲尚未串接）
- 左側潛伏採全市場兩段式漏斗：
  1. 以 TWSE/TPEx 全市場批次報表（MI_MARGN 融資融券、TWT93U 借券賣出餘額、TWTB4U 當沖統計）
     建立滾動籌碼快照（`frontend/data/chip_history.csv`，自動回補歷史、保留 45 個交易日）
  2. 對全市場（收盤 > 10 元、成交值 > 3 千萬）計算「佈局起手式」訊號初選，
     入圍前 50 檔才抓取完整歷史股價與 FinMind 資料做六構面評分
- 左側策略深度籌碼數據源：交易所批次快照＋FinMind（大戶持股、法人、個股籌碼備援）
- Top 20 候選股 API
- 交易計畫分析：建議進場、替代進場、停損、目標價、風險報酬比
- Firebase Hosting ready 靜態前端
- Pytest 測試
- GitHub Actions CI

## 不開發項目

本專案永久移除：

- 富邦 Neo API
- 券商登入
- 下單
- 改單
- 刪單
- 庫存同步
- 即時成交回報
- 自動交易

## 專案結構

```text
taiwan-stock-screener/
├── taiwan_stock_screener/
│   ├── api/
│   ├── backtest/
│   ├── collectors/
│   ├── database/
│   ├── indicators/
│   ├── jobs/
│   ├── scoring/
│   ├── services/
│   └── strategy/
├── dashboard/
├── frontend/
├── firebase/
├── tests/
├── config.yaml
├── firebase.json
├── requirements.txt
└── pyproject.toml
```

## 快速開始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m taiwan_stock_screener.jobs.daily_update
uvicorn taiwan_stock_screener.api.main:app --reload
```

開啟：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/candidates
```

## Streamlit Dashboard

```bash
streamlit run dashboard/app.py
```

## Firebase Hosting 前端

前端位於 `frontend/`，可直接用 Firebase Hosting 部署。

```bash
firebase init hosting
firebase deploy --only hosting
```

## GitHub Pages 發布

已加入 GitHub Pages 自動部署 workflow。推送到 `main` 後會部署 `frontend/`。

預期網址：

```text
https://wangdeshian.github.io/taiwan-stock-screener/
```

若 Repository 維持 Private，GitHub Pages 是否可公開瀏覽取決於 GitHub 帳號方案與 Pages 設定。若 Actions 顯示 Pages 尚未啟用，請到 GitHub Repository 的 `Settings -> Pages`，將 Source 設為 `GitHub Actions`。

預設前端會讀取：

```text
http://127.0.0.1:8000
```

可以在瀏覽器 localStorage 設定：

```js
localStorage.setItem("TAIWAN_STOCK_API_BASE_URL", "https://your-api.example.com")
```

## 環境變數

所有 Token 都只能放在 `.env`、GitHub Secrets 或雲端 Secret，不得提交到 GitHub。

```text
FINMIND_TOKEN=
FRED_API_KEY=
DATABASE_URL=sqlite:///./data/taiwan_stock_screener.db
```

## API

- `GET /health`
- `GET /stocks`
- `GET /stocks/{symbol}`
- `GET /search?q=台積電`
- `GET /candidates?limit=20`
- `GET /dashboard`
- `POST /jobs/update`
- `POST /backtest`
- `GET /watchlist/{user_id}`
- `PUT /watchlist/{user_id}/{symbol}`
- `DELETE /watchlist/{user_id}/{symbol}`

## 測試

```bash
pytest
```

## 已知限制

- MVP 預設啟用 sample data，可在沒有 API Token 的情況下跑完整流程。
- 真實資料來源 endpoint 與欄位可能會因資料提供者調整而需要更新 mapping。
- Firestore 同步與 Cloud Messaging 在此版本提供結構與前端基礎，尚未啟用雲端認證流程。
- 評分模型目前為規則型模型，後續可加入回測績效自動調整權重。
