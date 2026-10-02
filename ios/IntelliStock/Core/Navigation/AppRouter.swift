import Observation

/// The selected tab plus one navigation stack per tab — the native form of
/// go_router's `StatefulShellRoute` and the routes pushed over it.
///
/// - `push` mirrors `context.push`: append to the current tab.
/// - `go` mirrors `context.go`: a tab root selects that tab and pops it to
///   root; any other location replaces the current tab's stack.
/// - `open` is for chat-tool navigation and in-app links: tab roots select
///   the tab, routes push, unknown paths are ignored.
@Observable
final class AppRouter {
    var tab: AppTab = .dashboard
    private(set) var stacks: [AppTab: [Route]] = [:]

    func stack(for tab: AppTab) -> [Route] {
        stacks[tab] ?? []
    }

    func setStack(_ routes: [Route], for tab: AppTab) {
        stacks[tab] = routes
    }

    func push(_ route: Route) {
        stacks[tab, default: []].append(route)
    }

    func go(_ path: String) {
        if let root = AppTab(rootPath: path) {
            tab = root
            stacks[root] = []
        } else if let route = Route(path: path) {
            stacks[tab] = [route]
        }
    }

    func open(_ path: String) {
        if let root = AppTab(rootPath: path) {
            tab = root
        } else if let route = Route(path: path) {
            push(route)
        }
    }

    func pop() {
        guard var routes = stacks[tab], !routes.isEmpty else { return }
        routes.removeLast()
        stacks[tab] = routes
    }

    func popToRoot(_ tab: AppTab? = nil) {
        stacks[tab ?? self.tab] = []
    }

    /// Back to a fresh signed-in shell (sign-out, server change).
    func reset() {
        tab = .dashboard
        stacks = [:]
    }
}
