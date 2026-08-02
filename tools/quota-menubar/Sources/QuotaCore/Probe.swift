import Foundation

/// `quotabar --probe` 的實作。
///
/// 這個工具最大的風險是「各家記錄格式跟預期不一樣」，而那只有在你自己的機器上才看得到。
/// Probe 把找到的目錄、檔案數、最新檔名、以及實際解析結果印出來，格式對不上時可以直接看出是哪一層斷掉。
public enum Probe {

    public static func report(config: AppConfig, now: Date = Date()) -> String {
        var out: [String] = []
        out.append("QuotaBar 診斷報告  \(now)")
        out.append(String(repeating: "=", count: 56))

        out.append(contentsOf: section(
            title: "Codex",
            directory: CodexProvider(config: config.codex).sessionsDirectory,
            pathExtension: "jsonl"
        ))

        out.append(contentsOf: section(
            title: "Claude",
            directory: ClaudeProvider(config: config.claude).projectsDirectory,
            pathExtension: "jsonl"
        ))

        out.append(contentsOf: section(
            title: "Gemini",
            directory: GeminiProvider(config: config.gemini).logsRoot,
            fileName: "logs.json"
        ))

        out.append("")
        out.append("解析結果")
        out.append(String(repeating: "-", count: 56))
        for snapshot in QuotaSnapshotting.collect(config: config, now: now) {
            out.append("[\(snapshot.displayName)] \(snapshot.available ? "可用" : "不可用")")
            if let note = snapshot.note { out.append("  來源／備註：\(note)") }
            for window in snapshot.windows {
                let percent = window.usedPercent.map { String(format: "已用 %.1f%%", $0) } ?? "無百分比"
                let reset = window.resetsAt.map { " · " + Format.countdown(to: $0, from: now) } ?? ""
                out.append("  - \(window.label)：\(percent) · \(window.detail ?? "-")\(reset)")
            }
            out.append("")
        }

        return out.joined(separator: "\n")
    }

    private static func section(
        title: String,
        directory: URL,
        pathExtension: String? = nil,
        fileName: String? = nil
    ) -> [String] {
        var out: [String] = ["", title, String(repeating: "-", count: 56)]
        out.append("目錄：\(directory.path)")

        guard FileManager.default.fileExists(atPath: directory.path) else {
            out.append("狀態：目錄不存在")
            return out
        }

        let files = FileScan.files(in: directory, pathExtension: pathExtension, fileName: fileName)
        out.append("檔案數：\(files.count)")

        for file in files.prefix(3) {
            out.append("  \(file.url.lastPathComponent)  \(file.size) bytes  \(file.modified)")
        }

        if let newest = files.first {
            let lines = FileScan.lines(of: newest.url)
            out.append("最新檔行數：\(lines.count)")
            if let sample = lines.last {
                out.append("最後一行（前 400 字）：")
                out.append("  " + String(sample.prefix(400)))
            }
        }

        return out
    }
}
