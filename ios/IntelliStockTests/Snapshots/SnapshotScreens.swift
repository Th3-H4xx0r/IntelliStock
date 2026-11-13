import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// The screens the snapshot harness renders (see SnapshotHarness.swift for
/// how to run it). Skipped unless `IS_SNAPSHOT_DIR` is set. Serialized: one
/// window on screen at a time.
@Suite(.serialized, .enabled(if: SnapshotConfig.isEnabled))
@MainActor
struct SnapshotScreens {
    // MARK: Dashboard

    @Test func dashboard() async throws {
        try await Snapshot.capture("dashboard", pages: 10, settle: .seconds(5)) { services in
            SnapshotHost.shell(services, tab: .dashboard)
        }
    }

    // MARK: Stock

    @Test func stock() async throws {
        let env = Snapshot.makeServices()
        let holdings = try await env.services.dashboardRepository.accountHoldings(SnapshotFixtures.alpacaLiveId)
        let aapl = holdings.positions.first { $0.symbol == "AAPL" }
        let total = (holdings.cash ?? 0) + holdings.positions.reduce(0) { $0 + $1.marketValue }
        let route = StockRoute(symbol: "AAPL", position: aapl, brokerageId: SnapshotFixtures.alpacaLiveId, portfolioTotal: total)
        try await Snapshot.capture("stock", environment: env, pages: 4, settle: .seconds(5)) { services in
            SnapshotHost.shell(services, tab: .dashboard, stack: [.stock(route)])
        }
    }

    // MARK: Trade ticket and review

    /// The trade ticket as the stock screen presents it, on Sell.
    @Test func tradeTicket() async throws {
        let env = Snapshot.makeServices()
        let accounts = TradeTicket.tradableAccounts(try await env.services.dashboardRepository.brokerages())
        let model = Self.ticket(env.services, side: .sell, accounts: accounts)
        try await Snapshot.capture("trade-ticket", environment: env) { _ in
            SnapshotHost.sheet { TradeTicketSheet(model: model) { _ in } }
        }
    }

    /// The order review for a 4.25 sh limit sell, before the swipe.
    @Test func tradeReview() async throws {
        let env = Snapshot.makeServices()
        let accounts = TradeTicket.tradableAccounts(try await env.services.dashboardRepository.brokerages())
        let model = Self.ticket(env.services, side: .sell, accounts: accounts)
        model.draft.amountText = "4.25"
        model.draft.orderType = .limit
        model.draft.limitText = "188.15"
        await model.loadHeld()
        await model.loadInstances()
        let item = try #require(model.reviewItem)
        try await Snapshot.capture("trade-review", environment: env) { _ in
            SnapshotHost.sheet {
                NavigationStack {
                    TradeReviewView(model: model, item: item, onSubmitted: { _ in }, onClose: {})
                }
            }
        }
    }

    private static func ticket(_ services: AppServices, side: TradeSide, accounts: [BrokerageAccount]) -> TradeTicketModel {
        TradeTicketModel(
            symbol: "AAPL",
            side: side,
            accounts: accounts,
            accountId: SnapshotFixtures.alpacaLiveId,
            lastPrice: { 188.15 },
            backend: { [unowned services] in TradeRepository(client: services.apiClient) },
            onOrdersChanged: { _ in }
        )
    }

    // MARK: Search

    @Test func searchEmpty() async throws {
        try await Snapshot.capture("search-empty", settle: .seconds(2)) { window in
            Snapshot.endEditing(window)
        } content: { services in
            SnapshotHost.shell(services, tab: .dashboard, stack: [.search])
        }
    }

    @Test func searchResults() async throws {
        try await Snapshot.capture("search-results", settle: .seconds(2), afterPrepare: .seconds(3)) { window in
            Snapshot.type("AA", in: window)
            try? await Task.sleep(for: .seconds(2))
            Snapshot.endEditing(window)
        } content: { services in
            SnapshotHost.shell(services, tab: .dashboard, stack: [.search])
        }
    }

    // MARK: Screens the foundation did not restyle (what the design system alone carries)

    @Test func instances() async throws {
        try await Snapshot.capture("instances", pages: 2, settle: .seconds(4)) { services in
            SnapshotHost.shell(services, tab: .instances)
        }
    }

    @Test func settings() async throws {
        try await Snapshot.capture("settings", pages: 2) { services in
            SnapshotHost.shell(services, tab: .more, stack: [.settings])
        }
    }

    @Test func more() async throws {
        try await Snapshot.capture("more") { services in
            SnapshotHost.shell(services, tab: .more)
        }
    }
}
