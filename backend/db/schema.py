"""Table registry and idempotent DDL.

Tables keep their RethinkDB names 1:1 and the default shape
``(id text PRIMARY KEY COLLATE "C", doc jsonb NOT NULL, updated_at timestamptz)``.
Every field that is a secondary index, a filter key, or a prefix-scan key
today becomes a STORED generated column plus a B-tree under COLLATE "C".

ALL_TABLES is the live ``table_list()`` (125 names, read 2026-08-22) plus the
two tables the BacktestResults split adds -- BacktestSteps and
BacktestProgress -- which have specs and so must be created by a bare
``ensure_schema()``, not only by the tests that name them explicitly.
TABLES holds an entry only where a table needs something beyond the default:
a non-id primary key, an index, a partition, a retention policy, an int id,
time fields, or suppressed notifications. The other ~85 tables are created
from the default template by name, with no entry to maintain. The one
exception is Instances, which carries a default-only entry so the registry
states out loud that its keys are text -- it was declared int, and every one
of its live keys is a string.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from . import pool as dbpool
from .errors import StoreError


@dataclass(frozen=True)
class PartitionSpec:
    by: str
    interval: str = "1 month"
    premake: int = 3


@dataclass(frozen=True)
class RetentionSpec:
    field: str
    days_env: str
    default_days: Optional[int] = None   # None => retention OFF by default
    # True when ``field`` names a real COLUMN rather than a key inside doc.
    # PriceHistory's "ts" is a timestamptz column; doc->>'ts' is always NULL
    # there, which made the sweep report deleted:0 forever.
    is_column: bool = False

    def days(self) -> Optional[int]:
        raw = os.environ.get(self.days_env)
        if raw is None or raw == "":
            return self.default_days
        try:
            return max(1, int(raw))
        except ValueError:
            raise StoreError("%s must be an integer, got %r" % (self.days_env, raw))


@dataclass(frozen=True)
class TableSpec:
    name: str
    id_type: str = "text"
    pk_field: str = "id"
    pk: tuple = ("id",)
    indexed_fields: tuple = ()
    prefix_fields: tuple = ()
    generated: Mapping[str, tuple] = field(default_factory=dict)
    compound_indexes: Mapping[str, str] = field(default_factory=dict)
    # Real (non-generated) columns the STORE must populate on insert, mapped
    # to (doc key, kind) where kind is "text" or "timestamp". A partitioned
    # table's partition key cannot be a generated column, so PriceHistory's
    # ticker/ts are written by store.insert from the document.
    column_sources: Mapping[str, tuple] = field(default_factory=dict)
    # Extra indexes that are not backed by a generated column: short name ->
    # index expression. Named "<Table>_<short>_idx", like the rest.
    extra_indexes: Mapping[str, str] = field(default_factory=dict)
    time_fields: tuple = ()
    partitioned: Optional[PartitionSpec] = None
    retention: Optional[RetentionSpec] = None
    notify: bool = True
    tune_autovacuum: bool = False
    ddl: Optional[str] = None


ALL_TABLES: tuple = (
    "AIBacktestingResults", "AgentBest", "AgentCycleLog", "AgentTop5",
    "AlpacaBarsCache", "AlphaAllocations", "AlphaBrokerOrders",
    "AlphaCashActivities", "AlphaEvents", "AlphaExperiments", "AlphaFills",
    "AlphaGates", "AlphaIncidents", "AlphaOrderIntents", "AlphaOutcomes",
    "AlphaPortfolioSnapshots", "AlphaPredictions", "AlphaState",
    "BacktestInstances", "BacktestProgress",
    "BacktestResults", "BacktestSteps", "BotTradeDecisions", "BrokerageAccounts",
    "ChatbotConversations", "Config",
    "DiscordMessageIds", "DiscordOutbox", "DiscoverPriceCache", "DiscoverStocks",
    "EarningsLLMCache", "EarningsLLMPromptCache", "EngineControl", "GoogleNewsCache",
    "GraphNexusActiveEventHistory", "GraphNexusActiveEventMaintenance",
    "GraphNexusActiveEvents", "GraphNexusAnalystPanel", "GraphNexusBenzingaCache",
    "GraphNexusDiscoveredStocks", "GraphNexusDiscoverySnapshots",
    "GraphNexusLLMPromptCache", "GraphNexusLearningCache", "GraphNexusMarketTrends",
    "GraphNexusNewsCache", "GraphNexusNewsDayFeatures", "GraphNexusNewsFinBERT",
    "GraphNexusNewsLLMCompany", "GraphNexusNewsLLMGoogle", "GraphNexusNewsLLMMacro",
    "GraphNexusNewsRaw", "GraphNexusOutcomeSeries", "GraphNexusOutcomes",
    "GraphNexusOverlayBarsCache", "GraphNexusOverlayResultCache", "GraphNexusProgress",
    "GraphNexusRotationCooldown", "GraphNexusTickerHistory", "GraphNexusTradeContexts",
    "GraphNexusTradeOutcomes", "Instances", "KalshiBacktestResults", "KalshiBacktests",
    "KalshiBtFixtureList", "KalshiHistCandles", "KalshiHistFixtures", "KalshiHistOdds",
    "KalshiModelRegistry", "LLMUsage", "LLMUsageDaily", "LearningActiveChanges",
    "LearningActivity", "LearningApprovals", "LearningBudgetLedger", "LearningConfig",
    "LearningEngineStatus", "LearningExperiments", "LearningFindings", "LearningFunnels",
    "LearningHypotheses", "LearningIntents", "LearningLease", "LearningNoiseFloors",
    "LearningObservationRollups", "LearningObservations", "LearningOutcomes",
    "LearningReports", "LiveBootAudit", "LiveCommands", "LiveDecisionAudit",
    "LiveOrderLifecycle", "LiveOrderWAL", "LivePrices", "LivePricesStocks", "LiveState",
    "MlNewsLLMPromptCache", "Models", "NewsLLM", "NewsLLMCache", "NewsLLMPromptCache",
    "NewsRaw", "NewsScored", "NexusGraphBuilds", "NexusRuntimeState",
    "NexusStrategyCache", "NotificationPreferences", "OutlierGraphPeers",
    "OutlierUniverseFeatures", "PointInTimeDatasetSnapshots",
    "PointInTimeManifests", "PriceHistory", "PushDevices", "Stocks", "Strategies",
    "TickerDayFeatures", "Users", "backtest_replay_calls",
    "backtest_replay_fixture_builds", "backtest_replay_fixtures",
    "backtest_replay_matrices", "backtest_replay_receipts", "h2h_history",
    "kalshi_capital_plan", "kalshi_clv_log", "kalshi_decisions", "kalshi_edge_history",
    "kalshi_edges", "kalshi_fills", "kalshi_live", "kalshi_market_listings",
    "kalshi_markets", "kalshi_odds_snapshots", "kalshi_orders",
    "kalshi_portfolio_snapshots", "kalshi_positions", "kalshi_scan_budget", "lineups",
    "match_features", "player_stats", "sports_fixtures", "team_stats",
)

_C = ' COLLATE "C"'


def _instance_coalesce() -> str:
    return "(coalesce(doc->>'instance_id', doc->>'instance', ''))" + _C


_SPECS = [
    TableSpec(
        "BacktestResults", id_type="int",
        indexed_fields=("status", "instance_id", "instance", "backtest_id",
                        "start_date", "end_date", "started_at", "created_at",
                        "timestamp", "completed_at"),
        # jsonb_array_length RAISES on a non-array, and a generated column that
        # raises makes every INSERT on the table fail. Guard on jsonb_typeof.
        generated={"tickers_total": ("integer",
                                     "(CASE WHEN jsonb_typeof(doc->'tickers') = 'array' "
                                     "      THEN jsonb_array_length(doc->'tickers') "
                                     "      ELSE 0 END)")},
        compound_indexes={
            "instance_or_instance_id": _instance_coalesce(),
            "list_ts": "(coalesce(doc->>'timestamp',''))" + _C,
            "instance_ts": _instance_coalesce() + ", (coalesce(doc->>'timestamp',''))" + _C,
        },
        tune_autovacuum=True),
    TableSpec(
        "BacktestSteps", notify=False,
        ddl='''CREATE TABLE IF NOT EXISTS "BacktestSteps" (
  backtest_id text COLLATE "C" NOT NULL,
  kind        text COLLATE "C" NOT NULL,
  seq         bigint NOT NULL,
  final       boolean NOT NULL DEFAULT false,
  doc         jsonb NOT NULL,
  PRIMARY KEY (backtest_id, kind, final, seq)
)''',
        tune_autovacuum=True),
    # BacktestProgress carries payload jsonb so the legacy scalar JSON TYPES
    # survive: live documents have progress as an int on a stopped run and a
    # float on a running one, and time_elapsed_seconds as an int running and a
    # float finished. A double precision column would turn 0 into 0.0 and
    # change the assembled bytes. last_active stays a plain writer-set column
    # -- a text->timestamptz cast is only STABLE and cannot be generated.
    TableSpec(
        "BacktestProgress",
        ddl='''CREATE TABLE IF NOT EXISTS "BacktestProgress" (
  id          text PRIMARY KEY COLLATE "C",
  payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  status      text COLLATE "C"
              GENERATED ALWAYS AS (payload ->> 'status') STORED,
  progress    double precision
              GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(payload -> 'progress') = 'number'
                                        THEN (payload ->> 'progress')::double precision
                                   END) STORED,
  time_elapsed_seconds double precision
              GENERATED ALWAYS AS (CASE WHEN jsonb_typeof(payload -> 'time_elapsed_seconds') = 'number'
                                        THEN (payload ->> 'time_elapsed_seconds')::double precision
                                   END) STORED,
  last_active timestamptz,
  updated_at  timestamptz NOT NULL DEFAULT now()
)''',
        compound_indexes={
            "status_norm":
                "((CASE WHEN lower(status) LIKE 'paused%' THEN 'paused' "
                "      ELSE lower(status) END) COLLATE \"C\")",
        }),
    # BacktestInstances is EMPTY live, so nothing observed contradicts the
    # declaration -- and broker.py builds its key with int(backtest_row_id),
    # so an int row_id is what the writer actually produces. Left as int.
    TableSpec("BacktestInstances", id_type="int", indexed_fields=("instance",)),
    # Instances is TEXT, not int -- the entry stays, with nothing but the
    # default, so the registry records the finding instead of going silent.
    # Every live primary key is a string (10 rows, sampled 2026-08-22):
    # '032f0c62-23a6-45a9-ad39-ed60ed13d106', 'main', 'alpaca-main',
    # 'alpaca-paper-pit', 'v2-conv-trt' -- and '10', a string that merely
    # LOOKS numeric. between(r.minval, "", index="id") returns nothing, so
    # there is not one numeric key in the table. Declared int, coerce_id
    # raised on every non-numeric key: get("Instances", "alpaca-main") -- the
    # live real-money instance -- was a StoreError, not a row. '10' is why a
    # "coerce it if it parses as an int" shortcut is wrong too: the stored key
    # is the string, and only leaving it untouched round-trips it. Every
    # id-keyed table in ALL_TABLES was probed the same way and this was the
    # only disagreement -- see tests/dbcore/test_id_type_registry.py.
    TableSpec("Instances"),
    # Strategies IS int: 125 rows, ids 20/4/5/3/15/13, and
    # between("", r.maxval, index="id") returns nothing -- no string keys.
    TableSpec("Strategies", id_type="int"),

    TableSpec(
        "PriceHistory", pk=("ticker", "ts", "id"), notify=False,
        partitioned=PartitionSpec(by="ts", interval="1 month"),
        retention=RetentionSpec(field="ts", days_env="RETAIN_PRICE_HISTORY_DAYS",
                                is_column=True),
        # priceBroker.py:186 writes {ticker, price, timestamp, type} and no id.
        column_sources={"ticker": ("ticker", "text"),
                        "ts": ("timestamp", "timestamp")},
        # design 3.4 line 503: get("PriceHistory", id) is used by the migration
        # verifier, and without this it is an all-partition sequential scan.
        extra_indexes={"id": "(id)"},
        tune_autovacuum=True,
        ddl='''CREATE TABLE IF NOT EXISTS "PriceHistory" (
  ticker      text COLLATE "C" NOT NULL,
  ts          timestamptz NOT NULL,
  id          text COLLATE "C" NOT NULL,
  doc         jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, ts, id)
) PARTITION BY RANGE (ts)'''),

    TableSpec("GraphNexusTradeContexts",
              indexed_fields=("instance_id", "base_instance_id", "date"),
              prefix_fields=("instance_id",),
              compound_indexes={"instance_base":
                                "(split_part(doc->>'instance_id', '|', 1))" + _C},
              retention=RetentionSpec(field="date",
                                      days_env="RETAIN_TRADE_CONTEXTS_DAYS"),
              tune_autovacuum=True),
    TableSpec("GraphNexusOutcomes",
              indexed_fields=("instance_id", "base_instance_id"),
              prefix_fields=("instance_id",)),
    TableSpec("GraphNexusTradeOutcomes",
              indexed_fields=("instance_id", "base_instance_id"),
              prefix_fields=("instance_id",)),
    TableSpec("GraphNexusOutcomeSeries", notify=False,
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusActiveEvents",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusActiveEventHistory",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusActiveEventMaintenance",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusAnalystPanel",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusDiscoveredStocks",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusMarketTrends",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    # r.now() wrote a native time on these two; the ported writer stores an
    # ISO-8601 string and time_fields decodes it back to a tz-aware datetime,
    # so readers see the same Python type they saw under RethinkDB.
    TableSpec("GraphNexusNewsCache",
              time_fields=("cached_at", "sentiment_cached_at")),
    TableSpec("GraphNexusTickerHistory", time_fields=("last_updated",)),
    TableSpec("GraphNexusRotationCooldown", prefix_fields=("id",)),
    TableSpec("GraphNexusLearningCache", prefix_fields=("id",)),
    TableSpec("GraphNexusDiscoverySnapshots", prefix_fields=("id",)),
    TableSpec("NexusRuntimeState", prefix_fields=("id",)),
    TableSpec("NexusStrategyCache",
              indexed_fields=("instance_id", "instance_id_config_hash", "origin"),
              prefix_fields=("id",)),
    # Outlier sleeve (docs/superpowers/specs/2026-09-02-outlier-sleeve-design.md §4):
    # id = "YYYY-MM-DD|SYMBOL", so one date's cross-section is a single prefix scan.
    TableSpec("OutlierUniverseFeatures", prefix_fields=("id",)),
    TableSpec("OutlierGraphPeers"),
    TableSpec("LiveBootAudit", indexed_fields=("instance_id",), prefix_fields=("id",)),
    # r.now() wrote a native time on these; the ported writer stores an
    # ISO-8601 string and time_fields decodes it back to a tz-aware datetime,
    # so readers see the same Python type they saw under RethinkDB.
    TableSpec("LiveCommands", indexed_fields=("instance_id",),
              time_fields=("created_at", "started_at", "completed_at",
                           "lease_expires_at")),
    TableSpec("LiveState", time_fields=("last_updated",)),
    TableSpec("NexusGraphBuilds",
              time_fields=("started_at", "completed_at", "last_tail_update")),
    # nexus_runtime_state.ensure_alpha_wal_indexes() created these two.
    TableSpec("LiveOrderWAL", indexed_fields=("instance_id", "client_order_id"),
              prefix_fields=("client_order_id",)),
    TableSpec("LiveOrderLifecycle", indexed_fields=("instance_id",)),
    TableSpec("AlphaEvents", indexed_fields=("kind",)),
    TableSpec("AlphaExperiments",
              indexed_fields=("record_kind", "search_scope", "registered_at")),

    # Cache tables index the ISO timestamp STRING, not a timestamptz: a
    # text->timestamptz cast is STABLE (it reads DateStyle/TimeZone) and PG
    # rejects it in a generated column. This matches
    # backend/self_learning/retention.py:46-57, whose cutoff_iso() already
    # "returns a cutoff string so the caller can push a between(...).delete()
    # into the DB".
    TableSpec("GraphNexusLLMPromptCache", notify=False,
              indexed_fields=("cached_at",),
              retention=RetentionSpec(field="cached_at",
                                      days_env="RETAIN_PROMPT_CACHE_DAYS"),
              tune_autovacuum=True),
    TableSpec("AlpacaBarsCache", notify=False,
              indexed_fields=("cached_at",),
              retention=RetentionSpec(field="cached_at",
                                      days_env="RETAIN_BARS_CACHE_DAYS"),
              tune_autovacuum=True),
    TableSpec("LLMUsage", notify=False,
              indexed_fields=("backtest_id", "instance_id", "model", "provider", "ts"),
              retention=RetentionSpec(field="ts", days_env="RETAIN_LLM_USAGE_DAYS")),
    TableSpec("LLMUsageDaily", indexed_fields=("date",)),
    TableSpec("GraphNexusNewsLLMMacro", notify=False),
    TableSpec("GraphNexusNewsLLMCompany", notify=False),
    TableSpec("GraphNexusNewsDayFeatures", notify=False),

    TableSpec("BotTradeDecisions", indexed_fields=("brokerage_id",)),
    TableSpec("ChatbotConversations", indexed_fields=("user_id",)),
    TableSpec("Users", indexed_fields=("username",)),
    TableSpec("NewsLLMCache", indexed_fields=("article_hash",)),
    TableSpec("LearningExperiments", indexed_fields=("registered_at", "status")),
    TableSpec("LearningFindings", indexed_fields=("detected_at",)),
    TableSpec("LearningFunnels", indexed_fields=("observed_at",)),
    TableSpec("LearningObservations", indexed_fields=("as_of", "run_id"),
              retention=RetentionSpec(field="as_of",
                                      days_env="RETAIN_LEARNING_OBSERVATIONS_DAYS",
                                      default_days=90)),
    TableSpec("LearningOutcomes",
              indexed_fields=("as_of", "observation_id", "run_id")),
    # benchmark_alpha/records.py's ten typed-record tables. api_reads.py pages
    # them through the run_asof / instance_origin_asof compound indexes
    # (pg_store._INDEX_FIELDS); the leading components carry the equality and
    # as_of carries the order, so each component is indexed on its own.
    ] + [
    TableSpec(_name, indexed_fields=("run_id", "instance_id", "origin", "as_of"),
              compound_indexes={
                  "run_asof": '(doc->>\'run_id\')' + _C + ", (doc->>'as_of')" + _C,
                  "instance_origin_asof":
                      '(doc->>\'instance_id\')' + _C + ", (doc->>'origin')" + _C
                      + ", (doc->>'as_of')" + _C,
              })
    for _name in ("AlphaPredictions", "AlphaGates", "AlphaAllocations",
                  "AlphaOrderIntents", "AlphaBrokerOrders", "AlphaFills",
                  "AlphaPortfolioSnapshots", "AlphaCashActivities",
                  "AlphaOutcomes", "AlphaIncidents")
    ] + [
    TableSpec("KalshiBacktests", indexed_fields=("status",)),

    # Non-`id` primary keys (live table_config; == kalshi/db.py registry).
    TableSpec("sports_fixtures", pk_field="fixture_id"),
    TableSpec("lineups", pk_field="fixture_id"),
    TableSpec("match_features", pk_field="fixture_id"),
    TableSpec("kalshi_market_listings", pk_field="fixture_id"),
    TableSpec("kalshi_markets", pk_field="market_ticker"),
    TableSpec("kalshi_orders", pk_field="client_order_id"),
    TableSpec("kalshi_scan_budget", pk_field="window"),
    TableSpec("kalshi_capital_plan", pk_field="instance_id"),
    TableSpec("KalshiHistFixtures", pk_field="fixture_key"),
]

TABLES: dict = {s.name: s for s in _SPECS}

_DEFAULT_TEMPLATE = '''CREATE TABLE IF NOT EXISTS {q} (
  id          text PRIMARY KEY COLLATE "C",
  doc         jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
)'''

STRIP_FN = '''
CREATE OR REPLACE FUNCTION jsonb_strip_literal(v jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT CASE
    WHEN jsonb_typeof(v) = 'object' AND v ? '__db_literal__'
         AND (SELECT count(*) FROM jsonb_object_keys(v)) = 1
      THEN jsonb_strip_literal(v -> '__db_literal__')
    WHEN jsonb_typeof(v) = 'object' THEN
      coalesce((SELECT jsonb_object_agg(k, jsonb_strip_literal(val))
                FROM jsonb_each(v) AS e(k, val)), '{}'::jsonb)
    WHEN jsonb_typeof(v) = 'array' THEN
      coalesce((SELECT jsonb_agg(jsonb_strip_literal(el) ORDER BY ord)
                FROM jsonb_array_elements(v) WITH ORDINALITY AS a(el, ord)),
               '[]'::jsonb)
    ELSE v
  END
$fn$;
'''

# The SQL twin of merge._strip: a value that REPLACES rather than merges is
# stripped of every r.literal wire sentinel inside it, at any depth. Without
# it Literal(Literal(x)) -- and any Literal nested in a replacement subtree or
# array -- left a raw {"__db_literal__": ...} object in the stored document.
MERGE_FN = '''
CREATE OR REPLACE FUNCTION jsonb_deep_merge(a jsonb, b jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT CASE
    WHEN jsonb_typeof(b) = 'object' AND b ? '__db_literal__'
         AND (SELECT count(*) FROM jsonb_object_keys(b)) = 1
      THEN jsonb_strip_literal(b -> '__db_literal__')
    WHEN jsonb_typeof(a) <> 'object' OR jsonb_typeof(b) <> 'object'
      THEN jsonb_strip_literal(b)
    ELSE (
      -- COALESCE: jsonb_object_agg over ZERO rows returns SQL NULL, so
      -- merging two empty objects returned null instead of '{}' -- and
      -- nested, jsonb_deep_merge('{"a":{}}','{"a":{}}') produced
      -- '{"a": null}', silently corrupting the document. Python's
      -- deep_merge returns {} there.
      SELECT coalesce(jsonb_object_agg(k, v), '{}'::jsonb) FROM (
        SELECT k, CASE
                    WHEN a ? k AND b ? k THEN jsonb_deep_merge(a -> k, b -> k)
                    WHEN b ? k            THEN
                      CASE WHEN jsonb_typeof(b -> k) = 'object'
                           THEN jsonb_deep_merge('{}'::jsonb, b -> k)
                           ELSE jsonb_strip_literal(b -> k) END
                    ELSE a -> k
                  END AS v
        FROM (SELECT jsonb_object_keys(a) AS k
              UNION SELECT jsonb_object_keys(b)) AS keys
      ) AS merged
    )
  END
$fn$;
'''

NOTIFY_FN = '''
CREATE OR REPLACE FUNCTION notify_row() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  PERFORM pg_notify('tbl:' || TG_TABLE_NAME, COALESCE(NEW.id, OLD.id));
  RETURN NULL;
END $fn$;
'''

_AUTOVACUUM = ('ALTER TABLE {q} SET (autovacuum_vacuum_scale_factor = 0.02, '
               'autovacuum_analyze_scale_factor = 0.01, '
               'toast.autovacuum_vacuum_scale_factor = 0.02)')

_ensure_lock = threading.Lock()

# (search_path, partition_name) pairs already known to exist in this process.
_partition_memo: set = set()


def quoted(name: str) -> str:
    if '"' in name:
        raise StoreError("illegal table name %r" % name)
    return '"%s"' % name


def spec(table: str) -> TableSpec:
    """Unknown table -> the default template. New tables need no registry edit."""
    return TABLES.get(table) or TableSpec(name=table)


def has_doc_column(s: TableSpec) -> bool:
    """False for the one table (BacktestProgress) whose shape has no ``doc``."""
    if s.ddl is None:
        return True
    return "doc " in s.ddl and "jsonb" in s.ddl.split("doc ", 1)[1][:40]


def _statements(s: TableSpec) -> list:
    """Every statement needed to bring one table to its declared shape."""
    q = quoted(s.name)
    out = [s.ddl if s.ddl else _DEFAULT_TEMPLATE.format(q=q)]
    for f in s.indexed_fields:
        out.append('ALTER TABLE %s ADD COLUMN IF NOT EXISTS "%s" text COLLATE "C" '
                   "GENERATED ALWAYS AS (doc ->> '%s') STORED" % (q, f, f))
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_idx" ON %s ("%s")'
                   % (s.name, f, q, f))
    for col, (sql_type, expr) in sorted(s.generated.items()):
        out.append('ALTER TABLE %s ADD COLUMN IF NOT EXISTS "%s" %s '
                   "GENERATED ALWAYS AS (%s) STORED" % (q, col, sql_type, expr))
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_idx" ON %s ("%s")'
                   % (s.name, col, q, col))
    for f in s.prefix_fields:
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_pfx" ON %s ("%s" text_pattern_ops)'
                   % (s.name, f, q, f))
    for index_name, expr in sorted(s.compound_indexes.items()):
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_idx" ON %s (%s)'
                   % (s.name, index_name, q, expr))
    for index_name, expr in sorted(s.extra_indexes.items()):
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_idx" ON %s %s'
                   % (s.name, index_name, q, expr))
    if s.notify:
        out.append('DROP TRIGGER IF EXISTS "%s_notify" ON %s' % (s.name, q))
        out.append('CREATE TRIGGER "%s_notify" AFTER INSERT OR UPDATE OR DELETE ON %s '
                   "FOR EACH ROW EXECUTE FUNCTION notify_row()" % (s.name, q))
    if s.tune_autovacuum and s.partitioned is None:
        # A partitioned table has no storage of its own -- PG rejects storage
        # parameters on it and points at the leaf partitions, which
        # ensure_partitions() tunes as it creates them.
        out.append(_AUTOVACUUM.format(q=q))
    return out


def _already_applied(cur, s: TableSpec) -> bool:
    """True when every object this spec declares already exists."""
    cur.execute("SELECT to_regclass(%s) AS t", (quoted(s.name),))
    if cur.fetchone()["t"] is None:
        return False
    cur.execute("SELECT attname FROM pg_attribute "
                "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped",
                (quoted(s.name),))
    cols = {r["attname"] for r in cur.fetchall()}
    if not (set(s.indexed_fields) | set(s.generated)) <= cols:
        return False
    cur.execute("SELECT c.relname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE i.indrelid = %s::regclass", (quoted(s.name),))
    idx = {r["relname"] for r in cur.fetchall()}
    wanted_idx = {"%s_%s_idx" % (s.name, f) for f in s.indexed_fields}
    wanted_idx |= {"%s_%s_idx" % (s.name, c) for c in s.generated}
    wanted_idx |= {"%s_%s_pfx" % (s.name, f) for f in s.prefix_fields}
    wanted_idx |= {"%s_%s_idx" % (s.name, n) for n in s.compound_indexes}
    wanted_idx |= {"%s_%s_idx" % (s.name, n) for n in s.extra_indexes}
    if not wanted_idx <= idx:
        return False
    if s.notify:
        cur.execute("SELECT 1 FROM pg_trigger WHERE tgrelid = %s::regclass "
                    "AND tgname = %s AND NOT tgisinternal",
                    (quoted(s.name), "%s_notify" % s.name))
        if cur.fetchone() is None:
            return False
    return True


def ensure_schema(*, tables: Optional[Iterable[str]] = None) -> list:
    """Idempotent DDL for the whole database (or the named subset).

    Creates jsonb_deep_merge and notify_row first, then every table, column,
    index and trigger. Returns the statements it actually ran, so a second
    call on an unchanged database returns []. Runs under an advisory lock so
    concurrent process boots do not race on CREATE INDEX.
    """
    names = list(tables) if tables is not None else list(ALL_TABLES)
    applied = []
    with _ensure_lock:
        with dbpool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(hashtext('intellistock.ddl'))")
                try:
                    # to_regprocedure resolves through search_path. A bare
                    # pg_proc lookup does NOT: it sees the function in ANY
                    # schema of the database, so a sibling schema that has
                    # notify_row made this skip the CREATEs and every
                    # CREATE TRIGGER then failed with "function notify_row()
                    # does not exist". Probe both functions, not just one.
                    cur.execute("SELECT to_regprocedure('jsonb_deep_merge(jsonb,jsonb)') "
                                "AS m, to_regprocedure('jsonb_strip_literal(jsonb)') "
                                "AS s, to_regprocedure('notify_row()') AS n")
                    fns = cur.fetchone()
                    if fns["s"] is None:
                        cur.execute(STRIP_FN)
                        applied.append("jsonb_strip_literal")
                    if fns["m"] is None:
                        cur.execute(MERGE_FN)
                        applied.append("jsonb_deep_merge")
                    if fns["n"] is None:
                        cur.execute(NOTIFY_FN)
                        applied.append("notify_row")
                    for name in names:
                        s = spec(name)
                        if _already_applied(cur, s):
                            continue
                        for stmt in _statements(s):
                            cur.execute(stmt)
                            applied.append(stmt)
                    conn.commit()
                except Exception:
                    # A failed statement poisons the transaction; roll back
                    # BEFORE unlocking or the unlock raises
                    # InFailedSqlTransaction and masks the real DDL error.
                    conn.rollback()
                    raise
                finally:
                    # Unlock AFTER the commit: the lock exists to cover the
                    # DDL commit, and releasing it first left a window where a
                    # second booting process saw neither the lock nor the
                    # committed objects.
                    try:
                        cur.execute(
                            "SELECT pg_advisory_unlock(hashtext('intellistock.ddl'))")
                    except Exception:
                        pass
    # Partitions are created OUTSIDE that transaction: the parent table has to
    # be committed before a sibling connection can attach a partition to it.
    # Plan A task 6 -- "wired into ensure_schema" -- without this, the first
    # insert fails with "no partition of relation ... found for row".
    for name in names:
        s = spec(name)
        if s.partitioned is None:
            continue
        import datetime as _dt
        today = _dt.datetime.now(_dt.timezone.utc).date()
        premake = max(1, int(s.partitioned.premake))
        hi = today
        for _ in range(premake):
            hi = _dt.date(hi.year + (hi.month == 12), hi.month % 12 + 1, 1)
        applied.extend(ensure_partitions(name, lo=today, hi=hi))
    return applied


def ensure_table(table: str) -> None:
    """On-demand path for the sites that create a table lazily (llm_utils
    prompt cache, backtest_evidence_runtime, kalshi ensure_tables)."""
    ensure_schema(tables=[table])


def partition_name(table: str, month_start) -> str:
    return "%s_p%04d_%02d" % (table, month_start.year, month_start.month)


def _month_starts(lo, hi):
    import datetime as _dt
    cur = _dt.date(lo.year, lo.month, 1)
    end = _dt.date(hi.year, hi.month, 1)
    out = []
    while cur <= end:
        out.append(cur)
        cur = _dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def ensure_partitions(table: str, *, lo, hi) -> list:
    """Create the monthly range partitions covering [lo, hi].

    In production pg_partman's background worker keeps partitions ahead
    (premake 3). This is the path tests and the migration copy use, where the
    hourly partman tick is too slow. There is deliberately NO default
    partition: a default partition forbids DETACH CONCURRENTLY.
    """
    import datetime as _dt
    s = spec(table)
    if s.partitioned is None:
        raise StoreError("%s is not a partitioned table" % table)
    # store.insert calls this on every write to a partitioned table, so the
    # already-created months must cost nothing. The memo is keyed by
    # search_path: each test runs in its own schema and must not inherit
    # another's belief that a partition exists.
    memo_scope = os.environ.get("PG_SEARCH_PATH", "")
    wanted = [d for d in _month_starts(lo, hi)
              if (memo_scope, partition_name(table, d)) not in _partition_memo]
    if not wanted:
        return []
    made = []
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            for start in wanted:
                nxt = _dt.date(start.year + (start.month == 12),
                               start.month % 12 + 1, 1)
                part = partition_name(table, start)
                cur.execute("SELECT to_regclass(%s) AS t", (quoted(part),))
                if cur.fetchone()["t"] is not None:
                    _partition_memo.add((memo_scope, part))
                    continue
                # CREATE TABLE is a utility statement: PG accepts no bind
                # parameters in FOR VALUES and answers "could not determine
                # data type of parameter $1". The bounds are therefore
                # inlined as literals -- they are formatted from date objects
                # this module computed, never from caller input. The explicit
                # +00 offset keeps the month boundary from shifting with the
                # session's TimeZone.
                cur.execute("SAVEPOINT p")
                try:
                    cur.execute(
                        "CREATE TABLE %s PARTITION OF %s FOR VALUES "
                        "FROM ('%s 00:00:00+00'::timestamptz) "
                        "TO ('%s 00:00:00+00'::timestamptz)"
                        % (quoted(part), quoted(table),
                           start.isoformat(), nxt.isoformat()))
                except Exception as exc:
                    # A sibling process (or pg_partman) won the race. Both
                    # errors mean the partition now exists, which is the
                    # postcondition this function promises.
                    if type(exc).__name__ not in ("DuplicateTable",
                                                  "DuplicateObject",
                                                  "InvalidObjectDefinition"):
                        raise
                    cur.execute("ROLLBACK TO SAVEPOINT p")
                    _partition_memo.add((memo_scope, part))
                    continue
                cur.execute("RELEASE SAVEPOINT p")
                _partition_memo.add((memo_scope, part))
                if s.tune_autovacuum:
                    # The 0.2 default scale factor means 570,050 dead tuples on
                    # PriceHistory before autovacuum reacts. Leaf partitions
                    # are where the storage actually lives.
                    cur.execute(_AUTOVACUUM.format(q=quoted(part)))
                made.append(part)
        conn.commit()
    return made
