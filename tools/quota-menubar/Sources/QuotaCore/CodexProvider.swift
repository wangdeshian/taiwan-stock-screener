import Foundation

/// 從 Codex CLI 的本機 session 記錄讀最新一筆速率限制事件。
///
/// Codex 每次呼叫模型後會把 `rate_limits` 寫進 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`，
/// 內容大致是：
/// ```json
/// {"rate_limits":{"primary":{"used_percent":12.3,"window_minutes":300,"resets_in_seconds":9000},
///                 "secondary":{"used_percent":3.5,"window_minutes":10080,"resets_in_seconds":500000}}}
/// ```
/// 這個物件在不同版本可能包在 `payload` 底下，所以用遞迴搜尋而不是固定路徑。
public final class CodexProvider: QuotaProvider {
    public let id = "codex"
    public let displayName = "Codex"
    public let shortName = "CX"

    private let config: CodexConfig

    public init(config: CodexConfig = CodexConfig()) {
        self.config = config
    }

    public var sessionsDirectory: URL {
        Paths.expand(config.sessionsDir, default: "~/.codex/sessions")
    }

    public func snapshot(now: Date) -> ProviderSnapshot {
        let directory = sessionsDirectory
        let files = FileScan.files(in: directory, pathExtension: "jsonl")
        guard !files.isEmpty else {
            return unavailable("找不到 Codex session：\(directory.path)", now: now)
        }

        guard let found = latestRateLimits(in: files) else {
            return unavailable("Codex session 裡還沒有 rate_limits 事件（先跑一次 codex 再看）", now: now)
        }

        let windows = Self.windows(from: found.limits, now: now)
        guard !windows.isEmpty else {
            return unavailable("讀到 rate_limits 但沒有可用欄位，請用 --probe 檢查格式", now: now)
        }

        return ProviderSnapshot(
            id: id,
            displayName: displayName,
            shortName: shortName,
            available: true,
            windows: windows,
            note: "來源：\(found.file.lastPathComponent)",
            updatedAt: now
        )
    }

    /// 由新到舊掃 session 檔，每個檔案由最後一行往前找，找到第一筆就停。
    func latestRateLimits(in files: [ScannedFile]) -> (limits: [String: Any], file: URL)? {
        for file in files.prefix(max(1, config.maxFilesToScan)) {
            let lines = FileScan.lines(of: file.url)
            for line in lines.reversed() {
                // 先做便宜的字串比對再解析 JSON；兩種命名都要接受。
                guard line.contains("rate_limit") || line.contains("rateLimit") else { continue }
                guard let object = JSONUtil.parseObject(line) else { continue }
                guard let limits = JSONUtil.findObject(keys: ["rate_limits", "rateLimits"], in: object) else { continue }
                return (limits, file.url)
            }
        }
        return nil
    }

    static func windows(from limits: [String: Any], now: Date) -> [QuotaWindow] {
        // primary / secondary 是目前的欄位名；順序固定，讓 5 小時窗永遠排在週窗前面。
        let ordered = ["primary", "secondary"]
        var windows: [QuotaWindow] = []

        for key in ordered {
            guard let entry = limits[key] as? [String: Any] else { continue }
            if let window = self.window(from: entry, now: now) {
                windows.append(window)
            }
        }

        // 未來若改名，把剩下的字典也撈進來，至少不會整個空掉。
        if windows.isEmpty {
            for (key, value) in limits.sorted(by: { $0.key < $1.key }) {
                guard let entry = value as? [String: Any] else { continue }
                if var window = self.window(from: entry, now: now) {
                    if window.label == "額度" { window.label = key }
                    windows.append(window)
                }
            }
        }

        return windows
    }

    static func window(from entry: [String: Any], now: Date) -> QuotaWindow? {
        guard let usedPercent = JSONUtil.double(
            JSONUtil.value(entry, "used_percent", "usedPercent", "percent_used", "percentUsed")
        ) else { return nil }

        let minutes = JSONUtil.int(JSONUtil.value(entry, "window_minutes", "windowMinutes"))
        let label = minutes.map { Format.windowLabel(minutes: $0) } ?? "額度"

        var resetsAt: Date?
        if let seconds = JSONUtil.double(JSONUtil.value(entry, "resets_in_seconds", "resetsInSeconds")) {
            resetsAt = now.addingTimeInterval(seconds)
        } else if let text = JSONUtil.string(JSONUtil.value(entry, "resets_at", "resetsAt")) {
            resetsAt = ISODate.parse(text)
        }

        return QuotaWindow(
            label: label,
            usedPercent: usedPercent,
            detail: String(format: "已用 %.1f%%", usedPercent),
            resetsAt: resetsAt
        )
    }
}
