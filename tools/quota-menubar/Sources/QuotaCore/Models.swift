import Foundation

/// 單一額度區間（例如 Codex 的 5 小時窗、Gemini 的每日請求數）。
public struct QuotaWindow: Sendable, Equatable {
    /// 顯示名稱，例如「5 小時」「每週」「今日」。
    public var label: String
    /// 已使用百分比 0...100。無法換算成百分比時為 nil（例如沒設 token 預算）。
    public var usedPercent: Double?
    /// 副標，例如「1.2M tokens · 342 則回應」。
    public var detail: String?
    /// 這個區間何時重置。
    public var resetsAt: Date?

    public init(label: String, usedPercent: Double? = nil, detail: String? = nil, resetsAt: Date? = nil) {
        self.label = label
        self.usedPercent = usedPercent.map { min(max($0, 0), 100) }
        self.detail = detail
        self.resetsAt = resetsAt
    }

    public var remainingPercent: Double? {
        usedPercent.map { 100 - $0 }
    }
}

/// 一個供應商（Codex / Claude / Gemini）的即時快照。
public struct ProviderSnapshot: Sendable, Equatable {
    public var id: String
    public var displayName: String
    /// 短代號，畫在選單列上（"CX" / "CL" / "GM"）。
    public var shortName: String
    /// 找不到資料來源時為 false；此時一律顯示「—」，不猜數字。
    public var available: Bool
    public var windows: [QuotaWindow]
    /// 資料來源說明或錯誤訊息，顯示在面板底部。
    public var note: String?
    public var updatedAt: Date

    public init(
        id: String,
        displayName: String,
        shortName: String,
        available: Bool,
        windows: [QuotaWindow] = [],
        note: String? = nil,
        updatedAt: Date = Date()
    ) {
        self.id = id
        self.displayName = displayName
        self.shortName = shortName
        self.available = available
        self.windows = windows
        self.note = note
        self.updatedAt = updatedAt
    }

    /// 主要顯示用的區間：第一個算得出百分比的區間。
    public var primaryWindow: QuotaWindow? {
        windows.first { $0.usedPercent != nil } ?? windows.first
    }

    public var remainingPercent: Double? {
        primaryWindow?.remainingPercent
    }
}

public protocol QuotaProvider: AnyObject {
    var id: String { get }
    var displayName: String { get }
    var shortName: String { get }
    func snapshot(now: Date) -> ProviderSnapshot
}

public extension QuotaProvider {
    func snapshot() -> ProviderSnapshot { snapshot(now: Date()) }

    func unavailable(_ reason: String, now: Date) -> ProviderSnapshot {
        ProviderSnapshot(
            id: id,
            displayName: displayName,
            shortName: shortName,
            available: false,
            windows: [],
            note: reason,
            updatedAt: now
        )
    }
}

public enum Format {

    public static func tokens(_ count: Int) -> String {
        let value = Double(count)
        if value >= 1_000_000 {
            return String(format: "%.1fM", value / 1_000_000)
        }
        if value >= 1_000 {
            return String(format: "%.1fK", value / 1_000)
        }
        return "\(count)"
    }

    /// 把「距離重置還有多久」寫成人看得懂的字串。
    public static func countdown(to date: Date, from now: Date) -> String {
        let seconds = Int(date.timeIntervalSince(now).rounded())
        if seconds <= 0 { return "即將重置" }
        if seconds < 60 { return "\(seconds) 秒後重置" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes) 分後重置" }
        let hours = minutes / 60
        if hours < 24 {
            let remainder = minutes % 60
            return remainder == 0 ? "\(hours) 小時後重置" : "\(hours) 小時 \(remainder) 分後重置"
        }
        let days = hours / 24
        return "\(days) 天 \(hours % 24) 小時後重置"
    }

    /// 由分鐘數推回窗口名稱，避免各家用不同單位時標籤不一致。
    public static func windowLabel(minutes: Int) -> String {
        switch minutes {
        case ..<60: return "\(minutes) 分鐘"
        case 10_080: return "每週"
        case 1_440: return "每日"
        default:
            if minutes % 1_440 == 0 { return "\(minutes / 1_440) 天" }
            if minutes % 60 == 0 { return "\(minutes / 60) 小時" }
            return "\(minutes) 分鐘"
        }
    }
}
