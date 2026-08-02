# QuotaBar

macOS 選單列常駐小工具，同時顯示 **Codex / Claude Code / Gemini** 三家的本機用量。
所有數字都在本機讀取，不連任何伺服器、不上傳帳號、對話或用量。

```
選單列：  CX 91%  CL 62%  GM 100%
```

點開後是一個毛玻璃面板，每家一張卡片：剩餘百分比、進度條、各區間的重置倒數，
左上角有一個「安全等級」圓環，取三家中最吃緊的那個。

## 這個工具跟原版 GlassQuota 的差別

參考的原版（`GlassQuota`）做 Codex + Gemini 兩家，Gemini 是靠 macOS 輔助功能去「讀」
官方桌面 App 畫面上的文字。這版的差別：

| | 原版 GlassQuota | QuotaBar |
|---|---|---|
| Codex | 讀 `~/.codex/sessions` | 同（格式改用遞迴搜尋，版本改名也不會壞） |
| Gemini | 輔助功能抓官方 App 畫面 | 讀 Gemini **CLI** 的 `logs.json` 算請求數 |
| Claude | 無 | 讀 Claude Code 對話記錄統計 token |
| 權限 | 需要輔助功能授權 | 不需要任何特殊權限 |

## 資料來源（以及它們各自誠實的限制）

### Codex —— 真的是官方額度

Codex CLI 每次呼叫模型後，會把伺服器回傳的 `rate_limits` 寫進
`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`：

```json
{"rate_limits":{"primary":{"used_percent":9.0,"window_minutes":300,"resets_in_seconds":3600},
                "secondary":{"used_percent":42.5,"window_minutes":10080,"resets_in_seconds":86400}}}
```

這是官方數字，準確。程式從最新的 session 檔往前找最後一筆，所以**你多久沒用 Codex，
數字就多久沒更新**——這是本機讀取的先天限制，不是 bug。

### Claude —— 是本機 token 統計，不是官方額度

**這點要講清楚**：Claude Code 沒有把官方額度百分比寫在本機任何檔案裡，`/usage` 看到的
數字是即時跟伺服器要的。所以這裡做的是「把 `~/.claude/projects/**/*.jsonl` 裡的
`message.usage` 加總」，得到 5 小時滾動窗與 7 天的 token 用量。

因此預設**只顯示 token 數量，不顯示百分比**。要百分比的話得自己在設定檔填一個
token 預算（`sessionTokenBudget`），程式才會拿它當分母——那是你自己校準的參考值，
不是 Anthropic 公布的額度。與其顯示一個看起來像官方數字的猜測值，不如不顯示。

想從實際用量反推自己的預算：跑幾天 `quotabar --once`，看你在觸發限制那一刻的 token 數。

### Gemini —— 讀 CLI，不是桌面 App

讀 `~/.gemini/tmp/<hash>/logs.json` 裡 `type == "user"` 的筆數，算「今日請求數」與
「最近一分鐘請求數」，對應免費層的 1000/日、60/分。換日用太平洋時間（Google 的重置時區）。

**沒做的部分**：原版那種用輔助功能去讀 Gemini 官方桌面 App 畫面的做法沒有實作。
那需要 AX API 去戳別人 App 的 UI 樹、要求輔助功能授權，而且對方改版就會壞。
如果你要的是桌面 App 的數字而不是 CLI 的，這條路要另外接。

## 建置與執行

需要 macOS 14 以上、Swift 6（Xcode 16）。

```bash
cd tools/quota-menubar
swift build -c release
.build/release/quotabar          # 常駐選單列
```

想開機自動啟動，把執行檔路徑加到「系統設定 → 一般 → 登入項目」。

### 先跑診斷再說

第一次用**先跑這個**，它會印出三家的目錄、檔案數、最新一行原始 JSON，以及實際解析結果：

```bash
swift run quotabar --probe
```

如果哪一家顯示「不可用」，`--probe` 的輸出會直接指出是目錄不存在、檔案是空的、
還是格式跟預期對不上。格式對不上時，把最後一行原始 JSON 貼出來就能對照修解析。

其他參數：

```bash
swift run quotabar --once      # 解析一次印出結果後結束（適合塞進其他 status bar 工具）
swift run quotabar --config    # 印出設定檔路徑與內容
```

### 測試

```bash
swift test
```

`QuotaCore`（所有解析邏輯）是純 Foundation，Linux 上也能編譯與測試；
`QuotaBar`（SwiftUI 介面）只在 macOS 上納入建置目標，見 `Package.swift` 的 `#if os(macOS)`。

## 設定檔

路徑 `~/.config/quotabar/config.json`，**每個欄位都可以省略**，省略就用預設值：

```json
{
  "refreshSeconds": 5,
  "hideUnavailable": true,
  "codex": {
    "enabled": true,
    "sessionsDir": null,
    "maxFilesToScan": 8
  },
  "claude": {
    "enabled": true,
    "projectsDir": null,
    "sessionWindowHours": 5,
    "sessionTokenBudget": null,
    "weeklyTokenBudget": null
  },
  "gemini": {
    "enabled": true,
    "logsRoot": null,
    "dailyRequestLimit": 1000,
    "minuteRequestLimit": 60,
    "dailyResetTimeZone": "America/Los_Angeles"
  }
}
```

- `hideUnavailable`：抓不到資料的供應商是否從選單列文字中隱藏（面板裡仍會列出並說明原因）
- `sessionsDir` / `projectsDir` / `logsRoot`：填 `null` 就用預設路徑，支援 `~` 開頭
- 想關掉某一家就把它的 `enabled` 設成 `false`

## 設計上的一條規則

**抓不到資料就顯示「—」，絕不補一個看起來合理的數字。**
額度顯示器最糟的失敗方式是「明明沒讀到，卻顯示 100%」——你會信它，然後撞到限制。
所以每個供應商在找不到目錄、檔案為空、或欄位對不上時，一律回報不可用並在面板寫出原因。

## 授權

MIT，跟隨本 repo。與 OpenAI、Google、Anthropic 均無隸屬關係；Codex、Gemini、Claude
及相關標誌是其各自權利人的商標。
