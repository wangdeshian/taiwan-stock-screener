import AppKit
import Combine
import QuotaCore
import SwiftUI

struct PanelView: View {
    @ObservedObject var model: QuotaModel
    /// 讓倒數秒數會跳動，而不是只在重新解析時才更新。
    @State private var tick = Date()

    private let ticker = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header

            if model.snapshots.isEmpty {
                Text("讀取中…")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 24)
            } else {
                ForEach(model.snapshots, id: \.id) { snapshot in
                    ProviderCard(snapshot: snapshot, now: tick)
                }
            }

            if let error = model.configError {
                Text(error)
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }

            footer
        }
        .padding(14)
        .frame(width: 320)
        .background(.ultraThinMaterial)
        .onReceive(ticker) { tick = $0 }
    }

    private var header: some View {
        HStack(spacing: 10) {
            SafetyRing(remaining: model.safetyLevel)
                .frame(width: 34, height: 34)

            VStack(alignment: .leading, spacing: 2) {
                Text("用量額度")
                    .font(.headline)
                Text(model.lastUpdated.map { "更新於 \($0.formatted(date: .omitted, time: .standard))" } ?? "尚未更新")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Spacer()
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Text("資料只在本機讀取，不會上傳")
                .font(.caption2)
                .foregroundStyle(.tertiary)

            Spacer()

            Button {
                Task { await model.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .help("立即重新讀取")

            Button {
                NSApplication.shared.terminate(nil)
            } label: {
                Image(systemName: "power")
            }
            .buttonStyle(.borderless)
            .help("結束 QuotaBar")
        }
    }
}

struct ProviderCard: View {
    let snapshot: ProviderSnapshot
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(snapshot.displayName)
                    .font(.system(size: 14, weight: .semibold))

                Spacer()

                if let remaining = snapshot.remainingPercent {
                    Text("\(Int(remaining.rounded()))%")
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                        .foregroundStyle(QuotaColor.forRemaining(remaining))
                } else {
                    Text("—")
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                        .foregroundStyle(.secondary)
                }
            }

            if snapshot.available {
                ForEach(Array(snapshot.windows.enumerated()), id: \.offset) { _, window in
                    WindowRow(window: window, now: now)
                }
            }

            if let note = snapshot.note {
                Text(note)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.regularMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.white.opacity(0.12), lineWidth: 1)
        )
    }
}

struct WindowRow: View {
    let window: QuotaWindow
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(window.label)
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Spacer()

                if let resetsAt = window.resetsAt {
                    Text(Format.countdown(to: resetsAt, from: now))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }

            if let used = window.usedPercent {
                QuotaBarGauge(remaining: 100 - used)
            }

            if let detail = window.detail {
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

struct QuotaBarGauge: View {
    let remaining: Double

    var body: some View {
        GeometryReader { geometry in
            let fraction = min(max(remaining / 100, 0), 1)
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(.quaternary)
                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [
                                QuotaColor.forRemaining(remaining).opacity(0.75),
                                QuotaColor.forRemaining(remaining)
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(width: max(2, geometry.size.width * fraction))
            }
        }
        .frame(height: 6)
    }
}

/// 面板左上的安全等級圓環：所有供應商中最吃緊的剩餘百分比。
struct SafetyRing: View {
    let remaining: Double?

    var body: some View {
        ZStack {
            Circle()
                .stroke(.quaternary, lineWidth: 4)

            if let remaining {
                Circle()
                    .trim(from: 0, to: min(max(remaining / 100, 0), 1))
                    .stroke(
                        QuotaColor.forRemaining(remaining),
                        style: StrokeStyle(lineWidth: 4, lineCap: .round)
                    )
                    .rotationEffect(.degrees(-90))

                Text("\(Int(remaining.rounded()))")
                    .font(.system(size: 11, weight: .bold, design: .rounded))
            } else {
                Text("—")
                    .font(.system(size: 11, weight: .bold, design: .rounded))
                    .foregroundStyle(.secondary)
            }
        }
        .animation(.easeOut(duration: 0.25), value: remaining)
    }
}
