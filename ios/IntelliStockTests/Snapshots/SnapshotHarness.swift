import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

// MARK: - Snapshot harness
//
// Renders real screens over the in-process stub (`DataStub` +
// `SnapshotFixtures`) in a UIWindow and writes PNGs, so a restyle can be
// compared side by side with the web app. It never touches the network: every
// request goes to the stub's private host.
//
// It runs only when `IS_SNAPSHOT_DIR` is set, so a normal test run skips it.
// `xcodebuild` passes a variable to the test process when it is prefixed
// with `TEST_RUNNER_`:
//
//     TEST_RUNNER_IS_SNAPSHOT_DIR=/path/to/out \
//     TEST_RUNNER_IS_SNAPSHOT_ONLY=dashboard,stock \   # optional: name prefixes
//     xcodebuild test -project IntelliStock.xcodeproj -scheme IntelliStock \
//       -destination 'platform=iOS Simulator,id=<your simulator>' \
//       -derivedDataPath /private/tmp/claude-501/dd-<you> \
//       -only-testing:IntelliStockTests/SnapshotScreens
//
// Each screen writes `<name>.png` (one phone screen, 3x), or
// `<name>-1.png`, `<name>-2.png`, … when it asks for `pages:` (the harness
// scrolls the screen's main scroll view a page at a time).
//
// To add a screen, add a `@Test` to `SnapshotScreens` (SnapshotScreens.swift)
// that calls `Snapshot.capture("name") { services in <the view> }`; give
// it any endpoints it needs in `SnapshotFixtures.respond`. Host a tab screen
// through `SnapshotHost.shell(_:tab:stack:)` to get the real tab bar and
// navigation bar; host a sheet's content with `SnapshotHost.sheet { … }`.

/// Where the PNGs go, and which screens to render.
nonisolated enum SnapshotConfig {
    static var directory: URL? {
        guard let path = ProcessInfo.processInfo.environment["IS_SNAPSHOT_DIR"], !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    static var isEnabled: Bool { directory != nil }

    /// `IS_SNAPSHOT_ONLY=dashboard,stock` renders only names with those prefixes.
    static func wants(_ name: String) -> Bool {
        guard let only = ProcessInfo.processInfo.environment["IS_SNAPSHOT_ONLY"], !only.isEmpty else { return true }
        return only.split(separator: ",").contains { name.hasPrefix($0.trimmingCharacters(in: .whitespaces)) }
    }
}

@MainActor
enum Snapshot {
    /// A services graph whose every request answers from `SnapshotFixtures`.
    /// Keep the stub alive as long as the services (it unregisters on deinit).
    static func makeServices() -> (services: AppServices, stub: DataStub) {
        let stub = DataStub()
        stub.handler = { SnapshotFixtures.respond($0) }
        let services = AppServices(apiClient: stub.client)
        return (services, stub)
    }

    /// Hosts the view built by `content` full screen (over `environment`'s
    /// services when given, else fresh ones), lets it load for
    /// `settle`, runs `prepare` (type into a field, tap a button), waits
    /// `afterPrepare`, and writes `<name>.png` — or `pages` PNGs, scrolling
    /// the main scroll view between them.
    static func capture<V: View>(
        _ name: String,
        environment: (services: AppServices, stub: DataStub)? = nil,
        pages: Int = 1,
        configuresBars: Bool = true,
        settle: Duration = .seconds(3),
        afterPrepare: Duration = .seconds(1.5),
        prepare: ((UIWindow) async -> Void)? = nil,
        @ViewBuilder content: (AppServices) -> V
    ) async throws {
        guard let directory = SnapshotConfig.directory, SnapshotConfig.wants(name) else { return }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if configuresBars { DSSystemBars.configure() }

        // Pass `environment` (from `makeServices()`) to load data before
        // building the view, e.g. a position for a stock route.
        let (services, stub) = environment ?? makeServices()
        defer { withExtendedLifetime(stub) {} }

        let scene = try #require(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
        let window = UIWindow(windowScene: scene)
        window.frame = scene.screen.bounds
        window.overrideUserInterfaceStyle = .dark
        let root = content(services)
            .environment(services)
            .dsAppearance()
        window.rootViewController = UIHostingController(rootView: root)
        window.makeKeyAndVisible()
        defer {
            window.isHidden = true
            window.rootViewController = nil
        }

        try await Task.sleep(for: settle)
        if let prepare {
            await prepare(window)
            try await Task.sleep(for: afterPrepare)
        }

        if pages <= 1 {
            try write(render(window), to: directory.appending(path: "\(name).png"))
            return
        }
        let scroll = mainScrollView(in: window)
        for page in 0..<pages {
            if page > 0 {
                guard let scroll, scrollOnePage(scroll) else { break }
                try await Task.sleep(for: .seconds(1.2))
            }
            try write(render(window), to: directory.appending(path: "\(name)-\(page + 1).png"))
        }
    }

    // MARK: Helpers for `prepare`

    /// The first view of type `T` in `view`'s subtree, depth first.
    static func find<T: UIView>(_ type: T.Type, in view: UIView) -> T? {
        if let match = view as? T { return match }
        for sub in view.subviews {
            if let match = find(type, in: sub) { return match }
        }
        return nil
    }

    /// Types `text` into the first text field (a search field included), as
    /// a person would, so SwiftUI's bindings see the change.
    static func type(_ text: String, in window: UIWindow) {
        guard let field = find(UITextField.self, in: window) else { return }
        field.becomeFirstResponder()
        field.insertText(text)
    }

    /// Dismisses the keyboard (the snapshot never includes it, but it
    /// shrinks the safe area).
    static func endEditing(_ window: UIWindow) {
        window.endEditing(true)
    }

    // MARK: Rendering

    private static func render(_ window: UIWindow) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = window.screen.scale
        format.opaque = true
        return UIGraphicsImageRenderer(bounds: window.bounds, format: format).image { _ in
            window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
        }
    }

    private static func write(_ image: UIImage, to url: URL) throws {
        let data = try #require(image.pngData())
        try data.write(to: url)
    }

    /// The tallest scrollable scroll view on screen (the screen's list).
    private static func mainScrollView(in view: UIView) -> UIScrollView? {
        var best: UIScrollView?
        func visit(_ v: UIView) {
            if let s = v as? UIScrollView, !v.isHidden, s.contentSize.height > s.bounds.height + 1,
               s.bounds.width >= view.bounds.width * 0.9,
               best.map({ s.contentSize.height > $0.contentSize.height }) ?? true {
                best = s
            }
            v.subviews.forEach(visit)
        }
        visit(view)
        return best
    }

    /// Scrolls down by one visible page, keeping 48 pt of overlap. False at
    /// the end.
    private static func scrollOnePage(_ scroll: UIScrollView) -> Bool {
        let insets = scroll.adjustedContentInset
        let maxY = scroll.contentSize.height - scroll.bounds.height + insets.bottom
        let current = scroll.contentOffset.y
        guard current < maxY - 1 else { return false }
        let visible = scroll.bounds.height - insets.top - insets.bottom
        let next = min(current + visible - 48, maxY)
        scroll.setContentOffset(CGPoint(x: 0, y: next), animated: false)
        return true
    }
}

/// Ways to host a screen the way the app shows it.
@MainActor
enum SnapshotHost {
    /// The signed-in shell (`MainTabView`): the real tab bar, navigation bar
    /// and chat accessory, on `tab`, with `stack` pushed on that tab.
    static func shell(_ services: AppServices, tab: AppTab, stack: [Route] = []) -> some View {
        services.router.tab = tab
        services.router.setStack(stack, for: tab)
        return MainTabView()
    }

    /// `content` presented as a sheet over a blank page, as the app presents
    /// it (grabber, detents and corner radius included).
    static func sheet<Content: View>(detents: Set<PresentationDetent> = [.large], @ViewBuilder content: @escaping () -> Content) -> some View {
        DS.Surface.page
            .ignoresSafeArea()
            .sheet(isPresented: .constant(true)) {
                content()
                    .presentationDetents(detents)
                    .dsAppearance()
            }
    }
}
