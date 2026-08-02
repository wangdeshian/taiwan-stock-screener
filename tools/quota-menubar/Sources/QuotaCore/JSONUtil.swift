import Foundation

/// 這些工具刻意用 `JSONSerialization` + `Any` 而不是 Codable：
/// Codex / Claude / Gemini 的本機記錄格式會隨版本改，寫死 struct 一改就整個壞掉。
/// 這裡改成「在 JSON 樹裡找得到就用，找不到就回 nil」，讓格式漂移只造成缺值而不是崩潰。
public enum JSONUtil {

    public static func parseObject(_ line: String) -> [String: Any]? {
        guard let data = line.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    public static func parseValue(_ data: Data) -> Any? {
        try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
    }

    /// 在巢狀 JSON 中深度優先尋找第一個符合 key（或別名）的字典。
    /// Codex 的 `rate_limits` 有時在頂層、有時包在 `payload` 裡，靠這個吸收差異。
    public static func findObject(
        keys: [String],
        in value: Any?,
        maxDepth: Int = 12
    ) -> [String: Any]? {
        guard maxDepth > 0, let value else { return nil }

        if let dict = value as? [String: Any] {
            for key in keys {
                if let hit = dict[key] as? [String: Any] { return hit }
            }
            for nested in dict.values {
                if let hit = findObject(keys: keys, in: nested, maxDepth: maxDepth - 1) {
                    return hit
                }
            }
            return nil
        }

        if let array = value as? [Any] {
            for element in array {
                if let hit = findObject(keys: keys, in: element, maxDepth: maxDepth - 1) {
                    return hit
                }
            }
        }
        return nil
    }

    /// 取字典中第一個存在的 key（支援 snake_case / camelCase 兩種寫法）。
    public static func value(_ dict: [String: Any], _ keys: String...) -> Any? {
        for key in keys {
            if let found = dict[key], !(found is NSNull) { return found }
        }
        return nil
    }

    public static func double(_ any: Any?) -> Double? {
        switch any {
        case let number as NSNumber: return number.doubleValue
        case let text as String: return Double(text)
        default: return nil
        }
    }

    public static func int(_ any: Any?) -> Int? {
        switch any {
        case let number as NSNumber: return number.intValue
        case let text as String: return Int(text)
        default: return nil
        }
    }

    public static func string(_ any: Any?) -> String? {
        any as? String
    }
}

/// ISO8601 解析。`ISO8601DateFormatter` 沒有保證執行緒安全，所以用鎖包起來，
/// 而不是每次呼叫都重建 formatter（單輪掃描會解析上萬行）。
public enum ISODate {
    private static let lock = NSLock()

    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let plain: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    public static func parse(_ text: String?) -> Date? {
        guard let text, !text.isEmpty else { return nil }
        lock.lock()
        defer { lock.unlock() }
        return fractional.date(from: text) ?? plain.date(from: text)
    }
}
