import XCTest
@testable import QuotaCore

final class FormatTests: XCTestCase {

    func testTokenFormatting() {
        XCTAssertEqual(Format.tokens(0), "0")
        XCTAssertEqual(Format.tokens(999), "999")
        XCTAssertEqual(Format.tokens(1_000), "1.0K")
        XCTAssertEqual(Format.tokens(10_801), "10.8K")
        XCTAssertEqual(Format.tokens(2_500_000), "2.5M")
    }

    func testWindowLabels() {
        XCTAssertEqual(Format.windowLabel(minutes: 1), "1 分鐘")
        XCTAssertEqual(Format.windowLabel(minutes: 300), "5 小時")
        XCTAssertEqual(Format.windowLabel(minutes: 1_440), "每日")
        XCTAssertEqual(Format.windowLabel(minutes: 10_080), "每週")
        XCTAssertEqual(Format.windowLabel(minutes: 2_880), "2 天")
    }

    func testCountdown() {
        let now = Date()
        XCTAssertEqual(Format.countdown(to: now.addingTimeInterval(-5), from: now), "即將重置")
        XCTAssertEqual(Format.countdown(to: now.addingTimeInterval(30), from: now), "30 秒後重置")
        XCTAssertEqual(Format.countdown(to: now.addingTimeInterval(600), from: now), "10 分後重置")
        XCTAssertEqual(Format.countdown(to: now.addingTimeInterval(7_200), from: now), "2 小時後重置")
        XCTAssertEqual(Format.countdown(to: now.addingTimeInterval(9_000), from: now), "2 小時 30 分後重置")
    }

    func testQuotaWindowClampsPercent() {
        XCTAssertEqual(QuotaWindow(label: "x", usedPercent: 140).usedPercent, 100)
        XCTAssertEqual(QuotaWindow(label: "x", usedPercent: -10).usedPercent, 0)
        XCTAssertNil(QuotaWindow(label: "x").usedPercent)
    }

    func testPrimaryWindowPrefersOneWithPercent() {
        let snapshot = ProviderSnapshot(
            id: "x",
            displayName: "X",
            shortName: "X",
            available: true,
            windows: [
                QuotaWindow(label: "無百分比", detail: "123 tokens"),
                QuotaWindow(label: "有百分比", usedPercent: 40)
            ]
        )
        XCTAssertEqual(snapshot.primaryWindow?.label, "有百分比")
        XCTAssertEqual(snapshot.remainingPercent ?? 0, 60, accuracy: 0.001)
    }
}

final class ConfigTests: XCTestCase {

    func testMissingConfigFileFallsBackToDefaults() {
        let missing = URL(fileURLWithPath: "/tmp/quotabar-no-config-\(UUID().uuidString).json")
        let loaded = AppConfig.load(from: missing)

        XCTAssertNil(loaded.error)
        XCTAssertEqual(loaded.config, AppConfig())
    }

    func testPartialConfigKeepsDefaultsForOmittedKeys() throws {
        let directory = try TestSupport.makeTempDirectory(self)
        let path = directory.appendingPathComponent("config.json")
        try TestSupport.write(#"{"refreshSeconds": 15}"#, to: path)

        let loaded = AppConfig.load(from: path)

        XCTAssertNil(loaded.error)
        XCTAssertEqual(loaded.config.refreshSeconds, 15)
        XCTAssertTrue(loaded.config.codex.enabled)
        XCTAssertEqual(loaded.config.gemini.dailyRequestLimit, 1_000)
    }

    func testBrokenConfigFallsBackWithMessage() throws {
        let directory = try TestSupport.makeTempDirectory(self)
        let path = directory.appendingPathComponent("config.json")
        try TestSupport.write("{ not json", to: path)

        let loaded = AppConfig.load(from: path)

        XCTAssertNotNil(loaded.error)
        XCTAssertEqual(loaded.config, AppConfig())
    }

    func testRoundTrip() throws {
        let directory = try TestSupport.makeTempDirectory(self)
        let path = directory.appendingPathComponent("config.json")

        var config = AppConfig()
        config.refreshSeconds = 20
        config.claude.sessionTokenBudget = 5_000_000
        try config.write(to: path)

        XCTAssertEqual(AppConfig.load(from: path).config, config)
    }

    func testTildeExpansion() {
        let expanded = Paths.expand("~/foo/bar", default: "~/other")
        XCTAssertEqual(expanded.path, Paths.home.appendingPathComponent("foo/bar").path)

        let fallback = Paths.expand(nil, default: "~/other")
        XCTAssertEqual(fallback.path, Paths.home.appendingPathComponent("other").path)
    }
}
