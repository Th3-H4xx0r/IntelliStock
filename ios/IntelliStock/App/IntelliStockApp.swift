import SwiftUI

@main
struct IntelliStockApp: App {
    var body: some Scene {
        WindowGroup {
            ContentUnavailableView("IntelliStock", systemImage: "chart.line.uptrend.xyaxis")
        }
    }
}
