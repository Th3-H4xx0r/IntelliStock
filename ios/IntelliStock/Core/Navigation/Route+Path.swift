import Foundation

nonisolated extension Route {
    /// The go_router location of this route — the inverse of `init(path:)`.
    /// Path parameters are encoded with Dart's `Uri.encodeComponent`.
    var path: String {
        let enc = Route.encodeComponent
        switch self {
        case .backtests: return "/backtests"
        case .backtest(let id): return "/backtests/\(enc(id))"
        case .backtestPlayback(let id): return "/backtests/\(enc(id))/playback"
        case .kalshiInstance(let id): return "/kalshi/instances/\(enc(id))"
        case .kalshiBacktest(let id): return "/kalshi/instances/\(enc(id))/backtest"
        case .kalshiBacktestResult(let id): return "/kalshi/backtests/\(enc(id))"
        case .instance(let id): return "/instances/\(enc(id))"
        case .liveTrading(let id): return "/instances/\(enc(id))/live"
        case .strategy(let id): return "/strategies/\(enc(id))"
        case .brokerages: return "/brokerages"
        case .crypto: return "/crypto"
        case .cryptoInstance(let id): return "/crypto/instances/\(enc(id))"
        case .search: return "/search"
        case .stock(let route): return "/stock/\(enc(route.symbol))"
        case .agentRuns: return "/agent-runs"
        case .nexus: return "/nexus"
        case .learning: return "/learning"
        case .models: return "/models"
        case .tokenUsage: return "/token-usage"
        case .settings: return "/settings"
        case .notificationSettings: return "/settings/notifications"
        case .connect: return "/connect"
        case .onboarding: return "/onboarding"
        }
    }

    /// Dart `Uri.encodeComponent`: letters, digits and `-_.!~*'()` pass
    /// through; everything else is percent-encoded UTF-8.
    static func encodeComponent(_ s: String) -> String {
        var out = ""
        for byte in s.utf8 {
            switch byte {
            case UInt8(ascii: "a")...UInt8(ascii: "z"), UInt8(ascii: "A")...UInt8(ascii: "Z"),
                 UInt8(ascii: "0")...UInt8(ascii: "9"),
                 UInt8(ascii: "-"), UInt8(ascii: "_"), UInt8(ascii: "."), UInt8(ascii: "!"),
                 UInt8(ascii: "~"), UInt8(ascii: "*"), UInt8(ascii: "'"), UInt8(ascii: "("), UInt8(ascii: ")"):
                out.unicodeScalars.append(Unicode.Scalar(byte))
            default:
                out += String(format: "%%%02X", byte)
            }
        }
        return out
    }
}

extension AppRouter {
    /// The go_router location the person is looking at: the top of the current
    /// tab's stack, else the tab root. The More tab's root had no location in
    /// Flutter (it was a sheet over the last branch), so it reads as nil.
    var location: String? {
        if let top = stack(for: tab).last { return top.path }
        return tab.rootPath
    }
}

/// Which top-level screen the app shows — the `redirect` in `router.dart`.
nonisolated enum AppGate: Hashable, Sendable {
    case connect
    case login
    case onboarding
    case main

    /// The gates, in `router.dart`'s order.
    static func resolve(isConfigured: Bool, isAuthenticated: Bool, hasCompletedOnboarding: Bool) -> AppGate {
        if !isConfigured { return .connect }
        if !isAuthenticated { return .login }
        if !hasCompletedOnboarding { return .onboarding }
        return .main
    }

    /// Honours `?redirect=` only when it is a safe relative in-app path: it
    /// starts with `/`, not `//`, and has no `:` or `@`.
    static func safeRedirect(_ path: String?) -> String? {
        guard let path,
              path.hasPrefix("/"),
              !path.hasPrefix("//"),
              !path.contains(":"),
              !path.contains("@")
        else { return nil }
        return path
    }
}
