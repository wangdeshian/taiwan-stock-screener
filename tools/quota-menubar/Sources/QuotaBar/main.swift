import AppKit
import QuotaCore

// 有 main.swift 時不能用 @main，改成手動呼叫 App.main()，
// 這樣才有機會在啟動 GUI 前攔截 CLI 參數。
let arguments = Set(CommandLine.arguments.dropFirst())

if arguments.contains("--help") || arguments.contains("-h") {
    print("""
    quotabar —— macOS 選單列顯示 Codex / Claude / Gemini 本機用量

    用法：
      quotabar            啟動選單列常駐程式
      quotabar --probe    印出資料來源診斷報告（格式對不上時先跑這個）
      quotabar --once     解析一次並印出結果後結束
      quotabar --config   印出目前設定檔路徑與內容
    """)
    exit(0)
}

let loaded = AppConfig.load()
if let error = loaded.error {
    FileHandle.standardError.write(Data((error + "\n").utf8))
}

if arguments.contains("--config") {
    print("設定檔路徑：\(AppConfig.defaultPath.path)")
    if let data = try? Data(contentsOf: AppConfig.defaultPath),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        print("（檔案不存在，使用內建預設值）")
    }
    exit(0)
}

if arguments.contains("--probe") {
    print(Probe.report(config: loaded.config))
    exit(0)
}

if arguments.contains("--once") {
    let now = Date()
    for snapshot in QuotaSnapshotting.collect(config: loaded.config, now: now) {
        guard snapshot.available else {
            print("\(snapshot.displayName)：— （\(snapshot.note ?? "無資料")）")
            continue
        }
        let summary = snapshot.windows.map { window -> String in
            let percent = window.remainingPercent.map { String(format: "剩 %.0f%%", $0) } ?? (window.detail ?? "-")
            return "\(window.label) \(percent)"
        }.joined(separator: " · ")
        print("\(snapshot.displayName)：\(summary)")
    }
    exit(0)
}

// 純選單列 App，不要 Dock 圖示與主選單。
NSApplication.shared.setActivationPolicy(.accessory)
QuotaBarApp.main()
