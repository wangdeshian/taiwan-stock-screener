import XCTest
@testable import QuotaCore

final class GeminiProviderTests: XCTestCase {

    private func provider(logsRoot: URL, timeZone: String = "UTC") -> GeminiProvider {
        var config = GeminiConfig()
        config.logsRoot = logsRoot.path
        config.dailyResetTimeZone = timeZone
        config.dailyRequestLimit = 100
        config.minuteRequestLimit = 10
        return GeminiProvider(config: config)
    }

    private func logs(_ entries: [(Date, String)]) -> String {
        let items = entries.map { date, type in
            #"{"sessionId":"s","messageId":0,"timestamp":"\#(TestSupport.timestamp(date))","type":"\#(type)","message":"hi"}"#
        }
        return "[\(items.joined(separator: ","))]"
    }

    func testCountsTodayAndLastMinuteRequests() throws {
        let root = try TestSupport.makeTempDirectory(self)
        // 固定在 UTC 當天中午，避免測試在午夜前後跑時跨日。
        let now = ISODate.parse("2026-08-01T12:00:00.000Z")!

        try TestSupport.write(
            logs([
                (now.addingTimeInterval(-10), "user"),
                (now.addingTimeInterval(-30), "user"),
                (now.addingTimeInterval(-4 * 3_600), "user"),
                (now.addingTimeInterval(-20), "gemini")
            ]),
            to: root.appendingPathComponent("hash1/logs.json")
        )

        let snapshot = provider(logsRoot: root).snapshot(now: now)

        XCTAssertTrue(snapshot.available)
        XCTAssertEqual(snapshot.windows[0].label, "今日請求")
        XCTAssertEqual(snapshot.windows[0].detail, "3 / 100 次")
        XCTAssertEqual(snapshot.windows[0].usedPercent ?? 0, 3, accuracy: 0.001)
        XCTAssertEqual(snapshot.windows[1].detail, "2 / 10 次")
    }

    func testAggregatesAcrossProjectDirectories() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = ISODate.parse("2026-08-01T12:00:00.000Z")!

        try TestSupport.write(logs([(now.addingTimeInterval(-100), "user")]),
                              to: root.appendingPathComponent("hash1/logs.json"))
        try TestSupport.write(logs([(now.addingTimeInterval(-200), "user")]),
                              to: root.appendingPathComponent("hash2/logs.json"))

        let snapshot = provider(logsRoot: root).snapshot(now: now)
        XCTAssertEqual(snapshot.windows[0].detail, "2 / 100 次")
    }

    func testYesterdayRequestsDoNotCountTowardToday() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = ISODate.parse("2026-08-01T01:00:00.000Z")!

        try TestSupport.write(
            logs([
                (now.addingTimeInterval(-2 * 3_600), "user"),  // 前一天 23:00 UTC
                (now.addingTimeInterval(-60), "user")
            ]),
            to: root.appendingPathComponent("hash1/logs.json")
        )

        let snapshot = provider(logsRoot: root).snapshot(now: now)
        XCTAssertEqual(snapshot.windows[0].detail, "1 / 100 次")
    }

    func testDailyResetTimeZoneShiftsTheBoundary() throws {
        let root = try TestSupport.makeTempDirectory(self)
        // UTC 01:00 = 太平洋時間前一天 18:00，所以在太平洋時區這兩筆算同一天。
        let now = ISODate.parse("2026-08-01T01:00:00.000Z")!

        try TestSupport.write(
            logs([
                (now.addingTimeInterval(-2 * 3_600), "user"),
                (now.addingTimeInterval(-60), "user")
            ]),
            to: root.appendingPathComponent("hash1/logs.json")
        )

        let snapshot = provider(logsRoot: root, timeZone: "America/Los_Angeles").snapshot(now: now)
        XCTAssertEqual(snapshot.windows[0].detail, "2 / 100 次")
    }

    func testMissingDirectoryIsUnavailable() {
        let missing = URL(fileURLWithPath: "/tmp/quotabar-missing-\(UUID().uuidString)")
        let snapshot = provider(logsRoot: missing).snapshot(now: Date())

        XCTAssertFalse(snapshot.available)
        XCTAssertNil(snapshot.remainingPercent)
    }

    func testEmptyLogsAreUnavailableRatherThanZeroPercent() throws {
        let root = try TestSupport.makeTempDirectory(self)
        try TestSupport.write("[]", to: root.appendingPathComponent("hash1/logs.json"))

        let snapshot = provider(logsRoot: root).snapshot(now: Date())
        XCTAssertFalse(snapshot.available)
    }
}
