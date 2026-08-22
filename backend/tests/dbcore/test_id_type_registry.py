"""Every table's declared ``id_type``, pinned to the type its keys really are.

The maps below are DATA, not opinion: read 2026-08-22 from the production
RethinkDB, read-only, with two cheap probes per table --

    r.db('IntelliStock').table(T).pluck('id').limit(12)            # the types
    r.db('IntelliStock').table(T).between(r.minval, "", index='id').limit(1)
    r.db('IntelliStock').table(T).between("", r.maxval, index='id').limit(1)

RethinkDB sorts every number before every string, so the two ``between``
probes answer "does this table hold ANY numeric key / ANY string key" in one
indexed row read each, without scanning PriceHistory's 2.85M rows.

That sweep found exactly one disagreement: ``Instances`` was declared
``id_type="int"`` while all ten of its live keys are strings -- uuid4s,
``'main'``, ``'alpaca-main'`` (the live real-money instance), and ``'10'``, a
string that merely looks numeric. ``store.coerce_id`` rejects a non-integer
key on an int-declared table, so after the cutover ``get("Instances",
"alpaca-main")`` raised StoreError instead of returning the row.

The evidence is encoded here rather than re-queried so the pin runs in CI with
no RETHINKDB_HOST and no database of any kind. Re-run the probes above when a
table's keys legitimately change type, and update the data -- the point is
that the declaration can never drift from the keys silently again.
"""
import pytest

from db import schema, store
from db.errors import StoreError
from db.fake import FakeStore

# table -> the Python type of every primary key sampled there.
#   "int"  = only numbers, and between("", maxval) found no string key
#   "text" = only strings, and between(minval, "") found no numeric key
#   ""     = the table is empty live, so it attests to nothing
LIVE_KEY_TYPE = {
    "AIBacktestingResults": "text", "AgentBest": "text",
    "AgentCycleLog": "text", "AgentTop5": "text", "AlpacaBarsCache": "text",
    "AlphaState": "text", "BacktestInstances": "", "BacktestResults": "int",
    "BotTradeDecisions": "text", "BrokerageAccounts": "text",
    "ChatbotConversations": "text", "Config": "text",
    "DiscordMessageIds": "text", "DiscordOutbox": "text",
    "DiscoverPriceCache": "", "DiscoverStocks": "",
    "EarningsLLMCache": "text", "EarningsLLMPromptCache": "text",
    "EngineControl": "text", "GoogleNewsCache": "text",
    "GraphNexusActiveEventHistory": "text",
    "GraphNexusActiveEventMaintenance": "text",
    "GraphNexusActiveEvents": "text", "GraphNexusAnalystPanel": "text",
    "GraphNexusBenzingaCache": "text", "GraphNexusDiscoveredStocks": "text",
    "GraphNexusDiscoverySnapshots": "text", "GraphNexusLLMPromptCache": "text",
    "GraphNexusLearningCache": "text", "GraphNexusMarketTrends": "text",
    "GraphNexusNewsCache": "text", "GraphNexusNewsDayFeatures": "text",
    "GraphNexusNewsFinBERT": "text", "GraphNexusNewsLLMCompany": "text",
    "GraphNexusNewsLLMGoogle": "text", "GraphNexusNewsLLMMacro": "text",
    "GraphNexusNewsRaw": "text", "GraphNexusOutcomeSeries": "text",
    "GraphNexusOutcomes": "text", "GraphNexusOverlayBarsCache": "text",
    "GraphNexusOverlayResultCache": "", "GraphNexusProgress": "text",
    "GraphNexusRotationCooldown": "text", "GraphNexusTickerHistory": "text",
    "GraphNexusTradeContexts": "text", "GraphNexusTradeOutcomes": "text",
    "Instances": "text", "KalshiBacktestResults": "text",
    "KalshiBacktests": "text", "KalshiBtFixtureList": "text",
    "KalshiHistCandles": "text", "KalshiHistOdds": "text",
    "KalshiModelRegistry": "text", "LLMUsage": "text", "LLMUsageDaily": "",
    "LearningActiveChanges": "", "LearningActivity": "text",
    "LearningApprovals": "", "LearningBudgetLedger": "",
    "LearningConfig": "text", "LearningEngineStatus": "text",
    "LearningExperiments": "", "LearningFindings": "text",
    "LearningFunnels": "text", "LearningHypotheses": "text",
    "LearningIntents": "text", "LearningLease": "", "LearningNoiseFloors": "",
    "LearningObservationRollups": "", "LearningObservations": "text",
    "LearningOutcomes": "text", "LearningReports": "", "LiveBootAudit": "text",
    "LiveCommands": "text", "LiveDecisionAudit": "", "LiveOrderLifecycle": "",
    "LiveOrderWAL": "text", "LivePrices": "text", "LivePricesStocks": "text",
    "LiveState": "text", "MlNewsLLMPromptCache": "text", "Models": "text",
    "NewsLLM": "text", "NewsLLMCache": "text", "NewsLLMPromptCache": "text",
    "NewsRaw": "text", "NewsScored": "text", "NexusGraphBuilds": "text",
    "NexusRuntimeState": "text", "NexusStrategyCache": "text",
    "NotificationPreferences": "text", "PointInTimeDatasetSnapshots": "text",
    "PointInTimeManifests": "text", "PriceHistory": "text",
    "PushDevices": "text", "Stocks": "text", "Strategies": "int",
    "TickerDayFeatures": "text", "Users": "text",
    "backtest_replay_calls": "", "backtest_replay_fixture_builds": "",
    "backtest_replay_fixtures": "", "backtest_replay_matrices": "text",
    "backtest_replay_receipts": "", "h2h_history": "",
    "kalshi_capital_plan": "", "kalshi_clv_log": "text",
    "kalshi_decisions": "text", "kalshi_edge_history": "text",
    "kalshi_edges": "", "kalshi_fills": "text", "kalshi_live": "text",
    "kalshi_market_listings": "", "kalshi_markets": "",
    "kalshi_odds_snapshots": "", "kalshi_orders": "text",
    "kalshi_portfolio_snapshots": "text", "kalshi_positions": "",
    "kalshi_scan_budget": "text", "lineups": "", "match_features": "",
    "player_stats": "", "sports_fixtures": "", "team_stats": "",
}

# Real keys, copied verbatim, for the tables where the key SHAPE is the point.
# A key here must survive coerce_id byte for byte.
LIVE_KEY_SAMPLES = {
    # The finding. '10' is the one that kills every "coerce it if it parses as
    # an int" shortcut: the stored key is the string.
    "Instances": ("032f0c62-23a6-45a9-ad39-ed60ed13d106", "10",
                  "alpaca-main", "alpaca-paper-pit", "main", "v2-conv-trt"),
    # Instances is not the only table keyed by a numeric-looking STRING --
    # the three news tables key on the provider's article id.
    "NewsLLM": ("48744591", "48741995"),
    "NewsRaw": ("48741662", "48739415"),
    "NewsScored": ("48741662", "48739415"),
    # Genuinely int-keyed, and verified so: no string key exists in either.
    "Strategies": (20, 4, 5, 3, 15, 13),
    "BacktestResults": (101666, 100025, 108253),
    # Scope-suffixed and pipe-joined keys, the shape most of the graph uses.
    "GraphNexusTradeContexts": (
        "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|AACI",),
    "LiveState": ("alpaca-paper-pit", "main", "alpaca-main"),
    "Config": ("Cache", "Config", "Pings"),
    "Stocks": ("AACB", "ABM"),
    "PriceHistory": ("000013aa-30d6-40ff-8653-02c02b68ef9a",),
    # Custom primary keys: the declared id_type still governs coerce_id.
    "kalshi_orders": ("91d96888-de30-54fd-be3d-ba9827dacad3",),
    "kalshi_scan_budget": ("2026-07", "2026-06"),
}

# An int declaration on a table with no live rows attests to nothing, so each
# one needs a reason that does not come from the data.
EMPTY_BUT_DECLARED_INT = {
    "BacktestInstances":
        "empty live; broker.py builds the key as int(backtest_row_id), so the "
        "writer itself produces an int",
}


def _declared(table):
    return schema.spec(table).id_type


def test_the_evidence_covers_every_table_the_registry_declares_int():
    """A new id_type="int" must arrive with evidence, or this fails."""
    for name, spec_ in schema.TABLES.items():
        if spec_.id_type != "int":
            continue
        assert name in LIVE_KEY_TYPE, (
            "%s declares id_type='int' with no sampled evidence" % name)
        assert LIVE_KEY_TYPE[name] == "int" or name in EMPTY_BUT_DECLARED_INT, (
            "%s declares id_type='int' but its live keys are %r"
            % (name, LIVE_KEY_TYPE[name]))


def test_declared_id_type_matches_the_live_key_type():
    wrong = {}
    for table, observed in LIVE_KEY_TYPE.items():
        if not observed:                       # empty table: attests nothing
            continue
        if _declared(table) != observed:
            wrong[table] = (_declared(table), observed)
    assert wrong == {}, (
        "declared id_type disagrees with the live primary keys: %r" % wrong)


def test_instances_is_text_because_every_live_key_is_a_string():
    """The blocking bug, stated as the one assertion that would have caught it."""
    assert _declared("Instances") == "text"
    for key in LIVE_KEY_SAMPLES["Instances"]:
        assert store.coerce_id("Instances", key) == key
    # The live real-money instance, by name: this raised StoreError before.
    assert store.coerce_id("Instances", "alpaca-main") == "alpaca-main"


def test_a_numeric_looking_string_key_is_not_normalised_through_int():
    """'10' is a STRING key. int('10') would round-trip by luck; '007' and
    '1.0' would not, and neither would '10 '. Nothing may parse the key."""
    for key in ("10", "007", "1.0", "10 ", "0x10", "1e3", "+1", "-0"):
        assert store.coerce_id("Instances", key) == key
    for key in LIVE_KEY_SAMPLES["NewsLLM"] + LIVE_KEY_SAMPLES["NewsRaw"]:
        assert store.coerce_id("NewsLLM", key) == key


def test_every_sampled_key_round_trips_through_coerce_id():
    for table, keys in LIVE_KEY_SAMPLES.items():
        for key in keys:
            got = store.coerce_id(table, key)
            if isinstance(key, str):
                assert got == key and type(got) is str, (table, key, got)
            else:
                # An int-keyed table stores the digits; both spellings of the
                # same key must land on the same row.
                assert got == str(key)
                assert store.coerce_id(table, str(key)) == got


def test_an_int_table_still_refuses_a_key_it_cannot_represent():
    """The protection Instances never needed, kept where it IS true."""
    for table in ("Strategies", "BacktestResults"):
        with pytest.raises(StoreError):
            store.coerce_id(table, "alpaca-main")


def test_the_samples_agree_with_the_type_map():
    """Guards the evidence itself: a hand-edited sample cannot contradict the
    column that drives the assertions."""
    for table, keys in LIVE_KEY_SAMPLES.items():
        observed = LIVE_KEY_TYPE[table]
        for key in keys:
            assert observed == ("int" if isinstance(key, int) else "text"), \
                (table, key, observed)


def test_fake_store_and_the_real_store_share_one_coerce_id():
    fake = FakeStore()
    assert fake.coerce_id is store.coerce_id
    for table, keys in LIVE_KEY_SAMPLES.items():
        for key in keys:
            assert fake.coerce_id(table, key) == store.coerce_id(table, key)


def test_fake_store_round_trips_the_instances_keys_that_broke():
    """Parity where it counts: the fake is what the ~475 db-less tests run
    against, so a key that reads back wrong there is a bug nobody sees."""
    fake = FakeStore()
    for key in LIVE_KEY_SAMPLES["Instances"]:
        fake.insert("Instances", {"id": key, "name": key})
    for key in LIVE_KEY_SAMPLES["Instances"]:
        assert fake.get("Instances", key) == {"id": key, "name": key}
    # '10' and 10 are different keys to RethinkDB; here they both address the
    # one row that exists, and it is the STRING row -- never a second one.
    assert fake.get("Instances", 10)["id"] == "10"
    assert fake.count("Instances") == len(LIVE_KEY_SAMPLES["Instances"])
