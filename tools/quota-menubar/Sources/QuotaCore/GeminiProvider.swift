import Foundation

/// 由 Gemini CLI 的本機 log 統計請求數。
///
/// Gemini CLI 把每一則使用者訊息寫進 `~/.gemini/tmp/<hash>/logs.json`（一個 JSON 陣列，
/// 元素形如 `{"sessionId":..,"messageId":0,"timestamp":"...","type":"user","message":".."}`）。
/// 免費層是「每分鐘 / 每日請求數」制，所以這裡算請求筆數而不是 token。
///
/// 注意：這讀的是 **Gemini CLI**。原版 GlassQuota 讀的是 Gemini 官方 macOS App 的畫面
/// （靠輔助功能 AX 抓文字），那條路徑沒有實作 —— 見 README 的「沒做的部分」。
public final class GeminiProvider: QuotaProvider {
    public let id = "gemini"
    public let displayName = "Gemini"
    public let shortName = "GM"

    private let config: GeminiConfig

    public init(config: GeminiConfig = GeminiConfig()) {
        self.config = config
    }

    public var logsRoot: URL {
        Paths.expand(config.logsRoot, default: "~/.gemini/tmp")
    }

    public func snapshot(now: Date) -> ProviderSnapshot {
        let root = logsRoot
        guard FileManager.default.fileExists(atPath: root.path) else {
            return unavailable("找不到 Gemini CLI 記錄：\(root.path)", now: now)
        }

        let files = FileScan.files(in: root, fileName: "logs.json")
        guard !files.isEmpty else {
            return unavailable("Gemini CLI 目錄下沒有 logs.json", now: now)
        }

        var timestamps: [Date] = []
        for file in files {
            timestamps.append(contentsOf: Self.userMessageDates(in: file.url))
        }

        guard !timestamps.isEmpty else {
            return unavailable("logs.json 裡沒有可用的請求記錄", now: now)
        }

        let timeZone = TimeZone(identifier: config.dailyResetTimeZone) ?? TimeZone(identifier: "America/Los_Angeles")!
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone

        let dayStart = calendar.startOfDay(for: now)
        let dayCount = timestamps.filter { $0 >= dayStart && $0 <= now }.count
        let minuteCount = timestamps.filter { $0 >= now.addingTimeInterval(-60) && $0 <= now }.count

        let nextDay = calendar.date(byAdding: .day, value: 1, to: dayStart)

        let windows: [QuotaWindow] = [
            QuotaWindow(
                label: "今日請求",
                usedPercent: Self.percent(used: dayCount, limit: config.dailyRequestLimit),
                detail: "\(dayCount) / \(config.dailyRequestLimit) 次",
                resetsAt: nextDay
            ),
            QuotaWindow(
                label: "每分鐘",
                usedPercent: Self.percent(used: minuteCount, limit: config.minuteRequestLimit),
                detail: "\(minuteCount) / \(config.minuteRequestLimit) 次",
                resetsAt: nil
            )
        ]

        return ProviderSnapshot(
            id: id,
            displayName: displayName,
            shortName: shortName,
            available: true,
            windows: windows,
            note: "來源：Gemini CLI logs.json（換日時區 \(timeZone.identifier)）",
            updatedAt: now
        )
    }

    static func percent(used: Int, limit: Int) -> Double? {
        guard limit > 0 else { return nil }
        return Double(used) / Double(limit) * 100
    }

    static func userMessageDates(in url: URL) -> [Date] {
        guard let data = try? Data(contentsOf: url, options: [.mappedIfSafe]),
              let array = JSONUtil.parseValue(data) as? [Any] else {
            return []
        }

        var dates: [Date] = []
        for element in array {
            guard let entry = element as? [String: Any] else { continue }
            // 只算使用者送出的請求；工具回覆與模型回覆不佔請求額度。
            let type = JSONUtil.string(entry["type"]) ?? "user"
            guard type == "user" else { continue }
            guard let date = ISODate.parse(JSONUtil.string(entry["timestamp"])) else { continue }
            dates.append(date)
        }
        return dates
    }
}
