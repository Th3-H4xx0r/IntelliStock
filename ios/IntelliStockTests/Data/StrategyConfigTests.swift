import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/features/strategies/strategy_config_test.dart. (The
/// "rank theming helpers" group re-implements a strategies-screen helper
/// inside the test; the strategies feature ports it.)
struct HumanizeStrategyConfigKeyTests {
    @Test func plainWordsAreTitleCased() {
        #expect(humanizeStrategyConfigKey("buy_threshold") == "Buy Threshold")
        #expect(humanizeStrategyConfigKey("max_discovered_stocks") == "Max Discovered Stocks")
    }

    @Test func knownAcronymsAreUppercased() {
        #expect(humanizeStrategyConfigKey("llm_provider") == "LLM Provider")
        #expect(humanizeStrategyConfigKey("rsi_period") == "RSI Period")
        #expect(humanizeStrategyConfigKey("macd_signal") == "MACD Signal")
        #expect(humanizeStrategyConfigKey("vwap_threshold") == "VWAP Threshold")
        #expect(humanizeStrategyConfigKey("api_key") == "API Key")
        #expect(humanizeStrategyConfigKey("atr_multiplier") == "ATR Multiplier")
        #expect(humanizeStrategyConfigKey("etf_portfolio_pct") == "ETF Portfolio Pct")
        #expect(humanizeStrategyConfigKey("usd_threshold") == "USD Threshold")
        #expect(humanizeStrategyConfigKey("sec_filing") == "SEC Filing")
        #expect(humanizeStrategyConfigKey("ai_enabled") == "AI Enabled")
    }

    @Test func multiAcronymKeys() {
        #expect(humanizeStrategyConfigKey("llm_api_key") == "LLM API Key")
        #expect(humanizeStrategyConfigKey("rsi_macd_combo") == "RSI MACD Combo")
    }

    @Test func emptyKeyReturnsEmptyString() {
        #expect(humanizeStrategyConfigKey("") == "")
    }

    @Test func singleToken() {
        #expect(humanizeStrategyConfigKey("weight") == "Weight")
        #expect(humanizeStrategyConfigKey("rsi") == "RSI")
    }

    @Test func handlesLeadingAndTrailingUnderscores() {
        #expect(humanizeStrategyConfigKey("_weight_") == "Weight")
    }
}

struct StrategyConfigFieldMetaTests {
    @Test func returnsKnownLabelForGraphNexusAnalysisFields() {
        let meta = getStrategyConfigFieldMeta("graph_nexus_analysis", "buy_threshold")
        #expect(meta.label == "Buy Score Threshold")
        #expect(!meta.description.isEmpty)
    }

    @Test func fallsBackToHumanizeForUnknownKeys() {
        let meta = getStrategyConfigFieldMeta("graph_nexus_analysis", "some_custom_field")
        #expect(meta.label == "Some Custom Field")
        #expect(meta.description == "")
    }

    @Test func returnsHumanizedLabelForUnknownStrategy() {
        #expect(getStrategyConfigFieldMeta("unknown_strategy", "rsi_period").label == "RSI Period")
    }

    @Test func allKnownGraphNexusAnalysisFieldsResolve() throws {
        let known = try #require(strategyFieldMeta["graph_nexus_analysis"])
        #expect(known.count == 68)
        for key in known.keys {
            #expect(!getStrategyConfigFieldMeta("graph_nexus_analysis", key).label.isEmpty, "label for \(key)")
        }
        // An apostrophe and a quote-free description survive the port verbatim.
        #expect(known["analyst_panel_enabled"]?.description.contains("debate each other's views") == true)
    }

    @Test func optionListsAndRoleLabelsKeepTheirOrder() {
        #expect(llmProviderOptions.map(\.value) == [
            "gemini", "deepseek", "openai", "azure", "nvidia", "ollama", "bedrock", "openrouter", "claude-cli", "codex-cli",
        ])
        #expect(claudeCliEffortOptions.last == SelectOption(value: "max", label: "Max"))
        #expect(knownLlmRoleLabels.map(\.key) == [
            "", "sentiment_", "company_article_", "macro_article_", "event_maintenance_", "overlay_", "analyst_panel_",
        ])
        #expect(knownLookbackLlmRoleLabels.first { $0.key == "lookback_overlay_" }?.value == "Trade Overlay LLM (Lookback)")
    }
}

struct StrategyModelTests {
    @Test func fromStrategyJsonCarriesSubStrategyNames() {
        let row = StrategyListRow(strategyJson: [
            "id": 42,
            "name": "My Strategy",
            "strategies": [["strategy": "graph_nexus_analysis"], ["strategy": "momentum"]],
            "instances_using": ["inst1", "inst2"],
        ])
        #expect(row.id == 42)
        #expect(row.name == "My Strategy")
        #expect(row.subCount == 2)
        #expect(row.subStrategyNames == ["graph_nexus_analysis", "momentum"])
        #expect(row.instancesUsing == ["inst1", "inst2"])
    }

    @Test func fromStrategyJsonWithBestBacktestStats() {
        let row = StrategyListRow(
            strategyJson: ["id": 7, "name": "Nexus", "strategies": [["strategy": "graph_nexus_analysis"]], "instances_using": []],
            bestPnl: 4500.25,
            bestPnlBid: "bt-99",
            bestPct: 45.0,
            bestPctBid: "bt-99",
            runCount: 12,
            rank: 1
        )
        #expect(row.bestPnl == 4500.25)
        #expect(row.rank == 1)
        #expect(row.isTop5)
        #expect(row.runCount == 12)
    }

    @Test func agentResultParsesAllFieldsCorrectly() {
        let r = AgentResult(json: [
            "backtest_id": "bt-42",
            "strategy_id": 7,
            "overall_profit": "1234.56",
            "pnl_percent": "12.34",
            "stocks_used": ["AAPL", "MSFT"],
            "start_date": "2025-01-01",
            "end_date": "2025-06-01",
            "created_at": "2025-06-10T10:00:00Z",
        ])
        #expect(r.backtestId == "bt-42")
        #expect(r.strategyId == 7)
        #expect(close(r.overallProfit, 1234.56, 0.001))
        #expect(close(r.pnlPercent, 12.34, 0.001))
        #expect(r.stocksUsed == ["AAPL", "MSFT"])
    }

    @Test func agentResultHandlesNullOverallProfit() {
        let r = AgentResult(json: ["backtest_id": "x"])
        #expect(r.overallProfit == nil)
        #expect(r.pnlPercent == nil)
    }

    @Test func subStrategyMergesConditionsIntoConfigSkippingEmpties() {
        let strategy = Strategy(json: [
            "id": 3,
            "name": "S",
            "strategies": [
                ["type": "rsi", "conditions": ["a": 1, "b": "", "c": nil], "config": ["a": 2, "d": "x"]],
                "junk",
                ["strategy": "macd", "execution_position": 9],
            ],
        ])
        #expect(strategy.strategies.count == 2)
        let first = strategy.strategies[0]
        #expect(first.strategy == "rsi")
        #expect(first.config == ["a": 2, "d": "x"])
        #expect(first.executionPosition == 0)
        #expect(first.decisionPhase == "pre")
        // fallbackPosition is the index in the raw list (the junk entry counts).
        #expect(strategy.strategies[1].executionPosition == 9)
    }
}
