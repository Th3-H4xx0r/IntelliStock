import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/symbol_search/symbol_search_models_test.dart
/// (minus `searchUnavailableMessage`, a screen helper),
/// test/features/settings/notification_prefs_model_test.dart,
/// test/core/models/option_symbol_test.dart and
/// test/features/live_trading/live_state_options_test.dart.
struct SymbolSearchModelsTests {
    @Test func matchesTickerAndCompanyNameWithoutLosingMetadata() {
        let result = SearchInstrument(json: ["symbol": "BTC-USD", "name": "Bitcoin USD", "type": "Crypto"])
        #expect(result.matches("btc"))
        #expect(result.matches("bitcoin"))
        #expect(!result.matches("ethereum"))
        #expect(result.symbol == "BTC-USD")
        #expect(result.type == "Crypto")
    }

    @Test func buildsAUniqueSymbolBatchForResultSparklines() {
        let results = [
            SearchInstrument(symbol: "AAPL", name: "Apple", type: "Stock"),
            SearchInstrument(symbol: "SPY", name: "SPDR S&P 500 ETF Trust", type: "ETF"),
            SearchInstrument(symbol: "AAPL", name: "Apple Inc.", type: "Stock"),
        ]
        #expect(searchSymbolsForSparklines(results) == ["AAPL", "SPY"])
    }

    @Test func turnsADayOfPricesIntoTheLatestPriceAndTodayChange() {
        let quote = searchQuoteFromHistory([594.25, 600.19, 601.12])
        #expect(quote?.price == 601.12)
        #expect(close(quote?.changePct, 1.16, 0.01))
        #expect(searchQuoteFromHistory([]) == nil)
        #expect(searchQuoteFromHistory([5]) == SearchQuote(price: 5))
    }

    @Test func searchSendsQAndDropsEmptySymbols() async throws {
        let stub = DataStub(json: #"{"results": [{"symbol": "AAPL", "name": "Apple"}, {"symbol": ""}, 3]}"#)
        let rows = try await SymbolSearchRepository(client: stub.client).search("aap")
        #expect(stub.last?.path == "/symbols/search")
        #expect(stub.last?.queryItems == ["q": "aap"])
        #expect(rows.map(\.symbol) == ["AAPL"])
    }
}

struct NotificationPrefsModelTests {
    @Test func parsesTheCategoriesMatrix() {
        let p = NotificationPrefs(json: [
            "categories": [
                "order_fill": ["discord": false, "push": true],
                "halt": ["discord": true, "push": false],
            ],
        ])
        #expect(p.routeFor("order_fill").discord == false)
        #expect(p.routeFor("order_fill").push == true)
        #expect(p.routeFor("halt").discord == true)
    }

    @Test func routeForDefaultsToDiscordOnlyWhenAbsent() {
        let p = NotificationPrefs(json: ["categories": [:]])
        #expect(p.routeFor("order_fill").discord == true)
        #expect(p.routeFor("order_fill").push == false)
    }

    @Test func roundTripsThroughToJSON() {
        let p = NotificationPrefs(categories: ["order_fill": CategoryRoute(discord: false, push: true)])
        let back = NotificationPrefs(json: p.toJSON())
        #expect(back.routeFor("order_fill").push == true)
        #expect(back.routeFor("order_fill").discord == false)
        #expect(p.toJSON() == ["categories": ["order_fill": ["discord": false, "push": true]]])
    }

    @Test func withRouteReturnsAnImmutableCopy() {
        let p = NotificationPrefs(categories: ["order_fill": CategoryRoute(discord: true, push: false)])
        let next = p.withRoute("order_fill", CategoryRoute(discord: true, push: true))
        #expect(p.routeFor("order_fill").push == false)
        #expect(next.routeFor("order_fill").push == true)
    }

    @Test func groupsInOrderAndTypesInGroup() {
        let p = NotificationPrefs(json: [
            "categories": [:],
            "types": [
                ["key": "a", "group": "Orders"],
                ["key": "b"],
                ["key": "c", "group": "Orders", "label": "C"],
            ],
        ])
        #expect(p.groupsInOrder == ["Orders", "Other"])
        #expect(p.typesInGroup("Orders").map(\.key) == ["a", "c"])
        #expect(p.types[0].label == "a")
    }

    @Test func thereAre19FallbackCategoriesWithStableKeys() {
        #expect(kNotificationCategories.count == 19)
        let keys = Set(kNotificationCategories.map(\.key))
        for key in ["order_submit", "order_fill", "order_reject", "order_retry", "strategy_start",
                    "strategy_error", "halt", "drawdown_halt", "crash_loop", "instance_crash"] {
            #expect(keys.contains(key))
        }
    }

    @Test func theFallbackListsTheSwingAndWheelTypesInBackendOrder() throws {
        let keys = kNotificationCategories.map(\.key)
        #expect(Array(keys.suffix(9)) == [
            "swing_entry",
            "swing_pending_review",
            "swing_exit",
            "swing_run_summary",
            "wheel_put_placed",
            "wheel_pending_review",
            "wheel_position_alert",
            "wheel_assignment",
            "swing_approval_failed",
        ])
        let failed = try #require(kNotificationCategories.first { $0.key == "swing_approval_failed" })
        #expect(failed.label == "Approved order refused or unconfirmed")
        #expect(failed.description
            == "A swing or wheel order you approved was not sent, may not have been placed, or WAS placed though its signal reads failed")
        let exit = try #require(kNotificationCategories.first { $0.key == "swing_exit" })
        #expect(exit.description
            == "The swing lane sold a position; or an exit was not placed or its outcome is unknown, so the position may be unprotected")
        #expect(Set(keys).count == keys.count)
    }

    @Test func repositoryWireShape() async throws {
        let stub = DataStub(json: #"{"categories": {}}"#)
        let repo = NotificationPrefsRepository(client: stub.client)
        _ = try await repo.save(NotificationPrefs(categories: ["halt": CategoryRoute(discord: false, push: true)]))
        #expect(stub.last?.method == "PUT")
        #expect(stub.last?.path == "/notification-preferences")
        #expect(stub.last?.jsonBody == ["categories": ["halt": ["discord": false, "push": true]]])
        _ = try await repo.sendTest(.push)
        #expect(stub.last?.path == "/notifications/test")
        #expect(stub.last?.jsonBody == ["channel": "push"])
    }
}

struct OptionSymbolTests {
    @Test func parseOccSymbolReadsRootExpiryTypeAndStrike() throws {
        let put = try #require(parseOccSymbol("APH261002P00130000"))
        #expect(put.underlying == "APH")
        #expect(put.expiry == "2026-10-02")
        #expect(put.optionType == "put")
        #expect(put.strike == 130.0)
        let call = try #require(parseOccSymbol("spy261218c00612500"))
        #expect(call.underlying == "SPY")
        #expect(call.optionType == "call")
        #expect(call.strike == 612.5)
    }

    @Test(arguments: ["AAPL", "BRK.B", "", nil, "APH261002X00130000", "TOOLONGROOT261002P00130000", "1PH261002P00130000"] as [String?])
    func stockTickersAndJunkAreNotOptions(symbol: String?) {
        #expect(!isOccOptionSymbol(symbol))
    }

    @Test func describeOptionContractPrefersExplicitFieldsFallsBackToTheSymbol() {
        #expect(describeOptionContract(symbol: "APH261002P00130000") == "APH $130 Put · 2026-10-02")
        #expect(describeOptionContract(symbol: "APH261002P00130000", underlying: "APH", strike: 127.5)
            == "APH $127.50 Put · 2026-10-02")
        #expect(describeOptionContract(symbol: "AAPL") == "")
    }
}

struct LiveStateOptionsTests {
    /// A live-state position after plan A-live: Alpaca had no quote for the put.
    private let shortPut: JSON = [
        "symbol": "APH261002P00130000",
        "qty": -1,
        "avg_entry_price": 1.23,
        "last_price": nil,
        "market_value": nil,
        "unrealized_pnl": nil,
        "unrealized_pnl_pct": nil,
        "asset_class": "us_option",
        "side": "short",
        "multiplier": 100,
        "underlying": "APH",
        "strike": 130,
        "expiry": "2026-10-02",
    ]

    @Test func aShortPutWithNoQuoteKeepsItsNils() {
        let p = Position(json: shortPut)
        #expect(p.isOption)
        #expect(p.isShort)
        #expect(p.contractMultiplier == 100)
        #expect(p.quantityLabel == "CONTRACTS")
        #expect(p.quantityText == "1")
        #expect(!p.canClose)
        #expect(p.lastPrice == nil)
        #expect(p.marketValue == nil)
        #expect(p.unrealizedPnl == nil)
        #expect(p.optionDescription == "APH $130 Put · 2026-10-02")
    }

    @Test func anEquityRowFromTodaysApiParsesAsBefore() {
        let p = Position(json: [
            "symbol": "AAPL",
            "qty": 12.5,
            "avg_entry_price": 190.0,
            "last_price": 200.0,
            "market_value": 2500.0,
            "unrealized_pnl": 125.0,
            "unrealized_pnl_pct": 5.26,
        ])
        #expect(!p.isOption)
        #expect(!p.isShort)
        #expect(p.contractMultiplier == 1)
        #expect(p.quantityLabel == "SHARES")
        #expect(p.quantityText == "12.5000")
        #expect(p.canClose)
        #expect(p.marketValue == 2500.0)
        #expect(p.optionDescription == "")
    }

    @Test func anOccSymbolWithoutAssetClassIsStillAnOption() {
        let p = Position(json: ["symbol": "APH261002P00130000", "qty": -2])
        #expect(p.isOption)
        #expect(p.isShort)
        #expect(p.contractMultiplier == 100)
    }

    @Test func anOptionFillTotalsX100AndCountsContracts() {
        let t = Trade(json: ["symbol": "APH261002P00130000", "side": "sell", "qty": 1, "price": 1.25])
        #expect(t.isOption)
        #expect(t.quantityLabel == "CONTRACTS")
        #expect(t.quantityText == "1")
        #expect(t.total == 125.0)
    }

    @Test func aStockFillIsUnchanged() {
        let t = Trade(json: ["symbol": "AAPL", "side": "buy", "qty": 12, "price": 200.0])
        #expect(!t.isOption)
        #expect(t.quantityLabel == "SHARES")
        #expect(t.quantityText == "12.0000")
        #expect(t.total == 2400.0)
    }

    @Test func liveStateDefaultsAndLookback() {
        let s = LiveState(json: ["cash": 50, "lookback": ["current": 3, "total": 2], "positions": [["symbol": "A"], "junk"]])
        #expect(s.status == "unknown")
        #expect(s.buyingPower == 50)
        #expect(s.lookback?.pct == 100)
        #expect(s.positions.map(\.symbol) == ["A"])
        #expect(Lookback(json: [:]).pct == 0)
    }
}
