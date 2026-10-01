import Foundation
import Testing
@testable import IntelliStock

/// The model-parsing groups of test/features/backtests/backtest_test.dart.
/// (`StatusPill.colorForStatus`, `_Pagination._buildPages` and the playback
/// speed group test presentation code; the backtests feature ports them.)
struct BacktestRowTests {
    @Test func parsesAllExpectedFields() {
        let row = BacktestRow(json: [
            "id": "abc123",
            "instance_id": "main",
            "status": "completed",
            "stocks": ["AAPL", "GOOG"],
            "start_date": "2025-01-01",
            "end_date": "2025-06-01",
            "completed_at": "2025-06-02T10:00:00Z",
            "pnl": 1234.56,
            "pnl_percent": 12.3,
            "time_elapsed_seconds": 300,
        ])
        #expect(row.id == "abc123")
        #expect(row.instanceId == "main")
        #expect(row.status == "completed")
        #expect(row.stocks == ["AAPL", "GOOG"])
        #expect(close(row.pnl?.double, 1234.56, 0.01))
        #expect(row.completedAt == "2025-06-02T10:00:00Z")
        // `num` keeps the int: "300", never "300.0".
        #expect(row.timeElapsedSeconds?.description == "300")
    }

    @Test func toleratesNullOrMissingFields() {
        let row = BacktestRow(json: ["id": "x"])
        #expect(row.id == "x")
        #expect(row.stocks.isEmpty)
        #expect(row.pnl == nil)
    }

    @Test func instanceFallbackUsesInstanceKeyWhenInstanceIdAbsent() {
        #expect(BacktestRow(json: ["id": "y", "instance": "legacy-inst"]).instanceId == "legacy-inst")
    }

    @Test func numericStringsParseAsDartNumTryParse() {
        let row = BacktestRow(json: ["id": "z", "pnl": "5", "pnl_percent": "5.0", "tickers": ["T"]])
        #expect(row.pnl == .int(5))
        #expect(row.pnl?.description == "5")
        #expect(row.pnlPercent?.description == "5.0")
        #expect(row.stocks == ["T"])
    }
}

struct BacktestSummaryTests {
    @Test func parsesStrategySchemaSubStrategies() {
        let summary = BacktestSummary(json: [
            "id": "1",
            "pnl": 100,
            "strategy_schema": [
                "name": "My Strat",
                "strategies": [
                    ["strategy": "NexusOnly", "weight": 1.0, "decision_phase": "pre"],
                ],
            ],
            "tickers": ["TSLA"],
        ])
        #expect(summary.strategySchema?.name == "My Strat")
        #expect(summary.strategySchema?.strategies.count == 1)
        #expect(summary.strategySchema?.strategies.first?.strategy == "NexusOnly")
    }

    @Test func handlesPauseFields() {
        let summary = BacktestSummary(json: [
            "id": "2",
            "status": "paused_llm_critical",
            "pause_reason_tag": "rate_limit",
            "pause_provider": "anthropic",
            "pause_model": "claude-sonnet-4",
            "pause_call_site": "strategy_eval",
            "pause_attempts": 3,
        ])
        #expect(summary.status == "paused_llm_critical")
        #expect(summary.pauseReasonTag == "rate_limit")
        #expect(summary.pauseProvider == "anthropic")
        #expect(summary.pauseAttempts == 3)
    }

    @Test func feesNumMapAndChangePercentMap() {
        let summary = BacktestSummary(json: [
            "fees": ["total_fees": 1.5, "taker_rate": "0.006", "emulated": true, "venue": "binanceus"],
            "stock_price_change": [
                "AAPL": ["start_price": 1, "end_price": 2, "change_percent": 100],
                "MSFT": -2.5,
                "BAD": ["start_price": 1],
            ],
            "pnl_per_stock": ["X": "nope"],
        ])
        #expect(summary.fees == ["total_fees": 1.5, "taker_rate": 0.006])
        #expect(summary.feeEmulated == true)
        #expect(summary.feeVenue == "binanceus")
        #expect(summary.stockPriceChange == ["AAPL": 100, "MSFT": -2.5])
        #expect(summary.pnlPerStock == nil)
        #expect(BacktestSummary(json: [:]).feeEmulated == nil)
    }
}

struct BacktestStatusTests {
    @Test func parsesNexusLookback() {
        let status = BacktestStatus(json: [
            "status": "running",
            "progress": 42,
            "nexus_lookback": [
                "current": 30,
                "total": 90,
                "current_date": "2025-02-01",
                "start_date": "2025-01-01",
                "end_date": "2025-03-31",
            ],
        ])
        #expect(status.progress == 42)
        #expect(status.nexusLookback?.current == 30)
        #expect(close(status.nexusLookback?.fraction, 1.0 / 3, 0.01))
    }
}

struct LlmCostTests {
    @Test func parsesBreakdownRows() {
        let cost = LlmCost(json: [
            "total_cost_usd": 0.25,
            "total_calls": 10,
            "ok_calls": 9,
            "failed_calls": 1,
            "total_input_tokens": 50000,
            "total_output_tokens": 5000,
            "total_reasoning_tokens": 1000,
            "by_model": [
                ["key": "claude-sonnet-4", "cost_usd": 0.20],
                ["key": "claude-haiku-3", "cost_usd": 0.05],
            ],
            "by_call_site": [],
            "by_provider": [["key": "anthropic", "cost_usd": 0.25]],
        ])
        #expect(cost.totalCostUsd == 0.25)
        #expect(cost.byModel.count == 2)
        #expect(cost.byModel[0].key == "claude-sonnet-4")
        #expect(cost.byProvider[0].costUsd == 0.25)
        #expect(LlmCostRow(json: [:]).key == "?")
    }
}

struct PlaybackDataTests {
    @Test func parsesEventsAndMetadata() {
        let data = PlaybackData(json: [
            "metadata": ["initial_cash": 100000],
            "events": [
                ["type": "date", "label": "Jan 02, 2025", "time": "09:30 AM"],
                "junk",
                [
                    "type": "portfolio",
                    "value": 101000,
                    "date": "2025-01-02",
                    "holdings": [["ticker": "AAPL", "qty": 10, "avg": 150.0, "curr": 155.0]],
                ],
            ],
        ])
        #expect(data.metadata.initialCash == 100000)
        #expect(data.events.count == 2)
        #expect(data.events[0].type == "date")
        #expect(data.events[1].type == "portfolio")
        // Indexes count map events only, as Dart's whereType<Map>() did.
        #expect(data.events.map(\.id) == ["date_0", "portfolio_1"])
        #expect(data.events[1].portfolioValue == 101000)
        #expect(data.events[1].holdings[0].ticker == "AAPL")
    }
}

struct BacktestDecisionTests {
    @Test func actionTakesPriorityOverDecisionInt() {
        #expect(BacktestDecision(json: ["decision": 1, "action": "sell", "symbol": "AAPL"]).decisionLabel() == "SELL")
    }

    @Test func decisionInt1IsBuyWhenActionAbsent() {
        #expect(BacktestDecision(json: ["decision": 1]).decisionLabel() == "BUY")
    }

    @Test func decisionIntMinus1IsSell() {
        #expect(BacktestDecision(json: ["decision": -1]).decisionLabel() == "SELL")
    }

    @Test func decision0IsHold() {
        #expect(BacktestDecision(json: ["decision": 0]).decisionLabel() == "HOLD")
    }

    @Test func nullDecisionIsHold() {
        #expect(BacktestDecision(json: [:]).decisionLabel() == "HOLD")
    }
}

struct PortfolioValuePointTests {
    @Test func epochSecondsMillisecondsAndIso() {
        let seconds = PortfolioValuePoint(json: ["timestamp": 1_700_000_000, "value": 5])
        #expect(DartDateTime.millisecondsSinceEpoch(seconds.timestamp) == 1_700_000_000_000)
        #expect(seconds.value == 5)
        let millis = PortfolioValuePoint(json: ["date": 1_700_000_000_123])
        #expect(DartDateTime.millisecondsSinceEpoch(millis.timestamp) == 1_700_000_000_123)
        let iso = PortfolioValuePoint(json: ["time": "2025-01-02T00:00:00Z"])
        #expect(DartDateTime.millisecondsSinceEpoch(iso.timestamp) == 1_735_776_000_000)
    }
}
