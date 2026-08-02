// swift-tools-version: 6.0
import PackageDescription

// QuotaCore 是純 Foundation，Linux / macOS 都能編譯與測試。
// QuotaBar 是 SwiftUI 選單列 App，只在 macOS 上加入建置目標。
var products: [Product] = [
    .library(name: "QuotaCore", targets: ["QuotaCore"])
]

var targets: [Target] = [
    .target(
        name: "QuotaCore",
        swiftSettings: [.swiftLanguageMode(.v5)]
    ),
    .testTarget(
        name: "QuotaCoreTests",
        dependencies: ["QuotaCore"],
        swiftSettings: [.swiftLanguageMode(.v5)]
    )
]

#if os(macOS)
products.append(.executable(name: "quotabar", targets: ["QuotaBar"]))
targets.append(
    .executableTarget(
        name: "QuotaBar",
        dependencies: ["QuotaCore"],
        swiftSettings: [.swiftLanguageMode(.v5)]
    )
)
#endif

let package = Package(
    name: "QuotaBar",
    platforms: [.macOS(.v14)],
    products: products,
    targets: targets
)
