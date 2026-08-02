import Foundation

/// 把三家供應商包成一個 actor：Provider 內部有解析快取（可變狀態），
/// 用 actor 隔離就不必讓每個 Provider 自己處理併發。
public actor QuotaService {
    private let config: AppConfig
    private var providers: [QuotaProvider]

    public init(config: AppConfig = AppConfig()) {
        self.config = config

        var list: [QuotaProvider] = []
        if config.codex.enabled { list.append(CodexProvider(config: config.codex)) }
        if config.claude.enabled { list.append(ClaudeProvider(config: config.claude)) }
        if config.gemini.enabled { list.append(GeminiProvider(config: config.gemini)) }
        self.providers = list
    }

    public func snapshots(now: Date = Date()) -> [ProviderSnapshot] {
        providers.map { $0.snapshot(now: now) }
    }
}

/// 同步版本，給 `--probe` / `--json` 這種一次性的 CLI 用。
public enum QuotaSnapshotting {
    public static func collect(config: AppConfig, now: Date = Date()) -> [ProviderSnapshot] {
        var results: [ProviderSnapshot] = []
        if config.codex.enabled { results.append(CodexProvider(config: config.codex).snapshot(now: now)) }
        if config.claude.enabled { results.append(ClaudeProvider(config: config.claude).snapshot(now: now)) }
        if config.gemini.enabled { results.append(GeminiProvider(config: config.gemini).snapshot(now: now)) }
        return results
    }
}
