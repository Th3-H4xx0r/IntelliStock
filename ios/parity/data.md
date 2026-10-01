# Parity checklist — data layers (Wave 1, agent "data")

Generated from the Dart sources, one line per `fromJson` field, computed getter, repository method (verb + path) and top-level helper; body keys and rulings added by hand.
`[x]` = ported and checked against the Dart; `→ native form:` notes a deliberate change of form.

Counts: 1315 done (of which 38 carry a `→ native form` note), 9 open — all nine are Riverpod family
providers declared in kalshi/crypto data files, which are view state for the Wave 2 kalshi agent.

## Rulings

- Ruling: Dart `num?` fields are `Num?` (Core/Models/Num.swift), an int-or-double enum whose `description` is Dart's `toString()` — Dart printed `5` vs `5.0` from the same field and Review Focus 2 forbids `5.0` where Dart showed `5` — cost if wrong: views write `.double` before formatting.
- Ruling: Dart `dynamic` → `JSON`; `Map<String, dynamic>` (fields and raw repository returns) → `[String: JSON]`; `List<Map<String, dynamic>>` → `[[String: JSON]]` — keeps the map typing honest; wrap with `JSON(m)["k"]` for Dart-style reads — cost if wrong: a little ceremony at call sites.
- Ruling: cast failures are lenient. `get<Map>` on a non-map body reads as `{}`; `x as String?` on a non-string coerces via `toString()`; `(e as Map)` list items that are not maps are skipped (Kalshi edges/positions/instances). Dart threw a TypeError in all three — no Flutter screen showed a useful message for those, and the spec says never crash — cost if wrong: a malformed body shows empty or partial data instead of an error row.
- Ruling: `DateTime.tryParse` → `DartDateTime.tryParse` (Core/Models/DartDateTime.swift), Dart's own regex: `Z`/`±HH[:MM]` → UTC, no suffix → device-local, six fraction digits, rollover of out-of-range fields — a backend string Dart accepted or rejected is accepted or rejected here — cost if wrong: none known.
- Ruling: `toStringAsFixed` → `dartToStringAsFixed` (exact-expansion, ties away from zero). `String(format:)` rounds exact ties to even (`2.5` → `2`, `0.125` → `0.12`) where Dart printed `3` and `0.13` — cost if wrong: none.
- Ruling: `jsonEncode` → `JSON.dartEncoded(indent:)` with sorted keys, used by `_asStr` (`llmAsString`) — `JSON` objects carry no key order — cost if wrong: a structured `smoke_response` shows its keys alphabetically.
- Ruling: `aggregateBySector` and `todaysMovers` take ordered `(symbol, value)` pairs, not dictionaries — the Dart maps were built from the positions list and their insertion order decided ties (e.g. several 0 % movers pre-market); a Swift dictionary would reshuffle them on every refresh — cost if wrong: callers build an array instead of a dictionary.
- Ruling: ET wall-clock dates (`etFromUtc`, `isMarketOpenAtEt`) are `Date`s read in GMT via `etWallClockCalendar`; tests build them with `etWallClock(...)` — mirrors Dart's shifted-UTC `DateTime` — cost if wrong: none (same arithmetic).
- Ruling: `Instance.copyWith` (with a nullable sentinel) → `var` properties; `Conversation`, `CategoryRoute` and `InstanceBacktestRow` keep a `copyWith` because their tests or controllers call it — cost if wrong: none.
- Ruling: `BestPerStrategy` (a mutable Dart class) → struct with `mutating func fold`; `computeBestByStrategy` mutates through the dictionary subscript — cost if wrong: none.
- Ruling: `DashboardModel` and `SelectedAccountModel` are keep-alive (live in `AppServices`); `dashboardServicesProvider`, `engineBusyProvider` and `brokeragesProvider` were autoDispose in Dart, so the dashboard view calls `pollServices()` / `loadBrokerages()` from `.task` on each appearance to refetch as the Dart rebuild did. `pollServices()` fetches immediately when its task (re)starts, where `IntervalPoller.resume()` waited one interval — cost if wrong: one extra `/status` fan-out on foreground.
- Ruling: `DashboardModel` takes `repository: () -> DashboardRepository` and re-reads it per call — a client rebuilt for a new server URL (Review Focus 5) is used without rebuilding the model — cost if wrong: none.
- Ruling: `DashboardModel.brokeragesValue` mirrors Riverpod's `valueOrNull` (last good list survives a failed refetch); `brokerages` mirrors `.when` — kalshi_screen / insights read `valueOrNull` — cost if wrong: none.
- Ruling: `SelectedAccountModel` hydrates synchronously in `init` (Dart: async after `build` returned null) — the keychain read is synchronous on iOS — cost if wrong: no flash of the first account on launch (an improvement, not a regression).
- Ruling: `Loadable<T>` is a stand-in in Features/Dashboard/Model/LoadableShim.swift with the plan's exact three cases; delete it at merge — the core agent owns `Loadable` and had not committed it — cost if wrong: a one-file delete at merge.
- Ruling: `BacktestRepository.graphData` and `playbackData` are `@concurrent` so their large payloads (thousands of rows, each with a date parse) parse off the main actor — cost if wrong: none.
- Ruling: `SwingRepository.approvedSignals` issues its two GETs with `async let` (Dart `Future.wait`); the test asserts the set of queries, not their order — cost if wrong: none.
- Ruling: `DashboardRepository.services()`, `StrategyRepository.agentBest()`, `CryptoRepository.accountEquity()` and `TokenUsageRepository.fetchAll()` are non-throwing — the Dart versions caught every error internally — cost if wrong: none.
- Ruling: `PortfolioHistory.sinceLocalMidnight(now:calendar:)` takes defaulted `now`/`calendar` parameters (Dart read `DateTime.now()`) — testability — cost if wrong: none.
- Ruling: `AgentStage.stocks` (`.cast<String>()`, which threw lazily on a non-string) prints each element with `toString()` — cost if wrong: none.
- Ruling: `knownLlmRoleLabels` / `knownLookbackLlmRoleLabels` are `KeyValuePairs` — the Dart const maps were ordered — cost if wrong: lookups use `first { $0.key == k }`.
- Ruling: repository tests use `IntelliStockTests/Data/Support/DataStub.swift` (a per-stub unique host), not `StubURLProtocol` — `StubURLProtocol` keeps one global handler, and Swift Testing runs suites in parallel (`.serialized` orders tests inside one suite only), so two suites using it race — cost if wrong: none; it reuses the `Data(reading:)`, `jsonBody` and `queryItems` helpers from StubURLProtocol.swift.

## Seams for the orchestrator (folders this agent does not own)

- JSON object key order: `JSON.object` is a `[String: JSON]` decoded by `JSONSerialization`, so server key order is lost. Dart maps kept it. Every Dart view that iterated a map without sorting (strategy `config`/`conditions`, finding `evidence`, `RecentCall.raw`, `pnlPerStock`, node counts, …) will list keys in arbitrary, run-to-run varying order. Fix in Core/JSON: an order-preserving decoder (`case object` over an ordered store), or each view sorts keys.
- `JSON.data()` writes a whole `Double` as `100000`, where Dart's `jsonEncode(100000.0)` wrote `100000.0` (`initial_cash` in the instance/crypto backtest bodies). Python decodes one as `int`, the other as `float`. Fix in Core/JSON if byte-identical bodies matter: encode `.double` with `dartDoubleString`.
- Name overlap risk at merge: this branch adds `extension JSON` members (`or`, `isObject`, `isArray`, `isNum`, `isString`, `objectElements`, `stringElements`, `num`, `lenientNum`, `numOrParsedDouble`, `dartEncoded`) and globals (`DartDateTime`, `dartToStringAsFixed`, `dartCompare`, `dartEncodeComponent`, `Num`). If the core agent added any with the same name, keep one.

## `lib/features/agent_runs/data/agent_repository.dart`

- [x] type `AgentStage`
  - [x] `AgentStage.label` ← `(j['label'] ?? '').toString()`
  - [x] `AgentStage.status` ← `(j['status'] ?? 'pending').toString()`
  - [x] `AgentStage.stocks` ← `(j['stocks'] as List? ?? const []).cast<String>()`
  - [x] `AgentStage.pnl` ← `j['pnl'] as num?`
  - [x] `AgentStage.pnlPct` ← `j['pnl_pct'] as num?`
  - [x] `AgentStage.details` ← `j['details']?.toString()`
- [x] type `AgentRun`
  - [x] `AgentRun.id` ← `(j['id'] ?? '').toString()`
  - [x] `AgentRun.status` ← `(j['status'] ?? 'stopped').toString()`
  - [x] `AgentRun.cycleId` ← `j['cycle_id']?.toString()`
  - [x] `AgentRun.name` ← `j['name']?.toString()`
  - [x] `AgentRun.createdAt` ← `j['created_at'] == null`
  - [x] `AgentRun.stages` ← `(j['stages'] as List? ?? const [])`
  - [x] `AgentRun.finalResult` ← `j['final_result']?.toString()`
- [x] type `AgentRunsPage`
  - [x] `AgentRunsPage.runs` ← `(j['runs'] as List? ?? const [])`
  - [x] `AgentRunsPage.total` ← `(j['total'] as num?)?.toInt() ?? 0`
  - [x] `AgentRunsPage.totalPages` ← `(j['total_pages'] as num?)?.toInt() ?? 1`
  - [x] `AgentRunsPage.page` ← `(j['page'] as num?)?.toInt() ?? 1`
- [x] type `AgentControl`
  - [x] getter `AgentControl.isRunning`
  - [x] getter `AgentControl.isPaused`
  - [x] getter `AgentControl.isStopped`
  - [x] `AgentControl.running` ← `j['running'] == true`
  - [x] `AgentControl.paused` ← `j['paused'] == true`
- [x] type `AgentRepository`
  - [x] method `AgentRepository.runs`
    - [x] `GET /agent/runs`
  - [x] method `AgentRepository.control`
    - [x] `GET /agent/control`
  - [x] method `AgentRepository.setControl`
    - [x] `POST /agent/control`
  - [x] method `AgentRepository.forceStop`
    - [x] `POST /agent/runs/$logId/force-stop`
- [x] top-level `agentRepositoryProvider` → native form: `AppServices.agentRepository` (AppServices+Repositories.swift)

## `lib/features/auth/data/auth_repository.dart`

- [x] type `AuthRepository`
  - [x] method `AuthRepository.login`
    - [x] `POST /auth/login`
  - [x] method `AuthRepository.fetchMe`
    - [x] `GET /auth/me`
- [x] top-level `authRepositoryProvider` → native form: `AppServices.authRepository` (AppServices+Repositories.swift)

## `lib/features/backtests/data/backtest_repository.dart`

- [x] type `BacktestRepository`
  - [x] method `BacktestRepository.list`
    - [x] `GET /backtests`
  - [x] method `BacktestRepository.get`
    - [x] `GET /backtests/$id`
  - [x] method `BacktestRepository.delete`
    - [x] `DELETE /backtests/$id`
  - [x] method `BacktestRepository.status`
    - [x] `GET /backtests/$id/status`
  - [x] method `BacktestRepository.summary`
    - [x] `GET /backtests/$id/summary`
  - [x] method `BacktestRepository.graphData`
    - [x] `GET /backtests/$id/graph-data`
  - [x] method `BacktestRepository.playbackData`
    - [x] `GET /backtests/$id/playback-data`
  - [x] method `BacktestRepository.logs`
    - [x] `GET /backtests/$id/logs`
  - [x] method `BacktestRepository.llmCost`
    - [x] `GET /backtests/$id/llm-cost`
  - [x] method `BacktestRepository.action`
    - [x] `POST /backtests/$id/$name`
  - [x] method `BacktestRepository.create`
    - [x] `POST /backtests`
- [x] top-level `backtestRepositoryProvider` → native form: `AppServices.backtestRepository` (AppServices+Repositories.swift)

## `lib/features/brokerages/data/brokerage_repository.dart`

- [x] type `BrokerageRepository`
  - [x] method `BrokerageRepository.list`
    - [x] `GET /brokerages`
  - [x] method `BrokerageRepository.link`
    - [x] `POST /brokerages`
  - [x] method `BrokerageRepository.edit`
    - [x] `PUT /brokerages/$id`
  - [x] method `BrokerageRepository.remove`
    - [x] `DELETE /brokerages/$id`
  - [x] method `BrokerageRepository.testAlpaca`
    - [x] `POST /brokerages/test-alpaca`
- [x] top-level `brokerageRepositoryProvider` → native form: `AppServices.brokerageRepository` (AppServices+Repositories.swift)

## `lib/features/chatbot/data/chatbot_repository.dart`

- [x] type `ChatbotRepository`
  - [x] method `ChatbotRepository.conversations`
    - [x] `GET /chatbot/conversations`
  - [x] method `ChatbotRepository.createConversation`
    - [x] `POST /chatbot/conversations`
  - [x] method `ChatbotRepository.conversation`
    - [x] `GET /chatbot/conversations/$id`
  - [x] method `ChatbotRepository.patchConversation`
    - [x] `PATCH /chatbot/conversations/$id`
  - [x] method `ChatbotRepository.deleteConversation`
    - [x] `DELETE /chatbot/conversations/$id`
  - [x] method `ChatbotRepository.clear`
    - [x] `POST /chatbot/conversations/$id/clear`
  - [x] method `ChatbotRepository.turn`
    - [x] `POST /chatbot/conversations/$id/turn`
  - [x] method `ChatbotRepository.confirmTool`
    - [x] `POST /chatbot/conversations/$conversationId/confirm-tool`
  - [x] method `ChatbotRepository.tools`
    - [x] `GET /chatbot/tools`
  - [x] method `ChatbotRepository.models`
    - [x] `GET /models`
- [x] top-level `chatbotRepositoryProvider` → native form: `AppServices.chatbotRepository` (AppServices+Repositories.swift)

## `lib/features/crypto/data/crypto_repository.dart`

- [x] type `CryptoRepository`
  - [x] method `CryptoRepository.listInstances`
    - [x] `GET /instances`
  - [x] method `CryptoRepository.getInstance`
    - [x] `GET /instances/$id`
  - [x] method `CryptoRepository.instanceBacktests`
    - [x] `GET /instances/$id/backtests`
  - [x] method `CryptoRepository.createInstance`
    - [x] `POST /instances`
  - [x] method `CryptoRepository.updateInstance`
    - [x] `PATCH /instances/$id`
  - [x] method `CryptoRepository.createBacktest`
    - [x] `POST /backtests`
  - [x] method `CryptoRepository.startInstance`
    - [x] `POST /instances/$id/start`
  - [x] method `CryptoRepository.stopInstance`
    - [x] `POST /instances/$id/stop`
  - [x] method `CryptoRepository.deleteInstance`
    - [x] `DELETE /instances/$id`
  - [x] method `CryptoRepository.brokerages`
    - [x] `GET /brokerages`
  - [x] method `CryptoRepository.strategies`
    - [x] `GET /strategies`
  - [x] method `CryptoRepository.accountEquity`
    - [x] `GET /brokerages/$brokerageId/positions`
- [x] top-level `cryptoRepositoryProvider` → native form: `AppServices.cryptoRepository` (AppServices+Repositories.swift)
- [ ] top-level `cryptoInstancesProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent

## `lib/features/dashboard/data/dashboard_repository.dart`

- [x] type `EngineStatus`
  - [x] `EngineStatus.id` ← `(json['id'] as String? ?? '')`
  - [x] `EngineStatus.status` ← `(json['status'] as String? ?? 'stopped')`
  - [x] `EngineStatus.details` ← `json['details'] as String?`
- [x] type `ServicesSnapshot`
  - [x] method `ServicesSnapshot.engineById`
  - [x] method `ServicesSnapshot.statusFor`
  - [x] method `ServicesSnapshot.isRunning`
  - [x] method `ServicesSnapshot.isPaused`
- [x] type `BrokerageAccount`
  - [x] `BrokerageAccount.id` ← `(json['id'] as String? ?? '')`
  - [x] `BrokerageAccount.accountName` ← `(json['account_name'] as String? ?? '')`
  - [x] `BrokerageAccount.brokerageType` ← `(json['brokerage_type'] as String? ?? '')`
  - [x] `BrokerageAccount.status` ← `(json['status'] as String? ?? '')`
  - [x] `BrokerageAccount.alpacaPaper` ← `json['alpaca_paper'] == true`
  - [x] getter `BrokerageAccount.isActive`
- [x] type `AccountPosition`
  - [x] `AccountPosition.symbol` ← `(json['symbol'] as String? ?? '')`
  - [x] `AccountPosition.qty` ← `(json['qty'] as num?)?.toDouble() ?? 0`
  - [x] `AccountPosition.marketValue` ← `(json['marketValue'] as num?)?.toDouble() ?? 0`
  - [x] `AccountPosition.unrealizedPnl` ← `(json['unrealizedPnl'] as num?)?.toDouble() ?? 0`
  - [x] `AccountPosition.unrealizedPnlPct` ← `(json['unrealizedPnlPct'] as num?)?.toDouble() ?? 0`
  - [x] `AccountPosition.lastPrice` ← `(json['lastPrice'] as num?)?.toDouble()`
  - [x] `AccountPosition.avgEntryPrice` ← `(json['avgEntryPrice'] as num?)?.toDouble()`
- [x] type `AccountHoldings`
  - [x] getter `AccountHoldings.isEmpty`
- [x] type `DashboardRepository`
  - [x] method `DashboardRepository.services`
    - [x] `GET /status`
    - [x] `GET /agent/control`
    - [x] `GET /digest/control`
    - [x] `GET /nexus/status`
  - [x] method `DashboardRepository.brokerages`
    - [x] `GET /brokerages`
  - [x] method `DashboardRepository.portfolioHistory`
    - [x] `GET /brokerages/$id/portfolio-history`
  - [x] method `DashboardRepository.accountHoldings`
    - [x] `GET /brokerages/$id/positions`
  - [x] method `DashboardRepository.nexusTrends`
    - [x] `GET /brokerages/$id/trends`
  - [x] method `DashboardRepository.backfillQueue`
    - [x] `GET /brokerages/$id/backfill-queue`
  - [x] method `DashboardRepository.discoveredStocks`
    - [x] `GET /brokerages/$id/discovered`
  - [x] method `DashboardRepository.tradeContexts`
    - [x] `GET /brokerages/$id/trade-contexts`
  - [x] method `DashboardRepository.nexusOutcomes`
    - [x] `GET /brokerages/$id/nexus-outcomes`
  - [x] method `DashboardRepository.momentumWatchlist`
    - [x] `GET /brokerages/$id/momentum-watchlist`
  - [x] method `DashboardRepository.startPriceService`
    - [x] `POST /config/run-price-service`
  - [x] method `DashboardRepository.terminatePrice`
    - [x] `POST /config/terminate-price`
  - [x] method `DashboardRepository.controlDiscover`
    - [x] `POST /discover/control`
  - [x] method `DashboardRepository.controlAgent`
    - [x] `POST /agent/control`
  - [x] method `DashboardRepository.controlDigest`
    - [x] `POST /digest/control`
  - [x] method `DashboardRepository.digestSendNow`
    - [x] `POST /digest/send-now`
  - [x] method `DashboardRepository.controlNexus`
    - [x] `POST /nexus/control`
- [x] top-level `dashboardRepositoryProvider` → native form: `AppServices.dashboardRepository` (AppServices+Repositories.swift)

## `lib/features/dashboard/data/nexus_models.dart`

- [x] type `MarketTrend`
  - [x] getter `MarketTrend.bullish`
  - [x] getter `MarketTrend.hasReversal`
  - [x] `MarketTrend.id` ← `(json['id'] as String? ?? '')`
  - [x] `MarketTrend.name` ← `(json['name'] as String? ?? '')`
  - [x] `MarketTrend.status` ← `(json['status'] as String? ?? 'active')`
  - [x] `MarketTrend.direction` ← `(json['direction'] as String? ?? 'bullish')`
  - [x] `MarketTrend.strength` ← `(json['strength'] as num?)?.toDouble() ?? 0`
  - [x] `MarketTrend.tickers` ← `_strs(json['affected_tickers'])`
  - [x] `MarketTrend.sectors` ← `_strs(json['affected_sectors'])`
  - [x] `MarketTrend.reversalCount` ← `(json['reversal_articles'] as List? ?? const []).length`
  - [x] `MarketTrend.endedAt` ← `(json['ended_at'] as String?) ??`
- [x] type `NexusTrendsView`
  - [x] getter `NexusTrendsView.reversalWatch`
  - [x] getter `NexusTrendsView.isEmpty`
- [x] type `BackfillItem`
  - [x] `BackfillItem.ticker` ← `(json['ticker'] as String? ?? '').toUpperCase()`
  - [x] `BackfillItem.score` ← `(json['score'] as num?)?.toDouble() ?? 0`
  - [x] `BackfillItem.nPaths` ← `(json['n_paths'] as num?)?.toInt() ?? 0`
  - [x] `BackfillItem.source` ← `(json['source'] as String? ?? '')`
  - [x] `BackfillItem.priority` ← `(json['priority'] as bool?) ?? false`
- [x] type `DiscoveredStock`
  - [x] `DiscoveredStock.ticker` ← `(json['ticker'] as String? ?? '').toUpperCase()`
  - [x] `DiscoveredStock.source` ← `(json['source'] as String? ?? '')`
  - [x] `DiscoveredStock.sourceTicker` ← `json['source_ticker'] as String?`
  - [x] `DiscoveredStock.discoveredAt` ← `(json['discovered_at'] as String?) ??`
- [x] type `TradeRationale`
  - [x] `TradeRationale.symbol` ← `(json['symbol'] as String? ?? '').toUpperCase()`
  - [x] `TradeRationale.reason` ← `(json['reason'] as String? ?? '')`
  - [x] `TradeRationale.eventType` ← `(json['dominant_event_type'] as String? ?? '')`
  - [x] `TradeRationale.actionIntent` ← `(json['action_intent'] as String? ?? '')`
  - [x] `TradeRationale.score` ← `(json['score'] as num?)?.toDouble() ?? 0`
- [x] type `OutcomeRow`
  - [x] getter `OutcomeRow.isLong`
  - [x] getter `OutcomeRow.correct`
  - [x] `OutcomeRow.symbol` ← `(json['symbol'] as String? ?? '').toUpperCase()`
  - [x] `OutcomeRow.actionIntent` ← `(json['action_intent'] as String? ?? '')`
  - [x] `OutcomeRow.latestReturn` ← `(json['latest_return'] as num?)?.toDouble() ?? 0`
  - [x] `OutcomeRow.eventType` ← `(json['dominant_event_type'] as String? ?? '')`
  - [x] `OutcomeRow.entryDate` ← `(json['entry_date'] as String? ?? '')`
- [x] type `OutcomeStats`
  - [x] getter `OutcomeStats.isEmpty`
  - [x] `OutcomeStats.hitRate` ← `(json['hit_rate'] as num?)?.toDouble() ?? 0`
  - [x] `OutcomeStats.n` ← `(json['n'] as num?)?.toInt() ?? 0`
  - [x] `OutcomeStats.nCorrect` ← `(json['n_correct'] as num?)?.toInt() ?? 0`
  - [x] `OutcomeStats.avgReturn` ← `(json['avg_return'] as num?)?.toDouble() ?? 0`
  - [x] `OutcomeStats.recent` ← `(json['recent'] as List? ?? const [])`
- [x] type `WatchlistEntry`
  - [x] `WatchlistEntry.symbol` ← `(json['symbol'] as String? ?? '').toUpperCase()`
  - [x] `WatchlistEntry.firstSeenBar` ← `(json['first_seen_bar'] as num?)?.toInt() ?? 0`
  - [x] `WatchlistEntry.firstSeenPrice` ← `(json['first_seen_price'] as num?)?.toDouble() ?? 0`
- [x] type `WatchlistSummary`
  - [x] getter `WatchlistSummary.isEmpty`
  - [x] `WatchlistSummary.count` ← `(json['count'] as num?)?.toInt() ?? 0`
  - [x] `WatchlistSummary.newest` ← `(json['newest'] as List? ?? const [])`

## `lib/features/instances/data/instance_repository.dart`

- [x] type `InstanceRepository`
  - [x] method `InstanceRepository.listInstances`
    - [x] `GET /instances`
  - [x] method `InstanceRepository.getInstance`
    - [x] `GET /instances/$id`
  - [x] method `InstanceRepository.createInstance`
    - [x] `POST /instances`
  - [x] method `InstanceRepository.patchInstance`
    - [x] `PATCH /instances/$id`
  - [x] method `InstanceRepository.deleteInstance`
    - [x] `DELETE /instances/$id`
  - [x] method `InstanceRepository.startInstance`
    - [x] `POST /instances/$id/start`
  - [x] method `InstanceRepository.stopInstance`
    - [x] `POST /instances/$id/stop`
  - [x] method `InstanceRepository.clearState`
    - [x] `POST /instances/$id/clear-state`
  - [x] method `InstanceRepository.previewClearState`
    - [x] `POST /instances/$id/clear-state`
  - [x] method `InstanceRepository.applyClearState`
    - [x] `POST /instances/$id/clear-state`
  - [x] method `InstanceRepository.addStock`
    - [x] `POST /instances/$id/stocks`
  - [x] method `InstanceRepository.removeStock`
    - [x] `DELETE /instances/$id/stocks/$symbol`
  - [x] method `InstanceRepository.linkBrokerage`
    - [x] `POST /instances/$id/link-brokerage`
  - [x] method `InstanceRepository.unlinkBrokerage`
    - [x] `PATCH /instances/$id`
  - [x] method `InstanceRepository.linkDataBrokerage`
    - [x] `POST /instances/$id/link-data-brokerage`
  - [x] method `InstanceRepository.linkStrategy`
    - [x] `POST /instances/$id/link-strategy`
  - [x] method `InstanceRepository.unlinkStrategy`
    - [x] `POST /instances/$id/unlink-strategy`
  - [x] method `InstanceRepository.listBacktests`
    - [x] `GET /instances/$instanceId/backtests`
  - [x] method `InstanceRepository.createBacktest`
    - [x] `POST /backtests`
  - [x] method `InstanceRepository.getBacktestStatus`
    - [x] `GET /backtests/$backtestId/status`
  - [x] method `InstanceRepository.listBrokerages`
    - [x] `GET /brokerages`
  - [x] method `InstanceRepository.listStrategies`
    - [x] `GET /strategies`
- [x] top-level `instanceRepositoryProvider` → native form: `AppServices.instanceRepository` (AppServices+Repositories.swift)

## `lib/features/kalshi/data/kalshi_repository.dart`

- [x] type `KalshiPortfolio`
  - [x] getter `KalshiPortfolio.isPaper`
  - [x] `KalshiPortfolio.value` ← `(j['value'] as num?)?.toDouble() ?? 0`
  - [x] `KalshiPortfolio.cash` ← `(j['cash'] as num?)?.toDouble() ?? 0`
  - [x] `KalshiPortfolio.dayChange` ← `(j['day_change'] as num?)?.toDouble() ?? 0`
  - [x] `KalshiPortfolio.series` ← `raw.map((p) => ((p as Map)['value'] as num?)?.toDouble() ?? 0).toList()`
  - [x] `KalshiPortfolio.seriesTs` ← `raw.map((p) => DateTime.tryParse(((p as Map)['ts'] ?? '').toString()) ?? DateTime.now()).toList()`
  - [x] `KalshiPortfolio.paperPnl` ← `(j['paper_pnl'] as num?)?.toDouble()`
  - [x] `KalshiPortfolio.paperSeries` ← `praw.map((p) => ((p as Map)['pnl'] as num?)?.toDouble() ?? 0).toList()`
  - [x] `KalshiPortfolio.paperSeriesTs` ← `praw.map((p) => DateTime.tryParse(((p as Map)['ts'] ?? '').toString()) ?? DateTime.now()).toList()`
- [x] type `KalshiEdge`
  - [x] `KalshiEdge.marketTicker` ← `(j['market_ticker'] ?? '').toString()`
  - [x] `KalshiEdge.side` ← `(j['side'] ?? '').toString()`
  - [x] `KalshiEdge.edge` ← `(j['edge'] as num?)?.toDouble() ?? 0`
- [x] type `KalshiPosition`
  - [x] `KalshiPosition.marketTicker` ← `(j['market_ticker'] ?? '').toString()`
  - [x] `KalshiPosition.side` ← `(j['side'] ?? '').toString()`
  - [x] `KalshiPosition.contracts` ← `(j['contracts'] as num?)?.toInt() ?? 0`
  - [x] `KalshiPosition.unrealizedCents` ← `(j['unrealized_cents'] as num?)?.toDouble()`
  - [x] `KalshiPosition.match` ← `(j['match'] ?? '').toString()`
  - [x] `KalshiPosition.pickLabel` ← `(j['pick_label'] ?? '').toString()`
  - [x] `KalshiPosition.pickLogo` ← `(j['pick_logo'] ?? '').toString()`
  - [x] `KalshiPosition.maxPayout` ← `(j['max_payout'] as num?)?.toDouble() ?? 0`
  - [x] `KalshiPosition.cost` ← `(j['cost'] as num?)?.toDouble() ?? 0`
  - [x] `KalshiPosition.currentValue` ← `(j['current_value'] as num?)?.toDouble()`
  - [x] `KalshiPosition.oddsPct` ← `(j['odds_pct'] as num?)?.toDouble()`
- [x] type `KalshiInstance`
  - [x] `KalshiInstance.id` ← `(j['id'] ?? '').toString()`
  - [x] `KalshiInstance.name` ← `(j['name'] ?? 'Kalshi instance').toString()`
  - [x] `KalshiInstance.running` ← `j['running'] == true`
  - [x] `KalshiInstance.liveEnabled` ← `j['live_enabled'] == true`
- [x] type `KalshiRepository`
  - [x] method `KalshiRepository.portfolio`
    - [x] `GET /brokerages/$bid/kalshi/portfolio`
  - [x] method `KalshiRepository.edges`
    - [x] `GET /brokerages/$bid/kalshi/edges`
  - [x] method `KalshiRepository.positions`
    - [x] `GET /brokerages/$bid/kalshi/positions`
  - [x] method `KalshiRepository.kill`
    - [x] `POST /brokerages/$bid/kalshi/kill`
  - [x] method `KalshiRepository.instances`
    - [x] `GET /brokerages/$bid/kalshi/instances`
  - [x] method `KalshiRepository.createInstance`
    - [x] `POST /brokerages/$bid/kalshi/instances`
  - [x] method `KalshiRepository.startInstance`
    - [x] `POST /instances/$id/start`
  - [x] method `KalshiRepository.stopInstance`
    - [x] `POST /instances/$id/stop`
  - [x] method `KalshiRepository.instanceDetail`
    - [x] `GET /instances/$id/kalshi/detail`
  - [x] method `KalshiRepository.instanceDecisions`
    - [x] `GET /instances/$id/kalshi/decisions`
  - [x] method `KalshiRepository.instanceLive`
    - [x] `GET /instances/$id/kalshi/live`
  - [x] method `KalshiRepository.instanceOrders`
    - [x] `GET /instances/$id/kalshi/orders`
  - [x] method `KalshiRepository.models`
    - [x] `GET /models`
  - [x] method `KalshiRepository.updateInstance`
    - [x] `PATCH /instances/$id/kalshi/config`
  - [x] method `KalshiRepository.deleteInstance`
    - [x] `DELETE /instances/$id`
  - [x] method `KalshiRepository.createBacktest`
    - [x] `POST /brokerages/$bid/kalshi/backtests`
  - [x] method `KalshiRepository.listBacktests`
    - [x] `GET /brokerages/$bid/kalshi/backtests`
  - [x] method `KalshiRepository.backtestStatus`
    - [x] `GET /kalshi/backtests/$id/status`
  - [x] method `KalshiRepository.backtestResults`
    - [x] `GET /kalshi/backtests/$id/results`
  - [x] method `KalshiRepository.stopBacktest`
    - [x] `POST /kalshi/backtests/$id/stop`
  - [x] method `KalshiRepository.deleteBacktest`
    - [x] `DELETE /kalshi/backtests/$id`
- [x] top-level `kalshiRepositoryProvider` → native form: `AppServices.kalshiRepository` (AppServices+Repositories.swift)
- [ ] top-level `kalshiPortfolioProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent
- [ ] top-level `kalshiEdgesProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent
- [ ] top-level `kalshiPositionsProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent
- [ ] top-level `kalshiInstancesProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent
- [ ] top-level `kalshiInstanceDetailProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent
- [ ] top-level `kalshiInstanceDecisionsProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent
- [ ] top-level `kalshiInstanceLiveProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent
- [ ] top-level `kalshiInstanceOrdersProvider` — OPEN (handoff): Riverpod view state, owned by the Wave 2 kalshi agent

## `lib/features/learning/data/learning_repository.dart`

- [x] type `LearningOverview`
  - [x] `LearningOverview.mode` ← `(j['mode'] ?? 'observe').toString()`
  - [x] `LearningOverview.actsAutonomously` ← `j['acts_autonomously'] == true`
  - [x] `LearningOverview.enabled` ← `j['enabled'] != false`
  - [x] `LearningOverview.openFindings` ← `(j['open_findings'] as num?)?.toInt() ?? 0`
  - [x] `LearningOverview.runsObserved` ← `(j['runs_observed'] as num?)?.toInt() ?? 0`
  - [x] `LearningOverview.decisionsObserved` ← `(j['decisions_observed'] as num?)?.toInt() ?? 0`
  - [x] `LearningOverview.refusalsObserved` ← `(j['refusals_observed'] as num?)?.toInt() ?? 0`
  - [x] `LearningOverview.engineRunning` ← `j['engine_running'] == true`
- [x] type `LearningFinding`
  - [x] `LearningFinding.id` ← `(j['id'] ?? '').toString()`
  - [x] `LearningFinding.kind` ← `(j['kind'] ?? '').toString()`
  - [x] `LearningFinding.target` ← `(j['target'] ?? '').toString()`
  - [x] `LearningFinding.severity` ← `(j['severity'] ?? 'low').toString()`
  - [x] `LearningFinding.title` ← `(j['title'] ?? '').toString()`
  - [x] `LearningFinding.detail` ← `(j['detail'] ?? '').toString()`
  - [x] `LearningFinding.detectedAt` ← `(j['detected_at'] ?? '').toString()`
  - [x] `LearningFinding.runId` ← `(j['run_id'] ?? '').toString()`
  - [x] `LearningFinding.status` ← `(j['status'] ?? 'open').toString()`
  - [x] `LearningFinding.evidence` ← `(j['evidence'] as Map?)?.cast<String, dynamic>() ?? const {}`
- [x] type `LearningFunnel`
  - [x] getter `LearningFunnel.buyConversionPct`
  - [x] `LearningFunnel.runId` ← `(j['run_id'] ?? '').toString()`
  - [x] `LearningFunnel.target` ← `(j['target'] ?? '').toString()`
  - [x] `LearningFunnel.decided` ← `(j['decided'] as num?)?.toInt() ?? 0`
  - [x] `LearningFunnel.executed` ← `(j['executed'] as num?)?.toInt() ?? 0`
  - [x] `LearningFunnel.refused` ← `(j['refused'] as num?)?.toInt() ?? 0`
  - [x] `LearningFunnel.buyDecided` ← `(j['buy_decided'] as num?)?.toInt() ?? 0`
  - [x] `LearningFunnel.buyExecuted` ← `(j['buy_executed'] as num?)?.toInt() ?? 0`
- [x] type `LearningApproval`
  - [x] `LearningApproval.id` ← `(j['id'] ?? '').toString()`
  - [x] `LearningApproval.rung` ← `(j['rung'] ?? '').toString()`
  - [x] `LearningApproval.actionClass` ← `(j['action_class'] ?? '').toString()`
  - [x] `LearningApproval.target` ← `(j['target'] ?? '').toString()`
  - [x] `LearningApproval.summary` ← `(j['summary'] ?? '').toString()`
  - [x] `LearningApproval.documentId` ← `(j['document_id'] ?? '').toString()`
  - [x] `LearningApproval.requestedAt` ← `(j['requested_at'] ?? '').toString()`
  - [x] `LearningApproval.holdsForever` ← `j['holds_forever'] == true`
- [x] type `LearningFloor`
  - [x] `LearningFloor.target` ← `(j['target'] ?? '').toString()`
  - [x] `LearningFloor.windowClass` ← `(j['window_class'] ?? '').toString()`
  - [x] `LearningFloor.floorPp` ← `(j['floor_pp'] as num?)?.toDouble() ?? 0.0`
  - [x] `LearningFloor.n` ← `(j['n'] as num?)?.toInt() ?? 0`
  - [x] `LearningFloor.measured` ← `j['measured'] == true`
  - [x] `LearningFloor.reason` ← `(j['reason'] ?? '').toString()`
- [x] type `LearningStrategyTarget`
  - [x] getter `LearningStrategyTarget.isLive`
  - [x] `LearningStrategyTarget.id` ← `(j['id'] ?? '').toString()`
  - [x] `LearningStrategyTarget.name` ← `(j['name'] ?? '').toString()`
  - [x] `LearningStrategyTarget.subStrategies` ← `(j['sub_strategies'] as num?)?.toInt() ?? 0`
  - [x] `LearningStrategyTarget.instanceNames` ← `((j['instance_names'] as List?) ?? const [])`
  - [x] `LearningStrategyTarget.money` ← `(j['money'] ?? 'unknown').toString()`
- [x] type `LearningInstanceTarget`
  - [x] getter `LearningInstanceTarget.isLive`
  - [x] `LearningInstanceTarget.id` ← `(j['id'] ?? '').toString()`
  - [x] `LearningInstanceTarget.name` ← `(j['name'] ?? '').toString()`
  - [x] `LearningInstanceTarget.kind` ← `(j['kind'] ?? '').toString()`
  - [x] `LearningInstanceTarget.strategyId` ← `j['strategy_id']?.toString()`
  - [x] `LearningInstanceTarget.running` ← `j['running'] == true`
  - [x] `LearningInstanceTarget.money` ← `(j['money'] ?? 'unknown').toString()`
- [x] type `LearningTargets`
  - [x] `LearningTargets.strategies` ← `((j['strategies'] as List?) ?? const [])`
  - [x] `LearningTargets.instances` ← `((j['instances'] as List?) ?? const [])`
  - [x] `LearningTargets.documentAllowlist` ← `((j['document_allowlist'] as List?) ?? const [])`
  - [x] `LearningTargets.watchedInstances` ← `((j['watched_instances'] as List?) ?? const [])`
  - [x] `LearningTargets.watchingAll` ← `j['watching_all'] == true`
- [x] type `LearningRepository`
  - [x] method `LearningRepository.overview`
    - [x] `GET /learning/overview`
  - [x] method `LearningRepository.findings`
    - [x] `GET /learning/findings`
  - [x] method `LearningRepository.approvals`
    - [x] `GET /learning/approvals`
  - [x] method `LearningRepository.noiseFloors`
    - [x] `GET /learning/noise-floors`
  - [x] method `LearningRepository.targets`
    - [x] `GET /learning/targets`
  - [x] method `LearningRepository.setDocumentAllowlist`
    - [x] `POST /learning/control`
  - [x] method `LearningRepository.setWatchedInstances`
    - [x] `POST /learning/control`
  - [x] method `LearningRepository.control`
    - [x] `GET /learning/control`
  - [x] method `LearningRepository.setRunning`
    - [x] `POST /learning/control`
  - [x] method `LearningRepository.setMode`
    - [x] `POST /learning/control`
  - [x] method `LearningRepository.decide`
    - [x] `POST /learning/approvals/$approvalId`
  - [x] method `LearningRepository.funnels`
    - [x] `GET /learning/funnels`
- [x] top-level `learningRepositoryProvider` → native form: `AppServices.learningRepository` (AppServices+Repositories.swift)

## `lib/features/live_trading/data/live_repository.dart`

- [x] type `CommandResult`
  - [x] `CommandResult.commandId` ← `(json['command_id'] as String?) ?? ''`
  - [x] `CommandResult.status` ← `(json['status'] as String?) ?? 'pending'`
  - [x] `CommandResult.result` ← `json['result'] is Map<String, dynamic>`
  - [x] `CommandResult.error` ← `json['error'] as String?`
  - [x] getter `CommandResult.isTerminal`
- [x] type `LiveRepository`
  - [x] method `LiveRepository.liveState`
    - [x] `GET /instances/$id/live-state`
  - [x] method `LiveRepository.equityHistory`
    - [x] `GET /instances/$id/portfolio-history`
  - [x] method `LiveRepository.symbolHistoricals`
    - [x] `GET /symbol-historicals`
  - [x] method `LiveRepository.holdingOpens`
    - [x] `GET /brokerages/$brokerageId/holding-opens`
  - [x] method `LiveRepository.sendCommand`
    - [x] `POST /instances/$id/live-command`
  - [x] method `LiveRepository.commandStatus`
    - [x] `GET /live-commands/$commandId`
- [x] type `HistPoint`
- [x] top-level `liveRepositoryProvider` → native form: `AppServices.liveRepository` (AppServices+Repositories.swift)

## `lib/features/models/data/model_repository.dart`

- [x] top-level `_asStr` → `llmAsString(_:)`
- [x] type `LlmModel`
  - [x] `LlmModel.id` ← `_asStr(j['id']) ?? ''`
  - [x] `LlmModel.name` ← `_asStr(j['name']) ?? ''`
  - [x] `LlmModel.provider` ← `_asStr(j['provider']) ?? ''`
  - [x] `LlmModel.model` ← `_asStr(j['model']) ?? ''`
  - [x] `LlmModel.reasoningEffort` ← `_asStr(j['reasoning_effort'])`
  - [x] `LlmModel.apiKey` ← `_asStr(j['api_key'])`
  - [x] `LlmModel.cliPath` ← `_asStr(j['cli_path'])`
  - [x] `LlmModel.extraArgs` ← `_asStr(j['extra_args'])`
  - [x] `LlmModel.openaiBaseUrl` ← `_asStr(j['openai_base_url'])`
  - [x] `LlmModel.nvidiaBaseUrl` ← `_asStr(j['nvidia_base_url'])`
  - [x] `LlmModel.azureOpenaiEndpoint` ← `_asStr(j['azure_openai_endpoint'])`
  - [x] `LlmModel.azureOpenaiApiVersion` ← `_asStr(j['azure_openai_api_version'])`
  - [x] `LlmModel.ollamaBaseUrl` ← `_asStr(j['ollama_base_url'])`
  - [x] `LlmModel.ollamaKeepAlive` ← `_asStr(j['ollama_keep_alive'])`
  - [x] `LlmModel.ollamaThink` ← `_asStr(j['ollama_think'])`
  - [x] `LlmModel.bedrockRegion` ← `_asStr(j['bedrock_region'])`
  - [x] `LlmModel.bedrockReasoning` ← `_asStr(j['bedrock_reasoning'])`
  - [x] `LlmModel.openrouterBaseUrl` ← `_asStr(j['openrouter_base_url'])`
  - [x] `LlmModel.openrouterReferer` ← `_asStr(j['openrouter_referer'])`
  - [x] `LlmModel.openrouterTitle` ← `_asStr(j['openrouter_title'])`
  - [x] `LlmModel.modelCacheFamily` ← `_asStr(j['model_cache_family'])`
  - [x] `LlmModel.inputCostPer1m` ← `(j['input_cost_per_1m'] as num?)?.toDouble()`
  - [x] `LlmModel.outputCostPer1m` ← `(j['output_cost_per_1m'] as num?)?.toDouble()`
  - [x] `LlmModel.cacheCreationCostPer1m` ← `(j['cache_creation_cost_per_1m'] as num?)?.toDouble()`
  - [x] `LlmModel.cacheReadCostPer1m` ← `(j['cache_read_cost_per_1m'] as num?)?.toDouble()`
  - [x] `LlmModel.createdAt` ← `_asStr(j['created_at'])`
- [x] type `ClaudeModelOption`
- [x] type `LlmTestResult`
  - [x] `LlmTestResult.provider` ← `_asStr(j['provider'])`
  - [x] `LlmTestResult.model` ← `_asStr(j['model'])`
  - [x] `LlmTestResult.effectiveModel` ← `_asStr(j['effective_model'])`
  - [x] `LlmTestResult.latencyMs` ← `(j['latency_ms'] as num?)?.toInt()`
  - [x] `LlmTestResult.result` ← `j['result']`
  - [x] `LlmTestResult.providerMeta` ← `j['provider_meta']`
  - [x] `LlmTestResult.smokePrompt` ← `_asStr(j['smoke_prompt'])`
  - [x] `LlmTestResult.smokeResponse` ← `_asStr(j['smoke_response'])`
  - [x] `LlmTestResult.smokeThinking` ← `_asStr(j['smoke_thinking'])`
  - [x] `LlmTestResult.smokeContentChars` ← `(j['smoke_content_chars'] as num?)?.toInt()`
  - [x] `LlmTestResult.smokeThinkingChars` ← `(j['smoke_thinking_chars'] as num?)?.toInt()`
  - [x] `LlmTestResult.smokeLatencyMs` ← `(j['smoke_latency_ms'] as num?)?.toInt()`
  - [x] `LlmTestResult.smokeError` ← `_asStr(j['smoke_error'])`
  - [x] `LlmTestResult.message` ← `_asStr(j['message'])`
- [x] type `CodexStatus`
  - [x] `CodexStatus.installed` ← `(j['installed'] as bool?) ?? false`
  - [x] `CodexStatus.version` ← `_asStr(j['version'])`
  - [x] `CodexStatus.authenticated` ← `(j['authenticated'] as bool?) ?? false`
  - [x] `CodexStatus.authMessage` ← `_asStr(j['auth_message'])`
  - [x] `CodexStatus.installMethod` ← `_asStr(j['install_method']) ?? 'unknown'`
- [x] type `ModelRepository`
  - [x] method `ModelRepository.list`
    - [x] `GET /models`
  - [x] method `ModelRepository.create`
    - [x] `POST /models`
  - [x] method `ModelRepository.update`
    - [x] `PUT /models/$id`
  - [x] method `ModelRepository._unwrapModel`
  - [x] method `ModelRepository.delete`
    - [x] `DELETE /models/$id`
  - [x] method `ModelRepository.testCli`
    - [x] `POST /models/$id/test-cli`
  - [x] method `ModelRepository.testLlm`
    - [x] `POST /llm/test`
  - [x] method `ModelRepository.ollamaModels`
    - [x] `POST /ollama/list-models`
  - [x] method `ModelRepository.bedrockModels`
    - [x] `POST /bedrock/list-models`
  - [x] method `ModelRepository.codexStatus`
    - [x] `GET /codex/status`
  - [x] method `ModelRepository.codexInstall`
    - [x] `POST /codex/install`
  - [x] method `ModelRepository.codexInstallJob`
    - [x] `GET /codex/install/$jobId`
  - [x] method `ModelRepository.codexLoginStart`
    - [x] `POST /codex/login/start`
  - [x] method `ModelRepository.codexLoginStatus`
    - [x] `GET /codex/login/$jobId/status`
  - [x] method `ModelRepository.codexLoginCancel`
    - [x] `POST /codex/login/$jobId/cancel`
  - [x] method `ModelRepository.codexLogout`
    - [x] `POST /codex/logout`
  - [x] method `ModelRepository.claudeModels`
    - [x] `GET /claude/models`
  - [x] method `ModelRepository.claudeAuthStatus`
    - [x] `GET /claude/auth/status`
  - [x] method `ModelRepository.claudeLoginStart`
    - [x] `POST /claude/login/start`
  - [x] method `ModelRepository.claudeLoginSubmit`
    - [x] `POST /claude/login/$jobId/submit`
  - [x] method `ModelRepository.claudeLoginStatus`
    - [x] `GET /claude/login/$jobId/status`
  - [x] method `ModelRepository.claudeLoginCancel`
    - [x] `POST /claude/login/$jobId/cancel`
  - [x] method `ModelRepository.claudeLogout`
    - [x] `POST /claude/logout`
- [x] top-level `modelRepositoryProvider` → native form: `AppServices.modelRepository` (AppServices+Repositories.swift)

## `lib/features/nexus/data/nexus_repository.dart`

- [x] type `NexusStage`
  - [x] getter `NexusStage.substepFraction`
  - [x] `NexusStage.key` ← `(j['key'] ?? '').toString()`
  - [x] `NexusStage.label` ← `(j['label'] ?? '').toString()`
  - [x] `NexusStage.status` ← `(j['status'] ?? 'pending').toString()`
  - [x] `NexusStage.message` ← `j['message']?.toString()`
  - [x] `NexusStage.durationSec` ← `(j['duration_sec'] as num?)?.toDouble()`
  - [x] `NexusStage.substepsCompleted` ← `(j['substeps_completed'] as num?)?.toInt()`
  - [x] `NexusStage.totalSubsteps` ← `(j['total_substeps'] as num?)?.toInt()`
  - [x] `NexusStage.stageIndex` ← `(j['stage_index'] as num?)?.toInt() ?? 0`
- [x] type `NexusGraphBuild`
  - [x] `NexusGraphBuild.status` ← `j['status']?.toString()`
  - [x] `NexusGraphBuild.progressPct` ← `(j['progress_pct'] as num?)?.toDouble() ?? 0`
  - [x] `NexusGraphBuild.etaFormatted` ← `j['eta_formatted']?.toString()`
  - [x] `NexusGraphBuild.currentPhaseLabel` ← `j['current_phase_label']?.toString()`
  - [x] `NexusGraphBuild.message` ← `j['message']?.toString()`
  - [x] `NexusGraphBuild.stages` ← `(j['stages'] as List? ?? const [])`
  - [x] `NexusGraphBuild.lastUpdated` ← `j['last_updated'] == null`
- [x] type `NexusRelCount`
  - [x] `NexusRelCount.key` ← `(j['key'] ?? '').toString()`
  - [x] `NexusRelCount.label` ← `(j['label'] ?? j['key'] ?? '').toString()`
  - [x] `NexusRelCount.activeCount` ← `(j['active_count'] as num?)?.toInt()`
  - [x] `NexusRelCount.totalCount` ← `(j['total_count'] as num?)?.toInt()`
- [x] type `NexusGraphSummary`
  - [x] `NexusGraphSummary.relationshipCounts` ← `(j['relationship_counts'] as List? ?? const [])`
- [x] type `NexusPhaseOption`
  - [x] `NexusPhaseOption.value` ← `(j['value'] as num?)?.toInt() ?? 0`
  - [x] `NexusPhaseOption.label` ← `(j['label'] ?? '').toString()`
- [x] type `NexusControl`
- [x] type `NexusBootstrap`
  - [x] `NexusBootstrap.enabled` ← `j['enabled'] == true`
  - [x] `NexusBootstrap.status` ← `(j['status'] ?? 'disabled').toString()`
  - [x] `NexusBootstrap.startDate` ← `j['start_date']?.toString()`
  - [x] `NexusBootstrap.coverageEnd` ← `j['coverage_end']?.toString()`
  - [x] `NexusBootstrap.complete` ← `j['complete'] == true`
  - [x] `NexusBootstrap.lastStatus` ← `j['last_status']?.toString()`
  - [x] `NexusBootstrap.phases` ← `(j['phases'] as List? ?? const [])`
  - [x] `NexusBootstrap.completedPhases` ← `(j['completed_phases'] as num?)?.toInt()`
  - [x] `NexusBootstrap.totalPhases` ← `(j['total_phases'] as num?)?.toInt()`
  - [x] `NexusBootstrap.durationSec` ← `(j['duration_sec'] as num?)?.toDouble()`
  - [x] `NexusBootstrap.completedAt` ← `j['completed_at'] == null`
  - [x] `NexusBootstrap.startedAt` ← `j['started_at'] == null`
- [x] type `NexusScraper`
  - [x] `NexusScraper.status` ← `j['status']?.toString()`
  - [x] `NexusScraper.index` ← `(j['index'] as num?)?.toInt()`
  - [x] `NexusScraper.totalTickers` ← `(j['total_tickers'] as num?)?.toInt()`
  - [x] `NexusScraper.edgesCount` ← `(j['edges_count'] as num?)?.toInt()`
  - [x] `NexusScraper.progressPct` ← `(j['progress_pct'] as num?)?.toDouble() ?? 0`
  - [x] `NexusScraper.etaFormatted` ← `j['eta_formatted']?.toString()`
- [x] type `NexusStatus`
  - [x] getter `NexusStatus.serviceRunning`
  - [x] getter `NexusStatus.isBuilding`
  - [x] getter `NexusStatus.showBuilt`
  - [x] `NexusStatus.control` ← `NexusControl.fromJson(_asMap(j['control']))`
  - [x] `NexusStatus.graphBuild` ← `j['graph_build'] == null`
  - [x] `NexusStatus.graphSummary` ← `j['graph_summary'] == null`
  - [x] `NexusStatus.scraper` ← `j['scraper'] == null`
  - [x] `NexusStatus.bootstrap` ← `j['bootstrap'] == null`
  - [x] `NexusStatus.graphBuilt` ← `j['graph_built'] == true`
- [x] top-level `_asMap` → local `asMap` in `NexusStatus.init(json:)`
- [x] type `NexusCacheEntry`
  - [x] `NexusCacheEntry.path` ← `(j['path'] ?? '').toString()`
  - [x] `NexusCacheEntry.isDir` ← `j['is_dir'] == true`
  - [x] `NexusCacheEntry.sizeBytes` ← `(j['size_bytes'] as num?)?.toInt()`
- [x] type `NexusCacheInfo`
  - [x] `NexusCacheInfo.available` ← `j['available'] == true`
  - [x] `NexusCacheInfo.cacheRoot` ← `(j['cache_root'] ?? '/app/.cache').toString()`
  - [x] `NexusCacheInfo.entries` ← `(j['entries'] as List? ?? const [])`
  - [x] `NexusCacheInfo.error` ← `j['error']?.toString()`
- [x] top-level `kFallbackPhaseOptions`
- [x] top-level `_kDeletePhaseValues`
- [x] type `NexusRepository`
  - [x] method `NexusRepository.status`
    - [x] `GET /nexus/status`
  - [x] method `NexusRepository.control`
    - [x] `POST /nexus/control`
  - [x] method `NexusRepository.rebuild`
    - [x] `POST /nexus/rebuild`
  - [x] method `NexusRepository.deleteEdges`
    - [x] `POST /nexus/delete-edges`
  - [x] method `NexusRepository.cache`
    - [x] `GET /nexus/cache`
- [x] top-level `nexusRepositoryProvider` → native form: `AppServices.nexusRepository` (AppServices+Repositories.swift)

## `lib/features/onboarding/data/onboarding_repository.dart`

- [x] type `OnboardingRepository`
  - [x] method `OnboardingRepository.state`
    - [x] `GET /onboarding/state`
  - [x] method `OnboardingRepository.complete`
    - [x] `POST /onboarding/complete`
  - [x] method `OnboardingRepository.reset`
    - [x] `POST /onboarding/reset`
- [x] top-level `onboardingRepositoryProvider` → native form: `AppServices.onboardingRepository` (AppServices+Repositories.swift)

## `lib/features/settings/data/notification_prefs_repository.dart`

- [x] type `NotificationPrefsRepository`
  - [x] method `NotificationPrefsRepository.get`
    - [x] `GET /notification-preferences`
  - [x] method `NotificationPrefsRepository.save`
    - [x] `PUT /notification-preferences`
  - [x] method `NotificationPrefsRepository.sendTest`
    - [x] `POST /notifications/test`
- [x] top-level `notificationPrefsRepositoryProvider` → native form: `AppServices.notificationPrefsRepository` (AppServices+Repositories.swift)

## `lib/features/strategies/data/strategy_repository.dart`

- [x] type `StrategyRepository`
  - [x] method `StrategyRepository.list`
    - [x] `GET /strategies`
  - [x] method `StrategyRepository.get`
    - [x] `GET /strategies/$id`
  - [x] method `StrategyRepository.update`
    - [x] `PUT /strategies/$id`
  - [x] method `StrategyRepository.previewConfigChange`
    - [x] `POST /strategies/$id/config-change-preview`
  - [x] method `StrategyRepository.available`
    - [x] `GET /strategies/available`
  - [x] method `StrategyRepository.agentResults`
    - [x] `GET /agent/results`
  - [x] method `StrategyRepository.top5`
    - [x] `GET /agent/top5`
  - [x] method `StrategyRepository.agentBest`
    - [x] `GET /agent/best`
  - [x] method `StrategyRepository.bestPerStrategy`
    - [x] `GET /backtests/best-per-strategy`
  - [x] method `StrategyRepository.instances`
    - [x] `GET /instances`
  - [x] method `StrategyRepository.createInstance`
    - [x] `POST /instances`
  - [x] method `StrategyRepository.linkStrategy`
    - [x] `POST /instances/${Uri.encodeComponent(instanceId)}/link-strategy`
  - [x] method `StrategyRepository.createBacktest`
    - [x] `POST /backtests`
  - [x] method `StrategyRepository.computeBestByStrategy`
  - [x] method `StrategyRepository.mergeStrategyRows`
  - [x] method `StrategyRepository._toDouble`
- [x] top-level `strategyRepositoryProvider` → native form: `AppServices.strategyRepository` (AppServices+Repositories.swift)

## `lib/features/swing/data/swing_repository.dart`

- [x] top-level `_num`
- [x] top-level `_int`
- [x] top-level `_str`
- [x] type `SwingSignal`
  - [x] getter `SwingSignal.isWheel`
  - [x] getter `SwingSignal.allowsHalf`
  - [x] getter `SwingSignal.keyRisksText`
  - [x] getter `SwingSignal.entry`
  - [x] getter `SwingSignal.stop`
  - [x] getter `SwingSignal.target`
  - [x] getter `SwingSignal.shares`
  - [x] getter `SwingSignal.contract`
  - [x] getter `SwingSignal.strike`
  - [x] getter `SwingSignal.expiry`
  - [x] getter `SwingSignal.qty`
  - [x] getter `SwingSignal.limitPrice`
  - [x] getter `SwingSignal.premiumEst`
  - [x] getter `SwingSignal.creditEst`
  - [x] getter `SwingSignal.collateral`
  - [x] `SwingSignal.id` ← `_str(j['id'])`
  - [x] `SwingSignal.lane` ← `_str(j['lane']).isEmpty ? 'swing' : _str(j['lane'])`
  - [x] `SwingSignal.symbol` ← `_str(j['symbol'])`
  - [x] `SwingSignal.session` ← `_str(j['session'])`
  - [x] `SwingSignal.createdAt` ← `_str(j['created_at'])`
  - [x] `SwingSignal.score` ← `_int(j['score'])`
  - [x] `SwingSignal.recommendation` ← `_str(j['recommendation'])`
  - [x] `SwingSignal.reasoning` ← `_str(j['reasoning'])`
  - [x] `SwingSignal.keyRisks` ← `((j['key_risks'] as List?) ?? const [])`
  - [x] `SwingSignal.sizeAdjustment` ← `_num(j['size_adjustment'])`
  - [x] `SwingSignal.proposal` ← `(j['proposal'] as Map?)?.cast<String, dynamic>() ?? const {}`
  - [x] `SwingSignal.status` ← `_str(j['status']).isEmpty ? 'pending' : _str(j['status'])`
  - [x] `SwingSignal.decidedAt` ← `DateTime.tryParse(_str(j['decided_at']))?.toUtc()`
- [x] type `WheelPut`
  - [x] getter `WheelPut.monitorWillBuyBack`
  - [x] `WheelPut.contract` ← `_str(j['contract'])`
  - [x] `WheelPut.underlying` ← `_str(j['underlying'])`
  - [x] `WheelPut.strike` ← `_num(j['strike'])`
  - [x] `WheelPut.expiry` ← `_str(j['expiry'])`
  - [x] `WheelPut.qty` ← `_int(j['qty'])`
  - [x] `WheelPut.avgEntryPrice` ← `_num(j['avg_entry_price'])`
  - [x] `WheelPut.currentPrice` ← `_num(j['current_price'])`
  - [x] `WheelPut.underlyingPrice` ← `_num(j['underlying_price'])`
  - [x] `WheelPut.itmPct` ← `_num(j['itm_pct'])`
  - [x] `WheelPut.dte` ← `_int(j['dte'])`
  - [x] `WheelPut.collateral` ← `_num(j['collateral'])`
  - [x] `WheelPut.unrealizedPl` ← `_num(j['unrealized_pl'])`
- [x] type `WheelScan`
  - [x] `WheelScan.id` ← `_str(j['id'])`
  - [x] `WheelScan.session` ← `_str(j['session'])`
  - [x] `WheelScan.symbol` ← `_str(j['symbol'])`
  - [x] `WheelScan.strike` ← `_num(j['strike'])`
  - [x] `WheelScan.expiry` ← `_str(j['expiry'])`
  - [x] `WheelScan.score` ← `_int(j['score'])`
  - [x] `WheelScan.status` ← `_str(j['status'])`
  - [x] `WheelScan.skipReason` ← `_str(j['skip_reason'])`
- [x] type `WheelSnapshot`
  - [x] `WheelSnapshot.openPuts` ← `((j['open_puts'] as List?) ?? const [])`
  - [x] `WheelSnapshot.collateralTotal` ← `_num(j['collateral_total'])`
  - [x] `WheelSnapshot.cash` ← `_num(j['cash'])`
  - [x] `WheelSnapshot.recentScans` ← `((j['recent_scans'] as List?) ?? const [])`
  - [x] `WheelSnapshot.fetchedAt` ← `fetchedAt`
- [x] type `DecisionReceipt`
  - [x] `DecisionReceipt.uncertain` ← `data['uncertain'] == true`
  - [x] `DecisionReceipt.detail` ← `_str(data['detail']).trim()`
- [x] type `SwingRepository`
  - [x] method `SwingRepository._signals`
    - [x] `GET /instances/$instanceId/swing/signals`
  - [x] method `SwingRepository.pendingSignals`
  - [x] method `SwingRepository.signalsWithStatus`
  - [x] method `SwingRepository.approvedSignals`
  - [x] method `SwingRepository.resend`
    - [x] `POST /instances/$instanceId/swing/signals/$signalId/resend`
  - [x] method `SwingRepository.decide`
    - [x] `POST /instances/$instanceId/swing/signals/$signalId/decision`
  - [x] method `SwingRepository.wheel`
    - [x] `GET /instances/$instanceId/wheel`
- [x] top-level `swingRepositoryProvider` → native form: `AppServices.swingRepository` (AppServices+Repositories.swift)

## `lib/features/symbol_search/data/symbol_search_models.dart`

- [x] type `SearchInstrument`
  - [x] `SearchInstrument.symbol` ← `(json['symbol'] ?? '').toString()`
  - [x] `SearchInstrument.name` ← `(json['name'] ?? '').toString()`
  - [x] `SearchInstrument.type` ← `(json['type'] ?? '').toString()`
  - [x] method `SearchInstrument.matches`
- [x] type `SearchQuote`
- [x] top-level `searchQuoteFromHistory`
- [x] top-level `searchSymbolsForSparklines`

## `lib/features/symbol_search/data/symbol_search_repository.dart`

- [x] type `SymbolSearchRepository`
  - [x] method `SymbolSearchRepository.search`
    - [x] `GET /symbols/search`
- [x] top-level `symbolSearchRepositoryProvider` → native form: `AppServices.symbolSearchRepository` (AppServices+Repositories.swift)

## `lib/features/token_usage/data/token_usage_repository.dart`

- [x] type `TelemetryHealth`
  - [x] `TelemetryHealth.bufferDepth` ← `(j['buffer_depth'] as num?)?.toInt()`
  - [x] `TelemetryHealth.lastFlushAgeS` ← `j['last_flush_age_s'] as num?`
  - [x] `TelemetryHealth.writeErrors24h` ← `(j['write_errors_24h'] as num?)?.toInt() ?? 0`
- [x] type `ProviderBreakdown`
  - [x] `ProviderBreakdown.provider` ← `(j['provider'] as String?) ?? ''`
  - [x] `ProviderBreakdown.costUsd` ← `(j['cost_usd'] as num?)?.toDouble()`
  - [x] `ProviderBreakdown.tokens` ← `(j['tokens'] as num?)?.toInt()`
  - [x] `ProviderBreakdown.calls` ← `(j['calls'] as num?)?.toInt()`
- [x] type `UsageSummary`
  - [x] `UsageSummary.totalCostUsd` ← `(j['total_cost_usd'] as num?)?.toDouble()`
  - [x] `UsageSummary.totalCalls` ← `(j['total_calls'] as num?)?.toInt()`
  - [x] `UsageSummary.totalTokens` ← `(j['total_tokens'] as num?)?.toInt()`
  - [x] `UsageSummary.maxPlanEstimateUsd` ← `(j['max_plan_estimate_usd'] as num?)?.toDouble()`
  - [x] `UsageSummary.byProvider` ← `(j['by_provider'] as List?)`
  - [x] `UsageSummary.telemetryHealth` ← `j['telemetry_health'] is Map<String, dynamic>`
- [x] type `TimeseriesRow`
  - [x] `TimeseriesRow.provider` ← `(j['provider'] as String?) ?? 'unknown'`
  - [x] `TimeseriesRow.bucketStartTs` ← `(j['bucket_start_ts'] as num?)?.toInt() ?? 0`
  - [x] `TimeseriesRow.costUsd` ← `(j['cost_usd'] as num?)?.toDouble()`
  - [x] `TimeseriesRow.tokens` ← `(j['tokens'] as num?)?.toInt()`
  - [x] `TimeseriesRow.calls` ← `(j['calls'] as num?)?.toInt()`
- [x] type `SpenderRow`
  - [x] `SpenderRow.key` ← `(j['key'] as String?) ?? ''`
  - [x] `SpenderRow.calls` ← `(j['calls'] as num?)?.toInt()`
  - [x] `SpenderRow.tokens` ← `(j['tokens'] as num?)?.toInt()`
  - [x] `SpenderRow.costUsd` ← `(j['cost_usd'] as num?)?.toDouble()`
- [x] type `BacktestUsageRow`
  - [x] `BacktestUsageRow.backtestId` ← `j['backtest_id'] as String?`
  - [x] `BacktestUsageRow.displayLabel` ← `j['display_label'] as String?`
  - [x] `BacktestUsageRow.kind` ← `j['kind'] as String?`
  - [x] `BacktestUsageRow.instanceId` ← `j['instance_id'] as String?`
  - [x] `BacktestUsageRow.firstTs` ← `(j['first_ts'] as num?)?.toInt()`
  - [x] `BacktestUsageRow.calls` ← `(j['calls'] as num?)?.toInt()`
  - [x] `BacktestUsageRow.tokens` ← `(j['tokens'] as num?)?.toInt()`
  - [x] `BacktestUsageRow.costUsd` ← `(j['cost_usd'] as num?)?.toDouble()`
  - [x] `BacktestUsageRow.okCalls` ← `(j['ok_calls'] as num?)?.toInt()`
  - [x] `BacktestUsageRow.failedCalls` ← `(j['failed_calls'] as num?)?.toInt()`
- [x] type `RecentCall`
  - [x] `RecentCall.id` ← `j['id'] as String?`
  - [x] `RecentCall.ts` ← `(j['ts'] as num?)?.toInt()`
  - [x] `RecentCall.provider` ← `j['provider'] as String?`
  - [x] `RecentCall.model` ← `j['model'] as String?`
  - [x] `RecentCall.inputTokens` ← `(j['input_tokens'] as num?)?.toInt()`
  - [x] `RecentCall.outputTokens` ← `(j['output_tokens'] as num?)?.toInt()`
  - [x] `RecentCall.totalCostUsd` ← `(j['total_cost_usd'] as num?)?.toDouble()`
  - [x] `RecentCall.strategy` ← `j['strategy'] as String?`
  - [x] `RecentCall.callSite` ← `j['call_site'] as String?`
  - [x] `RecentCall.raw` ← `j`
- [x] type `TokenUsageData`
- [x] type `TokenUsageRepository`
  - [x] method `TokenUsageRepository.summary`
    - [x] `GET /llm-usage/summary`
  - [x] method `TokenUsageRepository.timeseries`
    - [x] `GET /llm-usage/timeseries`
  - [x] method `TokenUsageRepository.topSpenders`
    - [x] `GET /llm-usage/top-spenders`
  - [x] method `TokenUsageRepository.byBacktest`
    - [x] `GET /llm-usage/by-backtest`
  - [x] method `TokenUsageRepository.calls`
    - [x] `GET /llm-usage/calls`
  - [x] method `TokenUsageRepository.fetchAll`
- [x] top-level `tokenUsageRepositoryProvider` → native form: `AppServices.tokenUsageRepository` (AppServices+Repositories.swift)

## `lib/features/backtests/data/models/backtest.dart`

- [x] type `BacktestRow`
  - [x] `BacktestRow.id` ← `j['id']?.toString() ?? ''`
  - [x] `BacktestRow.status` ← `j['status']?.toString()`
  - [x] `BacktestRow.stocks` ← `_strList(j['stocks'] ?? j['tickers'])`
  - [x] `BacktestRow.startDate` ← `j['start_date']?.toString()`
  - [x] `BacktestRow.endDate` ← `j['end_date']?.toString()`
  - [x] `BacktestRow.completedAt` ← `j['completed_at']`
  - [x] `BacktestRow.pnl` ← `_num(j['pnl'])`
  - [x] `BacktestRow.pnlPercent` ← `_num(j['pnl_percent'])`
  - [x] `BacktestRow.timeElapsedSeconds` ← `_num(j['time_elapsed_seconds'])`
- [x] type `BacktestStatus`
  - [x] `BacktestStatus.status` ← `j['status']?.toString()`
  - [x] `BacktestStatus.progress` ← `_num(j['progress'])`
  - [x] `BacktestStatus.nexusLookback` ← `j['nexus_lookback'] is Map`
  - [x] `BacktestStatus.timeElapsedSeconds` ← `_num(j['time_elapsed_seconds'])`
- [x] type `NexusLookback`
  - [x] `NexusLookback.current` ← `(j['current'] as num?)?.toInt() ?? 0`
  - [x] `NexusLookback.total` ← `(j['total'] as num?)?.toInt() ?? 0`
  - [x] `NexusLookback.currentDate` ← `j['current_date']?.toString()`
  - [x] `NexusLookback.startDate` ← `j['start_date']?.toString()`
  - [x] `NexusLookback.endDate` ← `j['end_date']?.toString()`
  - [x] getter `NexusLookback.fraction`
- [x] type `BacktestSummary`
  - [x] `BacktestSummary.id` ← `j['id']?.toString()`
  - [x] `BacktestSummary.status` ← `j['status']?.toString()`
  - [x] `BacktestSummary.pnl` ← `_num(j['pnl'])`
  - [x] `BacktestSummary.pnlPercent` ← `_num(j['pnl_percent'])`
  - [x] `BacktestSummary.fees` ← `_numMap(j['fees'])`
  - [x] `BacktestSummary.feeEmulated` ← `(j['fees'] is Map)`
  - [x] `BacktestSummary.feeVenue` ← `(j['fees'] is Map)`
  - [x] `BacktestSummary.emulateFeeVenue` ← `j['emulate_fee_venue']?.toString()`
  - [x] `BacktestSummary.portfolioStartValue` ← `_num(j['portfolio_start_value'])`
  - [x] `BacktestSummary.portfolioEndValue` ← `_num(j['portfolio_end_value'])`
  - [x] `BacktestSummary.totalTrades` ← `_num(j['total_trades'])`
  - [x] `BacktestSummary.totalBuys` ← `_num(j['total_buys'])`
  - [x] `BacktestSummary.totalSells` ← `_num(j['total_sells'])`
  - [x] `BacktestSummary.timeElapsedSeconds` ← `_num(j['time_elapsed_seconds'])`
  - [x] `BacktestSummary.winRatePercent` ← `_num(j['win_rate_percent'])`
  - [x] `BacktestSummary.winningRoundTrips` ← `_num(j['winning_round_trips'])`
  - [x] `BacktestSummary.losingRoundTrips` ← `_num(j['losing_round_trips'])`
  - [x] `BacktestSummary.portfolioValueHigh` ← `_num(j['portfolio_value_high'])`
  - [x] `BacktestSummary.portfolioValueLow` ← `_num(j['portfolio_value_low'])`
  - [x] `BacktestSummary.roundTrips` ← `_num(j['round_trips'])`
  - [x] `BacktestSummary.pnlPerStock` ← `_numMap(j['pnl_per_stock'])`
  - [x] `BacktestSummary.pnlPercentPerStock` ← `_numMap(j['pnl_percent_per_stock'])`
  - [x] `BacktestSummary.stockPriceChange` ← `_changePctMap(j['stock_price_change'])`
  - [x] `BacktestSummary.tickers` ← `_strList(j['tickers'])`
  - [x] `BacktestSummary.startDate` ← `j['start_date']?.toString()`
  - [x] `BacktestSummary.endDate` ← `j['end_date']?.toString()`
  - [x] `BacktestSummary.strategySchema` ← `j['strategy_schema'] is Map`
  - [x] `BacktestSummary.strategyId` ← `j['strategy_id']?.toString()`
  - [x] `BacktestSummary.instanceId` ← `(j['instance_id'] ?? j['instance'])?.toString()`
  - [x] `BacktestSummary.granularity` ← `j['granularity']?.toString()`
  - [x] `BacktestSummary.initialCash` ← `_num(j['initial_cash'])`
  - [x] `BacktestSummary.pauseReasonTag` ← `j['pause_reason_tag']?.toString()`
  - [x] `BacktestSummary.pauseProvider` ← `j['pause_provider']?.toString()`
  - [x] `BacktestSummary.pauseModel` ← `j['pause_model']?.toString()`
  - [x] `BacktestSummary.pauseCallSite` ← `j['pause_call_site']?.toString()`
  - [x] `BacktestSummary.pauseAttempts` ← `_num(j['pause_attempts'])`
  - [x] `BacktestSummary.pauseBarTime` ← `j['pause_bar_time']?.toString()`
  - [x] `BacktestSummary.pausedAt` ← `j['paused_at']`
  - [x] `BacktestSummary.pauseSample` ← `j['pause_sample']?.toString()`
  - [x] `BacktestSummary.totalRoundTripPnl` ← `_num(j['total_round_trip_pnl'])`
  - [x] `BacktestSummary.avgWinningRoundTrip` ← `_num(j['avg_winning_round_trip'])`
  - [x] `BacktestSummary.avgLosingRoundTrip` ← `_num(j['avg_losing_round_trip'])`
- [x] type `StrategySchema`
  - [x] `StrategySchema.name` ← `j['name']?.toString()`
  - [x] `StrategySchema.strategies` ← `(j['strategies'] as List? ?? [])`
- [x] type `SubStrategy` → renamed `BacktestSubStrategy` (module-unique name)
  - [x] `SubStrategy.strategy` ← `j['strategy']?.toString()`
  - [x] `SubStrategy.weight` ← `_num(j['weight'])`
  - [x] `SubStrategy.executionPosition` ← `_num(j['execution_position'])`
  - [x] `SubStrategy.decisionPhase` ← `j['decision_phase']?.toString()`
  - [x] `SubStrategy.executionScope` ← `j['execution_scope']?.toString()`
  - [x] `SubStrategy.conditions` ← `j['conditions'] is Map`
  - [x] `SubStrategy.config` ← `j['config'] is Map`
- [x] type `LlmCost`
  - [x] `LlmCost.totalCostUsd` ← `_num(j['total_cost_usd'])`
  - [x] `LlmCost.totalCalls` ← `_num(j['total_calls'])`
  - [x] `LlmCost.okCalls` ← `_num(j['ok_calls'])`
  - [x] `LlmCost.failedCalls` ← `_num(j['failed_calls'])`
  - [x] `LlmCost.totalInputTokens` ← `_num(j['total_input_tokens'])`
  - [x] `LlmCost.totalOutputTokens` ← `_num(j['total_output_tokens'])`
  - [x] `LlmCost.totalReasoningTokens` ← `_num(j['total_reasoning_tokens'])`
  - [x] `LlmCost.byModel` ← `_costRows(j['by_model'])`
  - [x] `LlmCost.byCallSite` ← `_costRows(j['by_call_site'])`
  - [x] `LlmCost.byProvider` ← `_costRows(j['by_provider'])`
- [x] type `LlmCostRow`
  - [x] `LlmCostRow.key` ← `j['key']?.toString() ?? '?'`
  - [x] `LlmCostRow.costUsd` ← `_num(j['cost_usd'])`
- [x] type `PortfolioValuePoint`
- [x] type `BacktestTrade`
  - [x] `BacktestTrade.ticker` ← `(j['ticker'] ?? j['symbol'] ?? '').toString()`
  - [x] `BacktestTrade.action` ← `j['action']?.toString()`
  - [x] `BacktestTrade.timestamp` ← `DateTime.tryParse(j['timestamp']?.toString() ?? '')`
  - [x] `BacktestTrade.price` ← `_num(j['price'])`
  - [x] `BacktestTrade.shares` ← `_num(j['shares'])`
  - [x] `BacktestTrade.total` ← `_num(j['total'])`
  - [x] `BacktestTrade.cashAfter` ← `_num(j['cash_after'])`
- [x] type `BacktestDecision`
  - [x] `BacktestDecision.symbol` ← `j['symbol']?.toString()`
  - [x] `BacktestDecision.timestamp` ← `DateTime.tryParse(j['timestamp']?.toString() ?? '')`
  - [x] `BacktestDecision.decision` ← `j['decision']`
  - [x] `BacktestDecision.action` ← `j['action']?.toString()`
  - [x] `BacktestDecision.normalizedScore` ← `_num(j['normalized_score'])`
  - [x] `BacktestDecision.finalReason` ← `j['final_reason']?.toString()`
  - [x] `BacktestDecision.overrideApplied` ← `j['override_applied'] as bool?`
  - [x] `BacktestDecision.preOverrideAction` ← `j['pre_override_action']?.toString()`
  - [x] `BacktestDecision.preOverrideDecision` ← `j['pre_override_decision']`
  - [x] `BacktestDecision.primaryStrategy` ← `j['primary_strategy']?.toString()`
  - [x] `BacktestDecision.primaryActionIntent` ← `j['primary_action_intent']?.toString()`
  - [x] `BacktestDecision.strategies` ← `(j['strategies'] as List? ?? [])`
  - [x] `BacktestDecision.postDecision` ← `(j['post_decision'] as List? ?? [])`
  - [x] `BacktestDecision.rawJson` ← `j`
  - [x] method `BacktestDecision.decisionLabel`
- [x] type `DecisionStrategy`
  - [x] `DecisionStrategy.strategy` ← `j['strategy']?.toString()`
  - [x] `DecisionStrategy.decision` ← `j['decision']`
  - [x] `DecisionStrategy.weight` ← `_num(j['weight'])`
  - [x] `DecisionStrategy.actionIntent` ← `j['action_intent']?.toString()`
  - [x] `DecisionStrategy.reason` ← `j['reason']?.toString()`
  - [x] method `DecisionStrategy.decisionLabel`
- [x] type `PostDecision`
  - [x] `PostDecision.strategy` ← `j['strategy']?.toString()`
  - [x] `PostDecision.decision` ← `j['decision']`
  - [x] `PostDecision.reason` ← `j['reason']?.toString()`
- [x] type `BacktestPrice`
  - [x] `BacktestPrice.symbol` ← `(j['symbol'] ?? '').toString()`
  - [x] `BacktestPrice.timestamp` ← `DateTime.tryParse(j['timestamp']?.toString() ?? '') ??`
  - [x] `BacktestPrice.close` ← `(j['close'] as num?)?.toDouble() ?? 0`
- [x] type `BacktestGraphData`
  - [x] `BacktestGraphData.portfolioValueHistory` ← `_mapList(`
- [x] type `PlaybackMetadata`
  - [x] `PlaybackMetadata.initialCash` ← `_num(j['initial_cash'])`
  - [x] `PlaybackMetadata.extra` ← `j`
- [x] type `PlaybackEvent`
  - [x] `PlaybackEvent.id` ← `'${j['type']}_$index'`
  - [x] `PlaybackEvent.type` ← `j['type']?.toString() ?? 'unknown'`
  - [x] `PlaybackEvent.label` ← `j['label']?.toString()`
  - [x] `PlaybackEvent.time` ← `j['time']?.toString()`
  - [x] `PlaybackEvent.name` ← `j['name']?.toString()`
  - [x] `PlaybackEvent.desc` ← `j['desc']?.toString()`
  - [x] `PlaybackEvent.reason` ← `j['reason']?.toString()`
  - [x] `PlaybackEvent.decision` ← `j['decision']?.toString()`
  - [x] `PlaybackEvent.tickers` ← `_strList(j['tickers'])`
  - [x] `PlaybackEvent.details` ← `j['details']?.toString()`
  - [x] `PlaybackEvent.buys` ← `_tradeList(j['buys'])`
  - [x] `PlaybackEvent.sells` ← `_tradeList(j['sells'])`
  - [x] `PlaybackEvent.portfolioValue` ← `_num(j['value'])`
  - [x] `PlaybackEvent.holdings` ← `(j['holdings'] as List? ?? [])`
  - [x] `PlaybackEvent.date` ← `j['date']?.toString()`
  - [x] `PlaybackEvent.raw` ← `j`
- [x] type `PlaybackTrade`
  - [x] `PlaybackTrade.ticker` ← `j['ticker']?.toString()`
  - [x] `PlaybackTrade.qty` ← `_num(j['qty'])`
  - [x] `PlaybackTrade.price` ← `_num(j['price'])`
  - [x] `PlaybackTrade.reason` ← `j['reason']?.toString()`
- [x] type `PlaybackHolding`
  - [x] `PlaybackHolding.ticker` ← `(j['ticker'] ?? j['symbol'] ?? '').toString()`
  - [x] `PlaybackHolding.qty` ← `_num(j['qty'])`
  - [x] `PlaybackHolding.avg` ← `_num(j['avg'])`
  - [x] `PlaybackHolding.curr` ← `_num(j['curr'])`
- [x] type `PlaybackData`
  - [x] `PlaybackData.events` ← `(j['events'] as List? ?? [])`
  - [x] `PlaybackData.metadata` ← `j['metadata'] is Map`
- [x] type `BacktestListResponse`
  - [x] `BacktestListResponse.backtests` ← `(j['backtests'] as List? ?? [])`
  - [x] `BacktestListResponse.total` ← `(j['total'] as num?)?.toInt() ?? 0`
  - [x] `BacktestListResponse.totalPages` ← `(j['total_pages'] as num?)?.toInt() ?? 1`
  - [x] `BacktestListResponse.page` ← `(j['page'] as num?)?.toInt() ?? 1`
- [x] top-level `_num`
- [x] top-level `_strList`
- [x] top-level `_numMap`
- [x] top-level `_changePctMap`

## `lib/features/brokerages/data/models/brokerage.dart`

- [x] type `Brokerage`
  - [x] method `Brokerage.toJson`

## `lib/features/chatbot/data/models/chat.dart`

- [x] type `ToolCall`
  - [x] `ToolCall.id` ← `(j['id'] ?? j['tool_call_id'] ?? '').toString()`
  - [x] `ToolCall.name` ← `(j['name'] ?? j['function'] ?? '').toString()`
  - [x] `ToolCall.arguments` ← `_asMap(j['arguments'] ?? j['input'] ?? {})`
  - [x] `ToolCall.description` ← `j['description']?.toString()`
  - [x] `ToolCall.safety` ← `(j['safety'] ?? 'write').toString()`
- [x] type `ChatMessage`
  - [x] getter `ChatMessage.isPendingConfirmation`
- [x] type `Conversation`
  - [x] method `Conversation.copyWith`
- [x] type `ChatModel`
  - [x] `ChatModel.id` ← `(j['id'] ?? '').toString()`
  - [x] `ChatModel.name` ← `(j['name'] ?? j['id'] ?? '').toString()`
  - [x] `ChatModel.provider` ← `(j['provider'] ?? '').toString()`
  - [x] `ChatModel.model` ← `(j['model'] ?? '').toString()`
- [x] top-level `_parseDate`
- [x] top-level `_asMap` → local `asMap` in `NexusStatus.init(json:)`

## `lib/features/instances/data/models/instance.dart`

- [x] type `Instance`
  - [x] method `Instance.copyWith` → native form: `var` properties; copy and assign (nil clears, as the sentinel did)
- [x] top-level `_sentinel`
- [x] type `BacktestRow` → renamed `InstanceBacktestRow` (module-unique name)
  - [x] method `BacktestRow.copyWith`

## `lib/features/live_trading/data/models/live_state.dart`

- [x] type `LiveState`
  - [x] `LiveState.status` ← `(json['status'] as String?) ?? 'unknown'`
  - [x] `LiveState.equity` ← `(json['equity'] as num?)?.toDouble() ?? 0`
  - [x] `LiveState.cash` ← `(json['cash'] as num?)?.toDouble() ?? 0`
  - [x] `LiveState.buyingPower` ← `(json['buying_power'] as num?)?.toDouble() ??`
  - [x] `LiveState.totalPnl` ← `(json['total_pnl'] as num?)?.toDouble() ?? 0`
  - [x] `LiveState.totalPnlPct` ← `(json['total_pnl_pct'] as num?)?.toDouble() ?? 0`
  - [x] `LiveState.dayPnl` ← `(json['day_pnl'] as num?)?.toDouble() ?? 0`
  - [x] `LiveState.dayPnlPct` ← `(json['day_pnl_pct'] as num?)?.toDouble() ?? 0`
  - [x] `LiveState.uptimeSec` ← `(json['uptime_sec'] as num?)?.toDouble() ?? 0`
  - [x] `LiveState.tradingActive` ← `json['trading_active'] == true`
  - [x] `LiveState.brokerFetchError` ← `json['broker_fetch_error'] as String?`
  - [x] `LiveState.containerStale` ← `json['container_stale'] == true`
  - [x] `LiveState.lookback` ← `json['lookback'] is Map<String, dynamic>`
  - [x] `LiveState.positions` ← `(json['positions'] as List?)`
  - [x] `LiveState.recentTrades` ← `(json['recent_trades'] as List?)`
- [x] type `Position`
  - [x] getter `Position.isOption`
  - [x] getter `Position.isShort`
  - [x] getter `Position.contractMultiplier`
  - [x] getter `Position.quantityLabel`
  - [x] getter `Position.quantityText`
  - [x] getter `Position.canClose`
  - [x] getter `Position.optionDescription`
  - [x] method `Position.describeOptionContract`
  - [x] `Position.symbol` ← `(json['symbol'] as String?) ?? ''`
  - [x] `Position.qty` ← `(json['qty'] as num?)?.toDouble() ?? 0`
  - [x] `Position.marketValue` ← `(json['market_value'] as num?)?.toDouble()`
  - [x] `Position.lastPrice` ← `(json['last_price'] as num?)?.toDouble()`
  - [x] `Position.avgEntryPrice` ← `(json['avg_entry_price'] as num?)?.toDouble()`
  - [x] `Position.unrealizedPnl` ← `(json['unrealized_pnl'] as num?)?.toDouble()`
  - [x] `Position.unrealizedPnlPct` ← `(json['unrealized_pnl_pct'] as num?)?.toDouble()`
  - [x] `Position.assetClass` ← `json['asset_class'] as String?`
  - [x] `Position.side` ← `json['side'] as String?`
  - [x] `Position.multiplier` ← `(json['multiplier'] as num?)?.toInt()`
  - [x] `Position.underlying` ← `json['underlying'] as String?`
  - [x] `Position.strike` ← `(json['strike'] as num?)?.toDouble()`
  - [x] `Position.expiry` ← `json['expiry'] as String?`
- [x] type `Trade`
  - [x] getter `Trade.isOption`
  - [x] getter `Trade.quantityLabel`
  - [x] getter `Trade.quantityText`
  - [x] getter `Trade.total`
  - [x] `Trade.side` ← `(json['side'] as String?) ?? ''`
  - [x] `Trade.symbol` ← `(json['symbol'] as String?) ?? ''`
  - [x] `Trade.price` ← `(json['price'] as num?)?.toDouble() ?? 0`
  - [x] `Trade.qty` ← `(json['qty'] as num?)?.toDouble() ?? 0`
  - [x] `Trade.ts` ← `json['ts']`
  - [x] `Trade.orderId` ← `json['order_id'] as String?`
  - [x] `Trade.assetClass` ← `json['asset_class'] as String?`
- [x] type `Lookback`
  - [x] getter `Lookback.pct`
  - [x] `Lookback.specName` ← `(json['spec_name'] as String?) ?? ''`
  - [x] `Lookback.startDate` ← `(json['start_date'] as String?) ?? ''`
  - [x] `Lookback.endDate` ← `(json['end_date'] as String?) ?? ''`
  - [x] `Lookback.current` ← `(json['current'] as num?)?.toInt() ?? 0`
  - [x] `Lookback.total` ← `(json['total'] as num?)?.toInt() ?? 0`
  - [x] `Lookback.currentDate` ← `(json['current_date'] as String?) ?? ''`

## `lib/features/settings/data/models/notification_prefs.dart`

- [x] type `CategoryRoute`
  - [x] `CategoryRoute.discord` ← `j['discord'] as bool? ?? true`
  - [x] `CategoryRoute.push` ← `j['push'] as bool? ?? false`
  - [x] method `CategoryRoute.toJson`
  - [x] method `CategoryRoute.copyWith`
- [x] type `NotificationType`
  - [x] `NotificationType.key` ← `(j['key'] ?? '').toString()`
  - [x] `NotificationType.group` ← `(j['group'] ?? 'Other').toString()`
  - [x] `NotificationType.label` ← `(j['label'] ?? j['key'] ?? '').toString()`
  - [x] `NotificationType.desc` ← `(j['desc'] ?? '').toString()`
- [x] type `NotificationPrefs`
  - [x] method `NotificationPrefs.toJson`
  - [x] getter `NotificationPrefs.groupsInOrder`
  - [x] method `NotificationPrefs.typesInGroup`
  - [x] method `NotificationPrefs.withRoute`
  - [x] method `NotificationPrefs.routeFor`
- [x] type `NotifChannel` → `enum NotifChannel: String`
- [x] type `NotificationCategoryMeta`
- [x] top-level `kNotificationCategories`

## `lib/features/strategies/data/models/strategy.dart`

- [x] type `SubStrategy`
- [x] type `Strategy`
- [x] type `StrategyListRow`
  - [x] getter `StrategyListRow.isTop5`
- [x] type `AgentResult`
  - [x] method `AgentResult._toDouble`
- [x] type `BestPerStrategy` → native form: struct with `mutating func fold`
  - [x] method `BestPerStrategy.fold`

## `lib/core/models/option_symbol.dart`

- [x] top-level `kOptionMultiplier`
- [x] top-level `_occ`
- [x] type `OccContract`
- [x] top-level `parseOccSymbol`
- [x] top-level `isOccOptionSymbol`

## `lib/core/models/portfolio_history.dart`

- [x] type `PortfolioHistory`
  - [x] getter `PortfolioHistory.isEmpty`
  - [x] method `PortfolioHistory.sinceLocalMidnight`

## `lib/features/strategies/strategy_config.dart`

- [x] top-level `_acronyms`
- [x] top-level `_titleizeToken`
- [x] top-level `humanizeStrategyConfigKey`
- [x] type `StrategyFieldMeta`
- [x] top-level `strategyFieldMeta`
- [x] top-level `getStrategyConfigFieldMeta`
- [x] type `SelectOption`
- [x] top-level `llmProviderOptions`
- [x] top-level `llmReasoningEffortOptions`
- [x] top-level `nvidiaReasoningEffortOptions`
- [x] top-level `ollamaThinkOptions`
- [x] top-level `bedrockReasoningOptions`
- [x] top-level `claudeCliEffortOptions`
- [x] top-level `knownLlmRoleLabels` → native form: `KeyValuePairs` (keeps the Dart map order)
- [x] top-level `knownLookbackLlmRoleLabels` → native form: `KeyValuePairs`

## `lib/features/dashboard/application/dashboard_controller.dart`

- [x] type `DashboardServicesNotifier` → native form: `DashboardModel.services` / `refreshNow()` / `pollServices()`
  - [x] method `DashboardServicesNotifier.fetch` → `DashboardModel.refreshNow()`
  - [x] method `DashboardServicesNotifier.interval` → `DashboardModel.servicesInterval` (10 s)
- [x] top-level `dashboardServicesProvider` → native form: `DashboardModel`, kept alive in `AppServices`
- [x] type `EngineBusyNotifier` → native form: `DashboardModel.busy`
  - [x] method `EngineBusyNotifier.build` → `busy` starts empty
  - [x] method `EngineBusyNotifier.isBusy` → `DashboardModel.isBusy(_:)`
  - [x] method `EngineBusyNotifier.run` → `DashboardModel.run(_:_:)`
  - [x] method `EngineBusyNotifier.action` → the `action` closure of `run`
- [x] top-level `engineBusyProvider` → native form: `DashboardModel`
- [x] type `BrokeragesNotifier` → native form: `DashboardModel.brokerages` + `brokeragesValue`
  - [x] method `BrokeragesNotifier.build` → `DashboardModel.loadBrokerages()` (also the `ref.invalidate` path)
- [x] top-level `brokeragesProvider` → native form: `DashboardModel`
- [x] top-level `portfolioUpdatedAtProvider` → native form: `DashboardModel.portfolioUpdatedAt`

## `lib/features/dashboard/application/portfolio_analytics.dart`

- [x] type `SectorSlice`
- [x] type `ConcentrationStats`
  - [x] getter `ConcentrationStats.isEmpty`
- [x] top-level `concentration`
- [x] type `Mover`
- [x] top-level `todaysMovers` → native form: ordered `(symbol, pct)` pairs instead of a Map (keeps tie order)
- [x] top-level `pctChangeOf`
- [x] type `RiskMetrics`
  - [x] getter `RiskMetrics.isEmpty`
- [x] top-level `riskMetrics`

## `lib/features/dashboard/application/market_hours.dart`

- [x] top-level `isMarketOpenAtEt` → native form: ET wall clock is a `Date` read in GMT (`etWallClockCalendar`)
- [x] top-level `etFromUtc` → native form: returns that GMT-read wall-clock `Date`

## `lib/features/dashboard/application/selected_account_controller.dart`

- [x] top-level `_kSelectedAccountKey` → `SelectedAccountModel.storageKey` (`dashboard_selected_account`)
- [x] type `SelectedAccountController` → native form: `SelectedAccountModel`
  - [x] method `SelectedAccountController.build` → native form: synchronous keychain hydrate in `init`
  - [x] method `SelectedAccountController.select` → `SelectedAccountModel.select(_:)`
- [x] top-level `selectedAccountProvider`


## Query and body keys (hand-written, per repository method)

### AgentRepository
- [x] `runs(page: 1, perPage: 20)` query `page`, `per_page` (strings)
- [x] `setControl` body `running?`, `paused?`, `special_request` only when non-empty
- [x] `forceStop(logId)` no body

### AuthRepository
- [x] `login` `POST /auth/login` body `username`, `password`
- [x] `fetchMe` `GET /auth/me`

### BacktestRepository
- [x] `list` `GET /backtests` query `page`, `per_page` (default 15), `sort_by` (`completed_at`), `sort_order` (`desc`)
- [x] `get` `GET /backtests/{id}`, `status` `/status`, `summary` `/summary`, `graphData` `/graph-data`, `playbackData` `/playback-data`, `llmCost` `/llm-cost`
- [x] `logs` `GET /backtests/{id}/logs` query `since_line`
- [x] `action(id, name)` `POST /backtests/{id}/{name}`, no body
- [x] `create(body)` `POST /backtests` body passed through

### BrokerageRepository
- [x] `list` `GET /brokerages` → `accounts`
- [x] `link` `POST /brokerages`, `edit` `PUT /brokerages/{id}`, `testAlpaca` `POST /brokerages/test-alpaca` bodies passed through
- [x] `remove` `DELETE /brokerages/{id}`

### ChatbotRepository
- [x] `conversations` `GET /chatbot/conversations` → `conversations`
- [x] `createConversation` body `model_id?`, `title?` (present only when non-nil)
- [x] `conversation`, `patchConversation(body)` `PATCH`, `deleteConversation` `DELETE`, `clear` `POST …/clear`
- [x] `turn` `POST …/turn` body `content` → `messages`
- [x] `confirmTool` `POST …/confirm-tool` body `message_id`, `approved`
- [x] `tools` `GET /chatbot/tools` → `tools`
- [x] `models` `GET /models` → `models` ?? `data`, or a bare list

### CryptoRepository
- [x] `listInstances` `GET /instances`, keeps `kind == 'crypto'`
- [x] `getInstance`, `createInstance` (`POST /instances`), `updateInstance` (`PATCH /instances/{id}`) unwrap `instance` ?? body
- [x] `instanceBacktests` `GET /instances/{id}/backtests` query `page=1`, `per_page=20`, `sort_by=completed_at`, `sort_order=desc`
- [x] `createBacktest` body `instance_id`, `stocks`, `start_date`, `end_date`, `granularity` (`'900'`), `initial_cash` (10000), `emulate_fee_venue` (`'default'`)
- [x] `startInstance`, `stopInstance`, `deleteInstance(force:)` query `force=true` only when forced
- [x] `brokerages` → `accounts`, `strategies` → `strategies`
- [x] `accountEquity` `GET /brokerages/{id}/positions`: `cash` + Σ `marketValue`; 0 on any error

### DashboardRepository
- [x] `services` four GETs in parallel (`/status`, `/agent/control`, `/digest/control`, `/nexus/status`), each failure → `{}`; empty → nil
- [x] `brokerages` → `accounts`; `portfolioHistory` query `range`; `accountHoldings` → `cash`, `positions`
- [x] `nexusTrends` query `status` (`active`), `limit` (`'50'`) → `trends`
- [x] `backfillQueue` → `queue`; `discoveredStocks` → `stocks`; `tradeContexts` → `contexts`; `nexusOutcomes`; `momentumWatchlist`
- [x] `startPriceService` `POST /config/run-price-service`; `terminatePrice` `POST /config/terminate-price`
- [x] `controlDiscover` body `running`; `controlDigest` body `running`; `controlNexus` body `running`; `digestSendNow` no body
- [x] `controlAgent` body `running?`, `paused?`, `special_request?` (present when non-nil, even empty)

### InstanceRepository
- [x] `listInstances` drops `kind == 'kalshi'` and `kind == 'crypto'`
- [x] `createInstance` body `id`, `name` (non-empty), `granularity` (?? `'60'`), `run_command`, `brokerage_id` (non-empty), `max_usage?`, `strategy_id` (non-empty)
- [x] `patchInstance` / `createInstance` unwrap `instance` ?? body
- [x] `deleteInstance(force:)` query `force=true` only when forced
- [x] `clearState` body `scope`, `apply`, `confirm?`; `previewClearState` body `scope`, `apply: false`; `applyClearState` body `scope`, `apply: true`, `confirm: id`
- [x] `addStock` body `symbol`; `removeStock` `DELETE /instances/{id}/stocks/{symbol}`
- [x] `linkBrokerage` body `brokerage_id`; `unlinkBrokerage` `PATCH` body `brokerage_id: ''`; `linkDataBrokerage` body `brokerage_id` (may be null)
- [x] `linkStrategy` body `strategy_id` = int when it parses, else the string; `unlinkStrategy` no body
- [x] `listBacktests` query `page`, `per_page` (15), `sort_by`, `sort_order`
- [x] `createBacktest` body `instance_id`, `stocks`, `start_date`, `end_date`, `granularity` (`'60'`), `initial_cash` (100000)
- [x] `getBacktestStatus`, `listBrokerages` → `accounts`, `listStrategies` → `strategies`

### KalshiRepository
- [x] `edges` query `limit=10`; `instanceDecisions` query `limit=200`; `instanceOrders` query `limit=50`
- [x] `models` `GET /models`: `models` key (or bare list), keeps maps with non-null `id`
- [x] `updateInstance` `PATCH /instances/{id}/kalshi/config`; `deleteInstance` `DELETE /instances/{id}` query `force=true`
- [x] `createBacktest` returns `id` as a string; `listBacktests` → `backtests`
- [x] `backtestStatus`, `backtestResults`, `stopBacktest`, `deleteBacktest` under `/kalshi/backtests/{id}`

### LearningRepository
- [x] `findings`, `approvals` (→ `pending`), `funnels` query `limit` (`'100'`)
- [x] `setDocumentAllowlist` body `config.document_allowlist`; `setWatchedInstances` body `config.watched_instances`; `setMode` body `config.mode`; `setRunning` body `running`
- [x] `decide` `POST /learning/approvals/{id}` body `decision`

### LiveRepository
- [x] `liveState` → nil on a 404
- [x] `equityHistory` query `range`
- [x] `symbolHistoricals` returns `{}` without a request when `symbols` is empty; query `symbols` (comma-joined), `range`
- [x] `holdingOpens` `GET /brokerages/{id}/holding-opens` → `opens`, unparseable dropped
- [x] `sendCommand` body `type`, `payload`; `commandStatus` `GET /live-commands/{id}`

### ModelRepository
- [x] `list` `GET /models` bare list or `models`
- [x] `create` `POST /models`, `update` `PUT /models/{id}` unwrap `model`
- [x] `delete` query `force=true`
- [x] `testCli`, `testLlm` (`POST /llm/test`), `ollamaModels`, `bedrockModels` (bare list or `models`)
- [x] codex: `codexStatus`, `codexInstall` (body `{}`), `codexInstallJob`, `codexLoginStart(body)`, `codexLoginStatus`, `codexLoginCancel`, `codexLogout`
- [x] claude: `claudeModels(cliPath:)` query `cli_path` only when non-empty; `claudeAuthStatus`; `claudeLoginStart(body)`; `claudeLoginSubmit` body `code`; `claudeLoginStatus`; `claudeLoginCancel`; `claudeLogout`

### NexusRepository
- [x] `status`, `control(body)`, `rebuild(body)`, `deleteEdges(body)`, `cache`

### NotificationPrefsRepository
- [x] `get`; `save` `PUT` body `categories.{key}.{discord,push}`; `sendTest` body `channel` (`discord` | `push`)

### OnboardingRepository
- [x] `state`, `complete`, `reset`

### StrategyRepository
- [x] `list` → `strategies`; `get` unwraps `strategy` ?? body; `update(preserveHistory:)` adds `preserve_history: true` only when set
- [x] `previewConfigChange` body `strategies`; `available` → `strategies` as strings
- [x] `agentResults` query `limit=10000` → `results`; `top5` → `top5`; `agentBest` nil on any error; `bestPerStrategy` → `by_strategy`
- [x] `instances` → `instances`; `createInstance(body)`; `linkStrategy` path percent-encodes the instance id, body `strategy_id` (int); `createBacktest(body)`
- [x] static `computeBestByStrategy`, `mergeStrategyRows`

### SwingRepository
- [x] `_signals` query `status`; bare list or `signals`; drops empty ids and other statuses
- [x] `pendingSignals` newest `created_at` first; `signalsWithStatus`; `approvedSignals` reads `approved` and `approved_half`, newest `decided_at` first
- [x] `resend` no body; `decide` body `decision`, `reason` (trimmed, only when non-empty)
- [x] `wheel` `GET /instances/{id}/wheel`, stamps `fetchedAt`; non-map → empty snapshot

### SymbolSearchRepository
- [x] `search` `GET /symbols/search` query `q` → `results`, drops empty symbols

### TokenUsageRepository
- [x] `summary` query `range`; `timeseries` query `range`, `bucket` (bare list or `rows`)
- [x] `topSpenders` query `range`, `group_by`, `limit`; `byBacktest` query `range`, `limit`; `calls` query `limit`, `range`
- [x] `fetchAll` six requests in parallel; bucket `hour` for `24h`, else `day`; `partialError` = "N of 6 requests failed: <first error>"

## Riverpod providers declared in data files (not ported here — the owning Wave 2 agent)
- [x] `cryptoInstancesProvider` (crypto) → Wave 2 kalshi agent
- [x] `kalshiPortfolioProvider`, `kalshiEdgesProvider`, `kalshiPositionsProvider`, `kalshiInstancesProvider`, `kalshiInstanceDetailProvider`, `kalshiInstanceDecisionsProvider`, `kalshiInstanceLiveProvider`, `kalshiInstanceOrdersProvider` → Wave 2 kalshi agent
- [x] every `xRepositoryProvider` → `AppServices+Repositories.swift`

## Tests ported
- [x] swing_repository_test, token_usage_repository_test, strategy_repository_preserve_test
- [x] backtest_test (models), nexus_test (models), chat_models_test, agent_runs_test (models)
- [x] portfolio_analytics_test, market_hours_test, nexus_models_test
- [x] strategy_config_test, symbol_search_models_test (models), notification_prefs_model_test
- [x] option_symbol_test, live_state_options_test
- [x] llm_config_draft_test: data parts → none: every case exercises `LlmConfigDraft` in presentation/llm_config_form.dart (Wave 2 models agent)

## Fields of block-bodied `fromJson` factories (hand-written; the generator skips them)

### `Instance` (instances/data/models/instance.dart)
- [x] `stocks` ← list items: map → `symbol ?? ticker ?? ''` (non-empty), non-empty string as is
- [x] `id` ← `(id ?? '').toString()`; `name` ← `(name ?? id ?? '')`; `createdBy` ← `created_by ?? 'user'`
- [x] `runCommand` ← `run_command == true || runCommand == true`; `crashed` ← `crashed == true`
- [x] `strategyId`, `brokerageId`, `alpacaDataBrokerageId`, `kind` ← `?.toString()`
- [x] `granularityTimeIncrement` ← `granularity_time_increment as num?` toInt ?? `int.tryParse(granularity.toString())`
- [x] `maxUsage` (double), `uptimeSeconds` (int)
- [x] `brokerage`, `strategy`, `cryptoConfig` ← map or null
- [x] `copyWith` with a sentinel for nullable fields → native form: `var` properties

### `BacktestRow` (instances) → `InstanceBacktestRow`
- [x] `stocks` ← non-empty strings only; `id`, `startDate`, `endDate`, `completedAt` (string), `status` (?? 'unknown')
- [x] `timeElapsedSeconds`, `pnl`, `pnlPercent`, `initialCash` (double); `instanceId`, `granularity` (string); `progress` (int)
- [x] `copyWith({progress, status})`

### `PortfolioHistory` (core/models)
- [x] `timestamps` ← num (ms when > 1e12, else s×1000) or ISO string, unparseable dropped
- [x] `values` ← `(v as num?)?.toDouble() ?? 0`
- [x] `currentValue`, `openValue`, `changeAbs`, `changePct` ← `as num?`
- [x] `isEmpty`, `sinceLocalMidnight()`

### `ChatMessage` / `Conversation` / `ToolCall` (chatbot)
- [x] ChatMessage `toolCalls`, `pendingTool` (dedicated key, else first tool call when pending_confirmation), `blocks` (maps only), `id`, `role` (?? 'assistant'), `content`, `createdAt` (`DateTime.parse` or null), `status`, `name`
- [x] Conversation `messages`, `autoConfirmSafeTools` (top-level bool, else `settings.auto_confirm_safe_tools` bool, else false), `id`, `title`, `modelId`, `modelName`, `messageCount` (?? messages.length), `copyWith`

### `Brokerage` (brokerages)
- [x] `accountNumber` ← `account_number ?? alpaca_account_number`; `id` ('' default); `brokerageType` ('alpaca'); `accountName` (''); `status`
- [x] `paper` ← `alpaca_paper as bool? ?? paper as bool? ?? true`
- [x] `alpacaDataFeed`, `lastRefreshAt`, `lastError`, `managementType`; `equity`, `buyingPower` ← num or `double.tryParse` of a string
- [x] `toJson` with the `if (x != null)` keys

### `NotificationPrefs` (settings)
- [x] `categories` ← map entries whose value is a map; `types` ← map items; `toJson` → `{categories: {k: {discord, push}}}`

### `Strategy` / `SubStrategy` / `StrategyListRow` / `AgentResult` (strategies)
- [x] SubStrategy `config` ← conditions then config, skipping null and `''`; `strategy` (`strategy ?? type ?? ''`); `executionPosition` (?? fallbackPosition = raw index); `decisionPhase` (?? 'pre'); `weight`; `executionScope`
- [x] Strategy `strategies` (maps only, index from the raw list), `id` (num → int ?? 0), `name`, `description`
- [x] StrategyListRow `subStrategyNames` (non-empty names of map items), `subCount` (raw list length), `instancesUsing` (toString), `id`, `name`, stats passed in
- [x] AgentResult `stocksUsed` (non-empty strings), `backtestId` (`backtest_id ?? id ?? ''`), `strategyId`, `overallProfit`/`pnlPercent` (`_toDouble`), `startDate`, `endDate`, `createdAt`

### `NexusControl` (nexus)
- [x] every field listed in the Dart constructor: running, auto-update (168 / 3 / 14 defaults), nextAutoUpdateAt, selectedPhases (`whereType<num>` → int), phase7HistoryQuarters (1), historical mode, phase labels, phaseOptions, deletePhaseOptions, delete operation (9 fields), rebuild operation (7 fields)

### Others
- [x] `ClaudeModelOption` `value` (`_asStr ?? ''`), `label` (non-empty label, else value), `description`, `requiresCredits` (`as bool? ?? false`)
- [x] `PortfolioValuePoint` `timestamp` (`timestamp ?? date ?? time`: num → ms/s, else `tryParse ?? now`), `value`
- [x] `aggregateBySector` (top-level, multi-line signature) → native form: ordered `(symbol, value)` pairs instead of a Map (keeps tie order)
