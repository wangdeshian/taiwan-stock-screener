import XCTest
@testable import QuotaCore

final class CodexProviderTests: XCTestCase {

    private func provider(sessionsDir: URL) -> CodexProvider {
        var config = CodexConfig()
        config.sessionsDir = sessionsDir.path
        return CodexProvider(config: config)
    }

    func testReadsRateLimitsFromNestedPayload() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let session = root.appendingPathComponent("2026/08/01/rollout-1.jsonl")
        try TestSupport.write(
            """
            {"type":"session_meta","payload":{"id":"abc"}}
            {"type":"event_msg","payload":{"type":"token_count","rate_limits":{"primary":{"used_percent":9.0,"window_minutes":300,"resets_in_seconds":3600},"secondary":{"used_percent":42.5,"window_minutes":10080,"resets_in_seconds":86400}}}}
            {"type":"response_item","payload":{"type":"message"}}
            """,
            to: session
        )

        let now = Date()
        let snapshot = provider(sessionsDir: root).snapshot(now: now)

        XCTAssertTrue(snapshot.available)
        XCTAssertEqual(snapshot.windows.count, 2)
        XCTAssertEqual(snapshot.windows[0].label, "5 小時")
        XCTAssertEqual(snapshot.windows[0].usedPercent ?? 0, 9.0, accuracy: 0.001)
        XCTAssertEqual(snapshot.windows[0].remainingPercent ?? 0, 91.0, accuracy: 0.001)
        XCTAssertEqual(snapshot.windows[1].label, "每週")
        XCTAssertEqual(snapshot.windows[1].usedPercent ?? 0, 42.5, accuracy: 0.001)

        let reset = try XCTUnwrap(snapshot.windows[0].resetsAt)
        XCTAssertEqual(reset.timeIntervalSince(now), 3600, accuracy: 2)
    }

    func testTopLevelRateLimitsAndCamelCaseKeys() throws {
        let root = try TestSupport.makeTempDirectory(self)
        try TestSupport.write(
            #"{"rateLimits":{"primary":{"usedPercent":75,"windowMinutes":60}}}"#,
            to: root.appendingPathComponent("rollout-2.jsonl")
        )

        let snapshot = provider(sessionsDir: root).snapshot(now: Date())

        XCTAssertTrue(snapshot.available)
        XCTAssertEqual(snapshot.windows.first?.label, "60 分鐘")
        XCTAssertEqual(snapshot.windows.first?.remainingPercent ?? 0, 25, accuracy: 0.001)
    }

    func testUsesMostRecentEventInMostRecentFile() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let older = root.appendingPathComponent("rollout-old.jsonl")
        let newer = root.appendingPathComponent("rollout-new.jsonl")

        try TestSupport.write(
            #"{"rate_limits":{"primary":{"used_percent":10,"window_minutes":300}}}"#,
            to: older
        )
        try TestSupport.write(
            """
            {"rate_limits":{"primary":{"used_percent":20,"window_minutes":300}}}
            {"rate_limits":{"primary":{"used_percent":31,"window_minutes":300}}}
            """,
            to: newer
        )

        // 明確設定 mtime，不依賴寫入順序。
        try FileManager.default.setAttributes(
            [.modificationDate: Date().addingTimeInterval(-3600)],
            ofItemAtPath: older.path
        )
        try FileManager.default.setAttributes(
            [.modificationDate: Date()],
            ofItemAtPath: newer.path
        )

        let snapshot = provider(sessionsDir: root).snapshot(now: Date())
        XCTAssertEqual(snapshot.windows.first?.usedPercent ?? 0, 31, accuracy: 0.001)
    }

    func testMissingDirectoryIsUnavailableNotZero() {
        let missing = URL(fileURLWithPath: "/tmp/quotabar-does-not-exist-\(UUID().uuidString)")
        let snapshot = provider(sessionsDir: missing).snapshot(now: Date())

        XCTAssertFalse(snapshot.available)
        XCTAssertTrue(snapshot.windows.isEmpty)
        XCTAssertNil(snapshot.remainingPercent)
    }

    func testSessionWithoutRateLimitsIsUnavailable() throws {
        let root = try TestSupport.makeTempDirectory(self)
        try TestSupport.write(
            #"{"type":"event_msg","payload":{"type":"agent_message"}}"#,
            to: root.appendingPathComponent("rollout-3.jsonl")
        )

        let snapshot = provider(sessionsDir: root).snapshot(now: Date())
        XCTAssertFalse(snapshot.available)
    }
}
