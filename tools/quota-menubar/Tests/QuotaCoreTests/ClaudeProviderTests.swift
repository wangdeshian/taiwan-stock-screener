import XCTest
@testable import QuotaCore

final class ClaudeProviderTests: XCTestCase {

    private func assistantLine(
        at date: Date,
        id: String,
        requestID: String,
        input: Int = 100,
        output: Int = 50,
        cacheCreation: Int = 0,
        cacheRead: Int = 0
    ) -> String {
        """
        {"type":"assistant","timestamp":"\(TestSupport.timestamp(date))","requestId":"\(requestID)",\
        "message":{"id":"\(id)","model":"test-model","usage":{"input_tokens":\(input),\
        "output_tokens":\(output),"cache_creation_input_tokens":\(cacheCreation),\
        "cache_read_input_tokens":\(cacheRead)}}}
        """
    }

    private func provider(projectsDir: URL, sessionBudget: Int? = nil) -> ClaudeProvider {
        var config = ClaudeConfig()
        config.projectsDir = projectsDir.path
        config.sessionTokenBudget = sessionBudget
        return ClaudeProvider(config: config)
    }

    func testSumsTokensInsideRollingWindow() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = Date()

        try TestSupport.write(
            [
                assistantLine(at: now.addingTimeInterval(-600), id: "m1", requestID: "r1", input: 1_000, output: 200),
                assistantLine(at: now.addingTimeInterval(-3_600), id: "m2", requestID: "r2", input: 500, output: 100),
                // 6 小時前：落在 5 小時窗之外，但仍在 7 天窗內
                assistantLine(at: now.addingTimeInterval(-6 * 3_600), id: "m3", requestID: "r3", input: 9_000, output: 1)
            ].joined(separator: "\n"),
            to: root.appendingPathComponent("proj-a/session1.jsonl")
        )

        let snapshot = provider(projectsDir: root).snapshot(now: now)

        XCTAssertTrue(snapshot.available)
        XCTAssertEqual(snapshot.windows.count, 2)
        XCTAssertEqual(snapshot.windows[0].label, "5 小時")
        XCTAssertEqual(snapshot.windows[0].detail, "1.8K tokens · 2 則回應")
        XCTAssertEqual(snapshot.windows[1].label, "最近 7 天")
        XCTAssertEqual(snapshot.windows[1].detail, "10.8K tokens · 3 則回應")
    }

    func testNoPercentageWithoutConfiguredBudget() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = Date()
        try TestSupport.write(
            assistantLine(at: now, id: "m1", requestID: "r1"),
            to: root.appendingPathComponent("proj/session.jsonl")
        )

        let snapshot = provider(projectsDir: root).snapshot(now: now)

        XCTAssertTrue(snapshot.available)
        XCTAssertNil(snapshot.windows[0].usedPercent)
        XCTAssertNil(snapshot.remainingPercent)
    }

    func testPercentageUsesConfiguredBudget() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = Date()
        try TestSupport.write(
            assistantLine(at: now, id: "m1", requestID: "r1", input: 2_000, output: 500),
            to: root.appendingPathComponent("proj/session.jsonl")
        )

        let snapshot = provider(projectsDir: root, sessionBudget: 10_000).snapshot(now: now)

        XCTAssertEqual(snapshot.windows[0].usedPercent ?? 0, 25, accuracy: 0.001)
        XCTAssertEqual(snapshot.remainingPercent ?? 0, 75, accuracy: 0.001)
    }

    func testDeduplicatesSameResponseAcrossFiles() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = Date()
        let line = assistantLine(at: now, id: "m1", requestID: "r1", input: 1_000, output: 0)

        // resume 或複製專案目錄時，同一則回應會出現在兩個 jsonl 裡。
        try TestSupport.write(line, to: root.appendingPathComponent("proj-a/session.jsonl"))
        try TestSupport.write(line, to: root.appendingPathComponent("proj-b/session.jsonl"))

        let snapshot = provider(projectsDir: root).snapshot(now: now)
        XCTAssertEqual(snapshot.windows[0].detail, "1.0K tokens · 1 則回應")
    }

    func testCountsAllFourTokenKinds() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = Date()
        try TestSupport.write(
            assistantLine(at: now, id: "m1", requestID: "r1", input: 1, output: 2, cacheCreation: 3, cacheRead: 4),
            to: root.appendingPathComponent("proj/session.jsonl")
        )

        let snapshot = provider(projectsDir: root, sessionBudget: 10).snapshot(now: now)
        XCTAssertEqual(snapshot.windows[0].usedPercent ?? 0, 100, accuracy: 0.001)
    }

    func testIgnoresUserLinesAndMalformedJSON() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = Date()
        try TestSupport.write(
            [
                #"{"type":"user","message":{"role":"user","content":"hi"}}"#,
                "{ this is not json",
                assistantLine(at: now, id: "m1", requestID: "r1", input: 10, output: 0)
            ].joined(separator: "\n"),
            to: root.appendingPathComponent("proj/session.jsonl")
        )

        let snapshot = provider(projectsDir: root).snapshot(now: now)
        XCTAssertEqual(snapshot.windows[0].detail, "10 tokens · 1 則回應")
    }

    func testMissingDirectoryIsUnavailable() {
        let missing = URL(fileURLWithPath: "/tmp/quotabar-missing-\(UUID().uuidString)")
        let snapshot = provider(projectsDir: missing).snapshot(now: Date())

        XCTAssertFalse(snapshot.available)
        XCTAssertNil(snapshot.remainingPercent)
    }

    func testCacheReturnsSameResultOnRepeatedCalls() throws {
        let root = try TestSupport.makeTempDirectory(self)
        let now = Date()
        try TestSupport.write(
            assistantLine(at: now, id: "m1", requestID: "r1", input: 1_000, output: 0),
            to: root.appendingPathComponent("proj/session.jsonl")
        )

        let subject = provider(projectsDir: root)
        let first = subject.snapshot(now: now)
        let second = subject.snapshot(now: now)

        XCTAssertEqual(first.windows[0].detail, second.windows[0].detail)
    }
}
