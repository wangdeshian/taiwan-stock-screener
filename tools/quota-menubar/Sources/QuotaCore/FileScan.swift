import Foundation

public struct ScannedFile: Sendable, Equatable {
    public var url: URL
    public var size: Int
    public var modified: Date
}

public enum FileScan {

    /// 列出目錄下符合副檔名的檔案，依修改時間由新到舊排序。
    /// - Parameter modifiedAfter: 只保留這個時間之後改過的檔案（用來跳過幾個月前的舊 session）。
    public static func files(
        in directory: URL,
        pathExtension: String? = nil,
        fileName: String? = nil,
        modifiedAfter: Date? = nil
    ) -> [ScannedFile] {
        let manager = FileManager.default
        var isDirectory: ObjCBool = false
        guard manager.fileExists(atPath: directory.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            return []
        }

        let keys: [URLResourceKey] = [.isRegularFileKey, .fileSizeKey, .contentModificationDateKey]
        guard let enumerator = manager.enumerator(
            at: directory,
            includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else {
            return []
        }

        var results: [ScannedFile] = []
        for case let url as URL in enumerator {
            if let pathExtension, url.pathExtension != pathExtension { continue }
            if let fileName, url.lastPathComponent != fileName { continue }

            guard let values = try? url.resourceValues(forKeys: Set(keys)),
                  values.isRegularFile == true else { continue }

            let modified = values.contentModificationDate ?? .distantPast
            if let modifiedAfter, modified < modifiedAfter { continue }

            results.append(ScannedFile(url: url, size: values.fileSize ?? 0, modified: modified))
        }

        return results.sorted { $0.modified > $1.modified }
    }

    /// 逐行讀 JSONL。檔案可能是幾 MB，但一次讀進來仍比逐行 syscall 快得多。
    public static func lines(of url: URL) -> [String] {
        guard let data = try? Data(contentsOf: url, options: [.mappedIfSafe]),
              let text = String(data: data, encoding: .utf8) else {
            return []
        }
        return text.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
    }
}
