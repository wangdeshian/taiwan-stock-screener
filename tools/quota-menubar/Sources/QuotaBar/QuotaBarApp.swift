import SwiftUI

struct QuotaBarApp: App {
    @StateObject private var model = QuotaModel()

    var body: some Scene {
        MenuBarExtra {
            PanelView(model: model)
        } label: {
            Text(model.menuBarText)
        }
        .menuBarExtraStyle(.window)
    }
}
