# V4 台股微結構策略規格

本文件記錄「台股 AI 選股分析平台 V4」新增的四個左側潛伏子策略。這些策略用於降低布林壓縮、空單回補訊號的偽陽性，屬於決策輔助評分，不涉及券商登入或下單。

## 評分整合

- 入口：`taiwan_stock_screener/scoring/left_side.py`
- 輸入欄位：`microstructure_row`
- 輸出欄位：
  - `microstructure_score`
  - `window_dressing_score`
  - `jailbreak_score`
  - `cb_signal_score`
  - `geographic_broker_score`
- 每個子策略原始分數：`config.yaml` 的 `microstructure_strategy_points`，目前為 15 分
- 微結構構面總分上限：`left_side.weights.microstructure`，目前為 15 分
- 總分仍以 100 分封頂

資料未接上時不給分，也不推測訊號。

## 一、投信季底作帳（Window Dressing）

### 資料來源

- FinMind 三大法人買賣超
- 投信持股比例資料源待接

### 必要欄位

- `days_to_quarter_end`
- `trust_holding_ratio_pct`
- `trust_net_buy_5d`

### 觸發條件

- 距離季底小於等於 20 天
- 投信持股比例介於 3% 至 8%
- 近 5 日投信淨買超張數大於 0

### 前端標籤

- `window_dressing_setup`：投信作帳中

## 二、處置股出關潛伏（Jailbreak）

### 資料來源

- TWSE/TPEx 處置有價證券公告待接
- 千張大戶持股變化可由 FinMind 股權分散表補強

### 必要欄位

- `disposition_days_to_end`
- `disposition_range_pct`
- `big_holder_ratio_not_down` 或 `big_holder_ratio_change_pp`

### 觸發條件

- 距離處置結束小於等於 2 天
- 處置期間股價振幅小於 15%
- 千張大戶持股比例未下滑

### 前端標籤

- `jailbreak_setup`：處置即將出關

## 三、可轉債異常訊號（Convertible Bond）

### 資料來源

- 可轉債對照表待接
- 可轉債日成交資料待接
- 現股布林帶寬由既有技術指標提供

### 必要欄位

- `has_convertible_bond`
- `bb_bandwidth_percentile`
- `cb_price`
- `cb_volume_ratio`

### 觸發條件

- 現股布林帶寬百分位小於 10%
- 有對應可轉債
- 可轉債價格突破 105 元，或可轉債成交量大於 20 日均量 3 倍

### 前端標籤

- `cb_abnormal_signal`：CB 異常訊號

## 四、地緣券商異常吃貨（Geographic Broker Accumulation）

### 資料來源

- FinMind 個股券商分點進出明細已可用
- 公司登記地址與券商分點地址對照表待接

### 必要欄位

- `same_city_branch_buy_streak_days`
- `same_city_branch_buy_volume_pct`

### 觸發條件

- 同縣市券商分點連續 5 天以上淨買超
- 買超張數佔單日成交量 10% 以上

### 前端標籤

- `geographic_broker_accumulation`：地緣大戶進駐

## 已完成與待補

- 已完成：評分引擎欄位、門檻設定、前端標籤與微結構卡片
- 已完成：FinMind 分點 API 只分析入圍前 20 檔，降低 workflow 時間
- 已完成：處置股公告 collector（FinMind `TaiwanStockDispositionSecuritiesPeriod`，全市場一次；
  **實際欄位是 `period_start` / `period_end`**，2026-07-19 首輪 log 驗證後已修正 mapping）
- 已完成：可轉債對照與成交 collector（`TaiwanStockConvertibleBondInfo` ＋ 每檔日成交；
  CB 代號無股票欄位時以「去掉末碼」推導現股代號）
- **陷阱**：`TaiwanStockConvertibleBondDailyOverview` 只有發行條件（轉換價/賣回日），
  **沒有價量**——CB 價量必須用 `TaiwanStockConvertibleBondDaily`，fetch 端以
  `require_any=("close", ...)` 驗證，缺欄位的 dataset 會整輪封鎖並換下一個候選
- 已完成：公司縣市（公司基本資料 t187ap03 住址）×券商分點縣市（`TaiwanSecuritiesTraderInfo`）
  對照，餵給地緣券商訊號
- 已完成：投信近 5 日淨買超（沿用三大法人資料）
- **待補：投信持股比例**——FinMind 無現成 dataset，投信作帳訊號在此欄位補上前不會觸發
  （引擎規則：缺欄位不給分，不用估算值硬補）
- 純計算集中在 `taiwan_stock_screener/collectors/microstructure.py`（有單元測試）；
  抓取在 `scripts/run_screener.py`，dataset 名稱錯誤或等級不足會自我診斷並整輪跳過
