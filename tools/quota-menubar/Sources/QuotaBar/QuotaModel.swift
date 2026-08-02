import Foundation
import QuotaCore
import SwiftUI

@MainActor
final class QuotaModel: ObservableObject {
    @Published private(set) var snapshots: [ProviderSnapshot] = []
    @Published private(set) var lastUpdated: Date?
    @Published private(set) var isRefreshing = false

    let config: AppConfig
    let configError: String?

    private let service: QuotaService
    private var loop: Task<Void, Never>?

    init() {
        let loaded = AppConfig.load()
        self.config = loaded.config
        self.configError = loaded.error
        self.service = QuotaService(config: loaded.config)
        start()
    }

    deinit {
        loop?.cancel()
    }

    private func start() {
        let interval = max(1.0, config.refreshSeconds)
        loop = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    func refresh() async {
        isRefreshing = true
        let result = await service.snapshots()
        snapshots = result
        lastUpdated = Date()
        isRefreshing = false
    }

    /// 選單列上要顯示的供應商。抓不到資料的預設隱藏（設定檔可關掉這個行為）。
    var visibleSnapshots: [ProviderSnapshot] {
        config.hideUnavailable ? snapshots.filter(\.available) : snapshots
    }

    /// 選單列文字。刻意只用純文字：MenuBarExtra 的 label 對複雜 view 的支援不穩定。
    var menuBarText: String {
        let parts = visibleSnapshots.compactMap { snapshot -> String? in
            guard let remaining = snapshot.remainingPercent else { return nil }
            return "\(snapshot.shortName) \(Int(remaining.rounded()))%"
        }
        return parts.isEmpty ? "Quota" : parts.joined(separator: "  ")
    }

    /// 面板頂端圓環：取所有供應商中最吃緊的一個當作整體安全等級。
    var safetyLevel: Double? {
        snapshots.compactMap(\.remainingPercent).min()
    }
}

enum QuotaColor {
    /// 剩餘越少越紅。門檻刻意抓寬一點，20% 以下才轉紅，避免一整天都在示警。
    static func forRemaining(_ remaining: Double?) -> Color {
        guard let remaining else { return .secondary }
        switch remaining {
        case ..<20: return .red
        case ..<50: return .orange
        default: return .green
        }
    }
}
