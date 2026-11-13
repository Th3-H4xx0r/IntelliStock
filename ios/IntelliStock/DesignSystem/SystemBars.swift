import UIKit

/// The UIKit appearance of the system bars and controls SwiftUI draws with
/// UIKit, in the web palette. Called once at launch (`AppDelegate`) and by the
/// snapshot harness, before any bar is created.
///
/// - Navigation bar: the page colour (transparent over the page at the
///   scroll edge), no shadow line, Inter titles (inline 17/500, large
///   30/500) in zinc-100, bar buttons in the off-white. iOS 26 still draws
///   bar buttons on Liquid Glass capsules; that is left alone.
/// - Tab bar: flat `DS.Surface.bar` with a 6% top hairline; selected items
///   off-white, the rest zinc-500. On iOS 26 the system draws the tab bar as
///   a floating Liquid Glass capsule and ignores this appearance (only the
///   selected tint applies); it is left alone, as the brief asks.
/// - A leftover segmented `Picker`: a surface track, the raised segment,
///   Inter 12/500 labels. New code uses `DSSegmentedControl`.
nonisolated enum DSSystemBars {
    @MainActor
    static func configure() {
        let page = UIColor(hex: 0x0F0F10)
        let primary = UIColor(hex: 0xF4F4F5)
        let accent = UIColor(hex: 0xEDEDED)
        let muted = UIColor(hex: 0x71717A)

        // Navigation bar. At the scroll edge it is transparent over the page:
        // on iOS 26 a scroll-edge background hides the large title. Scrolled,
        // it takes the page colour with no shadow line.
        let nav = UINavigationBarAppearance()
        nav.configureWithDefaultBackground()
        nav.backgroundColor = page
        nav.shadowColor = .clear
        nav.titleTextAttributes = [
            .font: DS.Font.uiFont(17, .medium, textStyle: .headline),
            .foregroundColor: primary,
        ]
        nav.largeTitleTextAttributes = [
            .font: DS.Font.uiFont(30, .medium, textStyle: .largeTitle),
            .foregroundColor: primary,
        ]
        let plain = UIBarButtonItemAppearance(style: .plain)
        plain.normal.titleTextAttributes = [.font: DS.Font.uiFont(17, textStyle: .body), .foregroundColor: accent]
        let done = UIBarButtonItemAppearance(style: .done)
        done.normal.titleTextAttributes = [.font: DS.Font.uiFont(17, .medium, textStyle: .body), .foregroundColor: accent]
        nav.buttonAppearance = plain
        nav.backButtonAppearance = plain
        nav.doneButtonAppearance = done
        let edge = UINavigationBarAppearance()
        edge.configureWithTransparentBackground()
        edge.titleTextAttributes = nav.titleTextAttributes
        edge.largeTitleTextAttributes = nav.largeTitleTextAttributes
        edge.buttonAppearance = plain
        edge.backButtonAppearance = plain
        edge.doneButtonAppearance = done
        let navProxy = UINavigationBar.appearance()
        navProxy.standardAppearance = nav
        navProxy.compactAppearance = nav
        navProxy.scrollEdgeAppearance = edge
        navProxy.compactScrollEdgeAppearance = edge
        navProxy.tintColor = accent

        // Tab bar
        let tab = UITabBarAppearance()
        tab.configureWithOpaqueBackground()
        tab.backgroundColor = UIColor(hex: 0x0B0B0C)
        tab.shadowColor = UIColor.white.withAlphaComponent(0.06)
        let itemFont = DS.Font.uiFont(10, .medium, textStyle: .caption2)
        for item in [tab.stackedLayoutAppearance, tab.inlineLayoutAppearance, tab.compactInlineLayoutAppearance] {
            item.normal.iconColor = muted
            item.normal.titleTextAttributes = [.font: itemFont, .foregroundColor: muted]
            item.selected.iconColor = accent
            item.selected.titleTextAttributes = [.font: itemFont, .foregroundColor: accent]
        }
        let tabProxy = UITabBar.appearance()
        tabProxy.standardAppearance = tab
        tabProxy.scrollEdgeAppearance = tab
        tabProxy.tintColor = accent
        tabProxy.unselectedItemTintColor = muted

        // Segmented control (a leftover `Picker(.segmented)`)
        let seg = UISegmentedControl.appearance()
        seg.backgroundColor = UIColor(hex: 0x161617)
        seg.selectedSegmentTintColor = UIColor(hex: 0x1E1E20)
        seg.setTitleTextAttributes([.font: DS.Font.uiFont(12, .medium, textStyle: .caption1), .foregroundColor: muted], for: .normal)
        seg.setTitleTextAttributes([.font: DS.Font.uiFont(12, .medium, textStyle: .caption1), .foregroundColor: primary], for: .selected)

        // Alerts, menus and other system chrome follow the window tint.
        UIView.appearance(whenContainedInInstancesOf: [UIAlertController.self]).tintColor = accent
    }
}
