# QuotaTray

Windows 系統匣常駐小工具，同時顯示 **Codex / Claude Code / Gemini** 三家的本機用量。
所有數字都在本機讀取，不連任何伺服器、不上傳帳號、對話或用量。

```
系統匣提示：  QuotaTray  CX 91%  CL 62%  GM 96%
```

圖示是一個環，缺口長度代表剩餘量，顏色代表嚴重程度（50% 以上綠、20–50% 橘、20% 以下紅，
沒有資料則是灰色橫槓）。點一下跳出詳細面板，每家一張卡片，含進度條與重置倒數。

## 快速開始

```bat
cd taiwan-stock-screener

REM 先看看讀不讀得到你的資料（不需要裝任何套件）
python -m tools.quota_tray --probe

REM 讀一次
python -m tools.quota_tray --once

REM 系統匣常駐（需要下面的套件）
pip install -r tools\quota_tray\requirements.txt
python -m tools.quota_tray
```

想開機自動啟動又不要跳出黑色主控台視窗，用 `pythonw` 建一個捷徑：

```
pythonw.exe -m tools.quota_tray
```

把捷徑丟進 `shell:startup` 資料夾（Win+R 輸入 `shell:startup`）即可。

## 指令

| 指令 | 用途 |
|---|---|
| `--probe` | 印出診斷報告：找到哪些家目錄、每家的檔案數、最新一行原始 JSON、解析結果 |
| `--once` | 讀一次印出結果後結束 |
| `--watch` | 在終端機持續刷新，不需要系統匣套件 |
| `--json` | 以 JSON 輸出，方便接其他工具 |
| `--config` | 印出設定檔路徑與內容 |
| `--write-config` | 產生一份預設設定檔 |

只有系統匣模式需要 `pystray` / `pillow`；其餘模式純標準函式庫。

## WSL 也會一起找

Windows 上這些 CLI 有可能裝在原生 Windows，也可能跑在 WSL 裡，兩邊家目錄完全不同。
QuotaTray 預設會把 `C:\Users\你` 和 `\\wsl.localhost\<發行版>\home\你` 都掃一遍再合併。
不想要就在設定檔把 `search_wsl` 設成 `false`。WSL 沒啟動時 UNC 路徑會存取失敗，這種情況
會直接跳過，不會卡住。

## 資料來源（以及它們各自誠實的限制）

### Codex —— 真的是官方額度

Codex CLI 每次呼叫模型後，會把伺服器回傳的 `rate_limits` 寫進
`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`：

```json
{"rate_limits":{"primary":{"used_percent":9.0,"window_minutes":300,"resets_in_seconds":3600},
                "secondary":{"used_percent":42.5,"window_minutes":10080,"resets_in_seconds":86400}}}
```

這是官方數字，準確。但它是「最後一次呼叫模型時的快照」——**你多久沒用 Codex，
數字就多久沒更新**。這是本機讀取的先天限制，不是 bug。

### Claude —— 是本機 token 統計，不是官方額度

**這點要講清楚**：Claude Code 沒有把官方額度百分比寫在本機任何檔案裡，`/usage` 看到的
數字是即時跟伺服器要的。所以這裡做的是「把 `~/.claude/projects/**/*.jsonl` 裡的
`message.usage` 加總」，得到 5 小時滾動窗與 7 天的 token 用量（四種 token 都算：
input、output、cache 寫入、cache 讀取）。

因此預設**只顯示 token 數量，不顯示百分比**。要百分比的話得自己在設定檔填一個
token 預算（`session_token_budget`），程式才會拿它當分母——那是你自己校準的參考值，
不是 Anthropic 公布的額度。與其顯示一個看起來像官方數字的猜測值，不如不顯示。

想抓自己的預算：跑幾天 `--once`，記下你撞到限制那一刻的 token 數，拿它當分母。

### Gemini —— 讀 CLI，不是桌面 App

讀 `~/.gemini/tmp/<hash>/logs.json` 裡 `type == "user"` 的筆數，算「今日請求數」與
「最近一分鐘請求數」，對應免費層的 1000/日、60/分。換日用太平洋時間（Google 的重置時區），
這需要 `tzdata` 套件；沒裝的話會退回本機時區並在卡片備註寫明。

**沒做的部分**：Gemini 官方桌面 App 的用量頁沒有接。那需要去讀別的程式的視窗內容
（原版 GlassQuota 在 macOS 上是用輔助功能 API 做的），Windows 上要用 UI Automation，
對方改版就會壞。目前只支援 Gemini CLI。

## 設定檔

Windows 路徑是 `%APPDATA%\QuotaTray\config.json`，用 `--write-config` 可以產生一份。
**每個欄位都可以省略**，省略就用預設值：

```json
{
  "refresh_seconds": 5.0,
  "hide_unavailable": true,
  "search_wsl": true,
  "extra_home_dirs": [],
  "codex": { "enabled": true, "sessions_dir": null, "max_files_to_scan": 8 },
  "claude": {
    "enabled": true,
    "projects_dir": null,
    "session_window_hours": 5.0,
    "session_token_budget": null,
    "weekly_token_budget": null
  },
  "gemini": {
    "enabled": true,
    "logs_root": null,
    "daily_request_limit": 1000,
    "minute_request_limit": 60,
    "daily_reset_timezone": "America/Los_Angeles"
  }
}
```

- `hide_unavailable`：抓不到資料的供應商是否從系統匣文字中隱藏（面板裡仍會列出並說明原因）
- `sessions_dir` / `projects_dir` / `logs_root`：填 `null` 就自動找，也可以指定絕對路徑
- `extra_home_dirs`：額外要掃的家目錄，例如另一個帳號
- 想關掉某一家就把它的 `enabled` 設成 `false`

## 設計上的一條規則

**抓不到資料就顯示「—」，絕不補一個看起來合理的數字。**
額度顯示器最糟的失敗方式是「明明沒讀到，卻顯示 100%」——你會信它，然後撞到限制。
所以每個供應商在找不到目錄、檔案為空、或欄位對不上時，一律回報不可用並在面板寫出原因，
系統匣圖示也會畫成灰色橫槓而不是滿環。

## 測試

解析邏輯的測試在 `tests/test_quota_tray.py`，跟著專案的 `pytest` 一起跑：

```bash
python -m pytest tests/test_quota_tray.py -q
```

測試只涵蓋純資料層（不碰 pystray / tkinter），所以在無頭環境也能跑。
系統匣與面板的實際外觀請在 Windows 上自己看一眼。
