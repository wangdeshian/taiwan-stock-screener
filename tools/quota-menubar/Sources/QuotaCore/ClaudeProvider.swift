import Foundation

/// 由 Claude Code 的本機對話記錄統計 token 用量。
///
/// 重要前提：Claude Code **沒有**把官方額度百分比寫在本機檔案裡，`/usage` 的數字來自伺服器。
/// 所以這裡算的是「本機記錄的 token 用量」，不是官方剩餘額度。沒有在設定檔填 token 預算時，
/// 一律只顯示 token 數量，不換算百分比 —— 寧可少顯示，也不要編一個看起來像官方額度的數字。
///
/// 記錄位置：`~/.claude/projects/<專案>/<session>.jsonl`，assistant 行帶 `message.usage`。
public final class ClaudeProvider: QuotaProvider {
    public let id = "claude"
    public let displayName = "Claude"
    public let shortName = "CL"

    struct UsageEntry {
        var date: Date
        var key: String
        var input: Int
        var output: Int
        var cacheCreation: Int
        var cacheRead: Int

        var total: Int { input + output + cacheCreation + cacheRead }
    }

    private struct CachedFile {
        var size: Int
        var modified: Date
        var entries: [UsageEntry]
    }

    private let config: ClaudeConfig
    /// 以 (檔案大小, mtime) 當快取鍵：Claude 的 jsonl 只會往後追加，沒變動就不重解析。
    private var cache: [String: CachedFile] = [:]

    public init(config: ClaudeConfig = ClaudeConfig()) {
        self.config = config
    }

    public var projectsDirectory: URL {
        Paths.expand(config.projectsDir, default: "~/.claude/projects")
    }

    public func snapshot(now: Date) -> ProviderSnapshot {
        let directory = projectsDirectory
        guard FileManager.default.fileExists(atPath: directory.path) else {
            return unavailable("找不到 Claude 記錄：\(directory.path)", now: now)
        }

        // 只看最近 8 天改過的檔案：週窗需要 7 天，多留一天緩衝。
        let cutoff = now.addingTimeInterval(-8 * 24 * 3_600)
        let files = FileScan.files(in: directory, pathExtension: "jsonl", modifiedAfter: cutoff)
        guard !files.isEmpty else {
            return unavailable("最近 8 天沒有 Claude 對話記錄", now: now)
        }

        let entries = loadEntries(from: files)
        guard !entries.isEmpty else {
            return unavailable("讀到記錄檔但沒有 usage 欄位，請用 --probe 檢查格式", now: now)
        }

        let sessionWindow = max(0.5, config.sessionWindowHours)
        let sessionStart = now.addingTimeInterval(-sessionWindow * 3_600)
        let weekStart = now.addingTimeInterval(-7 * 24 * 3_600)

        let sessionEntries = entries.filter { $0.date >= sessionStart }
        let weekEntries = entries.filter { $0.date >= weekStart }

        let sessionTokens = sessionEntries.reduce(0) { $0 + $1.total }
        let weekTokens = weekEntries.reduce(0) { $0 + $1.total }

        // 滾動窗的重置點 = 最舊那筆滑出窗口的時間。
        let sessionResets = sessionEntries.map(\.date).min()?.addingTimeInterval(sessionWindow * 3_600)

        let windows: [QuotaWindow] = [
            QuotaWindow(
                label: Format.windowLabel(minutes: Int(sessionWindow * 60)),
                usedPercent: Self.percent(used: sessionTokens, budget: config.sessionTokenBudget),
                detail: "\(Format.tokens(sessionTokens)) tokens · \(sessionEntries.count) 則回應",
                resetsAt: sessionResets
            ),
            QuotaWindow(
                label: "最近 7 天",
                usedPercent: Self.percent(used: weekTokens, budget: config.weeklyTokenBudget),
                detail: "\(Format.tokens(weekTokens)) tokens · \(weekEntries.count) 則回應",
                resetsAt: nil
            )
        ]

        let note = config.sessionTokenBudget == nil
            ? "本機 token 統計（非官方額度）；在設定檔填 sessionTokenBudget 才會顯示百分比"
            : "本機 token 統計（非官方額度），百分比以自訂預算換算"

        return ProviderSnapshot(
            id: id,
            displayName: displayName,
            shortName: shortName,
            available: true,
            windows: windows,
            note: note,
            updatedAt: now
        )
    }

    static func percent(used: Int, budget: Int?) -> Double? {
        guard let budget, budget > 0 else { return nil }
        return Double(used) / Double(budget) * 100
    }

    /// 讀取（並快取）所有檔案的 usage 記錄，跨檔去重。
    func loadEntries(from files: [ScannedFile]) -> [UsageEntry] {
        var seen = Set<String>()
        var all: [UsageEntry] = []
        var nextCache: [String: CachedFile] = [:]

        for file in files {
            let path = file.url.path
            let entries: [UsageEntry]

            if let cached = cache[path], cached.size == file.size, cached.modified == file.modified {
                entries = cached.entries
            } else {
                entries = Self.parseEntries(FileScan.lines(of: file.url))
            }

            nextCache[path] = CachedFile(size: file.size, modified: file.modified, entries: entries)

            for entry in entries where seen.insert(entry.key).inserted {
                all.append(entry)
            }
        }

        cache = nextCache
        return all
    }

    static func parseEntries(_ lines: [String]) -> [UsageEntry] {
        var entries: [UsageEntry] = []
        entries.reserveCapacity(lines.count / 4)

        for line in lines {
            // 先做便宜的字串檢查，避免對每一行都做 JSON 解析。
            guard line.contains("\"usage\"") else { continue }
            guard let object = JSONUtil.parseObject(line) else { continue }
            guard let message = object["message"] as? [String: Any],
                  let usage = message["usage"] as? [String: Any] else { continue }

            let input = JSONUtil.int(JSONUtil.value(usage, "input_tokens", "inputTokens")) ?? 0
            let output = JSONUtil.int(JSONUtil.value(usage, "output_tokens", "outputTokens")) ?? 0
            let cacheCreation = JSONUtil.int(
                JSONUtil.value(usage, "cache_creation_input_tokens", "cacheCreationInputTokens")
            ) ?? 0
            let cacheRead = JSONUtil.int(
                JSONUtil.value(usage, "cache_read_input_tokens", "cacheReadInputTokens")
            ) ?? 0

            if input == 0 && output == 0 && cacheCreation == 0 && cacheRead == 0 { continue }

            guard let date = ISODate.parse(JSONUtil.string(object["timestamp"])) else { continue }

            // 同一則回應可能因為 resume / 複製專案而出現在多個檔案，用 message id + requestId 去重。
            let messageID = JSONUtil.string(message["id"]) ?? ""
            let requestID = JSONUtil.string(JSONUtil.value(object, "requestId", "request_id")) ?? ""
            let key = messageID.isEmpty && requestID.isEmpty
                ? "\(date.timeIntervalSince1970)-\(input)-\(output)-\(cacheCreation)-\(cacheRead)"
                : "\(messageID)|\(requestID)"

            entries.append(
                UsageEntry(
                    date: date,
                    key: key,
                    input: input,
                    output: output,
                    cacheCreation: cacheCreation,
                    cacheRead: cacheRead
                )
            )
        }

        return entries
    }
}
