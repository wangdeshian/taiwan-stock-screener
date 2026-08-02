import Foundation

// 這些型別都自己寫 `init(from:)` 而不是靠 Codable 合成：
// 設定檔是人手改的，只填一兩個欄位是常態，缺鍵時必須回落到預設值而不是整份解析失敗。

public struct CodexConfig: Codable, Sendable, Equatable {
    public var enabled = true
    /// 預設 ~/.codex/sessions
    public var sessionsDir: String?
    /// 最多回頭找幾個 session 檔才放棄
    public var maxFilesToScan = 8

    public init() {}

    enum CodingKeys: String, CodingKey {
        case enabled, sessionsDir, maxFilesToScan
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let defaults = CodexConfig()
        enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled) ?? defaults.enabled
        sessionsDir = try container.decodeIfPresent(String.self, forKey: .sessionsDir)
        maxFilesToScan = try container.decodeIfPresent(Int.self, forKey: .maxFilesToScan) ?? defaults.maxFilesToScan
    }
}

public struct ClaudeConfig: Codable, Sendable, Equatable {
    public var enabled = true
    /// 預設 ~/.claude/projects
    public var projectsDir: String?
    /// 滾動區間長度，Claude Code 的用量區間是 5 小時
    public var sessionWindowHours: Double = 5
    /// token 預算。沒填就不顯示百分比，只顯示實際 token 數（不編造官方額度）。
    public var sessionTokenBudget: Int?
    public var weeklyTokenBudget: Int?

    public init() {}

    enum CodingKeys: String, CodingKey {
        case enabled, projectsDir, sessionWindowHours, sessionTokenBudget, weeklyTokenBudget
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let defaults = ClaudeConfig()
        enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled) ?? defaults.enabled
        projectsDir = try container.decodeIfPresent(String.self, forKey: .projectsDir)
        sessionWindowHours = try container.decodeIfPresent(Double.self, forKey: .sessionWindowHours)
            ?? defaults.sessionWindowHours
        sessionTokenBudget = try container.decodeIfPresent(Int.self, forKey: .sessionTokenBudget)
        weeklyTokenBudget = try container.decodeIfPresent(Int.self, forKey: .weeklyTokenBudget)
    }
}

public struct GeminiConfig: Codable, Sendable, Equatable {
    public var enabled = true
    /// 預設 ~/.gemini/tmp
    public var logsRoot: String?
    public var dailyRequestLimit = 1_000
    public var minuteRequestLimit = 60
    /// Google 的每日額度以太平洋時間換日
    public var dailyResetTimeZone = "America/Los_Angeles"

    public init() {}

    enum CodingKeys: String, CodingKey {
        case enabled, logsRoot, dailyRequestLimit, minuteRequestLimit, dailyResetTimeZone
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let defaults = GeminiConfig()
        enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled) ?? defaults.enabled
        logsRoot = try container.decodeIfPresent(String.self, forKey: .logsRoot)
        dailyRequestLimit = try container.decodeIfPresent(Int.self, forKey: .dailyRequestLimit)
            ?? defaults.dailyRequestLimit
        minuteRequestLimit = try container.decodeIfPresent(Int.self, forKey: .minuteRequestLimit)
            ?? defaults.minuteRequestLimit
        dailyResetTimeZone = try container.decodeIfPresent(String.self, forKey: .dailyResetTimeZone)
            ?? defaults.dailyResetTimeZone
    }
}

public struct AppConfig: Codable, Sendable, Equatable {
    public var refreshSeconds: Double = 5
    /// 抓不到資料的供應商是否從選單列隱藏
    public var hideUnavailable = true
    public var codex = CodexConfig()
    public var claude = ClaudeConfig()
    public var gemini = GeminiConfig()

    public init() {}

    enum CodingKeys: String, CodingKey {
        case refreshSeconds, hideUnavailable, codex, claude, gemini
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let defaults = AppConfig()
        refreshSeconds = try container.decodeIfPresent(Double.self, forKey: .refreshSeconds)
            ?? defaults.refreshSeconds
        hideUnavailable = try container.decodeIfPresent(Bool.self, forKey: .hideUnavailable)
            ?? defaults.hideUnavailable
        codex = try container.decodeIfPresent(CodexConfig.self, forKey: .codex) ?? defaults.codex
        claude = try container.decodeIfPresent(ClaudeConfig.self, forKey: .claude) ?? defaults.claude
        gemini = try container.decodeIfPresent(GeminiConfig.self, forKey: .gemini) ?? defaults.gemini
    }

    public static var defaultPath: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/quotabar/config.json")
    }

    /// 讀設定檔；不存在或壞掉都回預設值（附帶錯誤訊息給呼叫端顯示）。
    public static func load(from url: URL = AppConfig.defaultPath) -> (config: AppConfig, error: String?) {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return (AppConfig(), nil)
        }
        do {
            let data = try Data(contentsOf: url)
            return (try JSONDecoder().decode(AppConfig.self, from: data), nil)
        } catch {
            return (AppConfig(), "設定檔讀取失敗，改用預設值：\(error.localizedDescription)")
        }
    }

    public func write(to url: URL = AppConfig.defaultPath) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try encoder.encode(self).write(to: url, options: .atomic)
    }
}

public enum Paths {
    public static var home: URL {
        FileManager.default.homeDirectoryForCurrentUser
    }

    /// 把設定檔裡的路徑字串展開成 URL，支援 `~` 開頭。
    public static func expand(_ path: String?, default fallback: String) -> URL {
        let raw = (path?.isEmpty == false) ? path! : fallback
        if raw.hasPrefix("~") {
            let stripped = String(raw.dropFirst()).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            return home.appendingPathComponent(stripped)
        }
        return URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
    }
}
