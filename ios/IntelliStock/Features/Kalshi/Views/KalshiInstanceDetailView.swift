import SwiftUI

/// A Kalshi instance — `KalshiInstanceDetailScreen`: status, Start / Stop,
/// decision summary, portfolio, live matches, orders, pregame analysis, the
/// LLM-reasoned decision log and live logs. An inset-grouped list under the
/// inline instance name; Start / Stop is the toolbar's primary action and the
/// rest sit in its More menu.
struct KalshiInstanceDetailView: View {
    let instanceId: String

    @Environment(AppServices.self) private var services
    @State private var model: KalshiInstanceDetailModel?
    @State private var toast: Toast?
    @State private var confirm: ConfirmRequest?
    @State private var editing = false

    var body: some View {
        Group {
            if let model, let detail = model.detailValue {
                content(model, detail: detail)
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle(model?.title ?? "Kalshi Instance")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbar }
        .toast($toast)
        .confirmAlert($confirm)
        .sheet(isPresented: $editing) {
            if let model, let detail = model.detailValue {
                KalshiInstanceSheet(
                    accounts: [],
                    initialBrokerageId: model.brokerageId,
                    editInstanceId: instanceId,
                    editName: detail["name"].flatMap { $0.isNull ? nil : $0.dartDescription },
                    editConfig: detail["config"]?.orderedObject ?? JSONObject(),
                    repository: { [services] in services.kalshiRepository },
                    onCreated: { _ in Task { await model.refresh() } }
                )
            }
        }
        .task(id: instanceId) {
            // Reused on reappear: the data stays on screen while it refreshes.
            if model?.instanceId != instanceId {
                model = KalshiInstanceDetailModel(instanceId: instanceId, repository: { [services] in services.kalshiRepository })
            }
            await model?.poll(lifecycle: services.lifecycle)
        }
    }

    // MARK: Toolbar

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        if let model, model.detailValue != nil {
            let running = model.running
            ToolbarItem(placement: .topBarTrailing) {
                ToolbarMenu {
                    Section {
                        Button {
                            services.router.push(.kalshiBacktest(instanceId))
                        } label: {
                            Label("Backtest", systemImage: Symbol.named("science"))
                        }
                        Button {
                            editing = true
                        } label: {
                            Label("Edit Config", systemImage: Symbol.named("tune"))
                        }
                        .disabled(model.busy)
                    }
                    Section {
                        Button(role: .destructive) {
                            confirm = ConfirmRequest(
                                title: "Delete instance?",
                                body: "This cannot be undone.",
                                confirmLabel: "Delete",
                                onConfirm: {
                                    try await model.delete()
                                    services.router.pop()
                                },
                                onError: { error in
                                    if !error.isCancellation { toast = Toast(KalshiFormat.errorText(error), style: .error) }
                                }
                            )
                        } label: {
                            Label("Delete", systemImage: Symbol.named("delete"))
                        }
                        .disabled(model.busy)
                    }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task {
                        if let message = await model.startStop(!running) {
                            toast = Toast(message, style: .error)
                        }
                    }
                } label: {
                    Text(running ? "Stop" : "Start")
                }
                .dsProminentButton()
                .disabled(model.busy)
            }
        }
    }

    // MARK: Content

    private func content(_ model: KalshiInstanceDetailModel, detail: JSONObject) -> some View {
        List {
            statusSection(model, model.decisions.value?["summary"]?.orderedObject)
            if !model.brokerageId.isEmpty {
                KalshiPortfolioHero(
                    title: "Portfolio value",
                    state: model.portfolio,
                    onRetry: { Task { await model.reloadPortfolio() } }
                )
            }
            liveSection(model.live.value)
            positionsSection(model.positions?.value ?? [])
            ordersSections(model.orders.value ?? JSONObject())
            pregameAnalysis(model)
            decisionLog(model)
            // The log tail opens in place from its one row.
            Section("Live logs") {
                LiveLogsPanel(instanceId: instanceId)
                    .id(instanceId)
                    .listRowInsets(EdgeInsets())
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.refresh() }
    }

    // MARK: Status + summary

    private func statusSection(_ model: KalshiInstanceDetailModel, _ s: JSONObject?) -> some View {
        func count(_ k: String) -> String {
            guard let v = s?[k], !v.isNull else { return "0" }
            return v.dartDescription
        }
        return Section("Status") {
            LabeledContent("State") {
                StatusDot(
                    model.running ? "Running" : "Stopped",
                    color: model.running ? DS.Palette.success : .secondary,
                    pulsing: model.running
                )
            }
            LabeledContent("Mode", value: model.isLive ? "Live" : "Paper")
            StatGrid(columns: 2) {
                StatCell(label: "Placed", value: count("placed"))
                StatCell(label: "Skipped", value: count("skipped"))
                StatCell(label: "Queued", value: count("queued"))
                StatCell(label: "Blocked", value: count("blocked"))
            }
            .padding(.vertical, 4)
            paperPnl(s)
        }
    }

    /// Paper P&L: realized (closed) + live unrealized (open).
    @ViewBuilder
    private func paperPnl(_ s: JSONObject?) -> some View {
        let realC = s?["realized_pnl_cents"]?.double
        let unrealC = s?["unrealized_pnl_cents"]?.double
        let openPos = s?["open_positions"]?.double.flatMap { Int(dartTruncating: $0) }
        if realC != nil || unrealC != nil {
            let realColor: Color = realC == nil ? .secondary : ((realC ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger)
            let unrealColor: Color = unrealC == nil ? .secondary : ((unrealC ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger)
            VStack(alignment: .leading, spacing: 2) {
                Text("Paper P&L")
                let realized = Text("realized \(realC.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")").foregroundStyle(realColor)
                let dot = Text("  ·  ").foregroundStyle(.tertiary)
                let unrealized = Text("unrealized \(unrealC.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")").foregroundStyle(unrealColor)
                let live = Text(openPos.map { " (live · \($0) open)" } ?? " (live)").foregroundStyle(.secondary)
                Text("\(realized)\(dot)\(unrealized)\(live)")
                    .font(.subheadline.monospacedDigit())
            }
            .accessibilityElement(children: .combine)
        }
    }

    // MARK: Live matches

    @ViewBuilder
    private func liveSection(_ live: JSONObject?) -> some View {
        let matches = live?["matches"]?.arrayValue ?? []
        if !matches.isEmpty {
            Section("Live now · \(matches.count)") {
                ForEach(Array(matches.enumerated()), id: \.offset) { _, raw in
                    liveRow(raw.orderedObject ?? JSONObject())
                }
            }
        }
    }

    private func liveRow(_ m: JSONObject) -> some View {
        let score = m["score"]?.orderedObject
        let probs = m["market_probs"]?.orderedObject ?? JSONObject()
        let decisions = m["decisions"]?.arrayValue ?? []
        let news = m["news"]?.string ?? ""
        let clock: String = {
            if let c = score?["clock"], !c.isNull, !c.dartDescription.isEmpty { return c.dartDescription }
            if let e = m["elapsed_min"]?.double { return "\(Int(e.rounded()))'" }
            return "Live"
        }()
        func goals(_ k: String) -> String {
            guard let score else { return "0" }
            guard let v = score[k], !v.isNull else { return "0" }
            return v.dartDescription
        }
        let home = KalshiPregame.str(m["home"])
        let away = KalshiPregame.str(m["away"])

        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                teamBadge(logo: KalshiPregame.str(m["home_logo"]), name: home)
                VStack(spacing: 4) {
                    Text("\(goals("home"))  :  \(goals("away"))")
                        .font(.title3.bold().monospacedDigit())
                    StatusDot(clock, color: DS.Palette.success, pulsing: true, font: .caption)
                }
                .frame(maxWidth: .infinity)
                teamBadge(logo: KalshiPregame.str(m["away_logo"]), name: away)
            }
            ForEach(probs.entries, id: \.key) { e in
                let v = min(max(e.value.double ?? 0, 0), 1)
                HStack(spacing: 8) {
                    Text(sideLabel(m, e.key))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .frame(width: 72, alignment: .leading)
                    ProgressView(value: v)
                        .tint(DS.Palette.accent)
                    Text("\(Int((v * 100).rounded()))%")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            if !news.isEmpty {
                Text(news.components(separatedBy: "\n").first ?? "")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            if !decisions.isEmpty {
                MarketsFlowLayout {
                    ForEach(Array(decisions.prefix(4).enumerated()), id: \.offset) { _, d in
                        let act = KalshiPregame.str(d["action"])
                        let size = d["size"]
                        let sz = (!size.isNull && size != .int(0) && size != .double(0)) ? " \(size.dartDescription)" : ""
                        MarketsTag(text: "\(act.dsSentenceCased)\(sz)", color: actionColor(act))
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }

    private func sideLabel(_ m: JSONObject, _ side: String) -> String {
        if side == "home" { return m["home"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "Home" }
        if side == "away" { return m["away"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "Away" }
        return "Draw"
    }

    private func actionColor(_ a: String) -> Color {
        switch a {
        case "open", "add": DS.Palette.success
        case "reduce": DS.Palette.warning
        case "exit": DS.Palette.danger
        default: .secondary
        }
    }

    private func teamBadge(logo: String, name: String) -> some View {
        VStack(spacing: 6) {
            MarketsCrest(url: logo, initials: KalshiFormat.badgeInitials(name), size: 44)
            Text(name)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(width: 76)
    }

    // MARK: Positions and orders

    @ViewBuilder
    private func positionsSection(_ positions: [KalshiPosition]) -> some View {
        if !positions.isEmpty {
            Section("Open positions · \(positions.count)") {
                ForEach(Array(positions.enumerated()), id: \.offset) { _, p in
                    KalshiPositionRow(position: p)
                }
            }
        }
    }

    /// The Dart "Orders" card's four groups, each its own section: pending,
    /// filled, mock positions and mock filled.
    @ViewBuilder
    private func ordersSections(_ data: JSONObject) -> some View {
        let placed = data["placed"]?.arrayValue ?? []
        let fills = data["fills"]?.arrayValue ?? []
        let mock = data["mock"]?.arrayValue ?? []
        let mockHistory = data["mock_history"]?.arrayValue ?? []
        Section("Pending · \(placed.count)") {
            if placed.isEmpty {
                emptyNote("No resting orders — everything filled.")
            }
            ForEach(Array(placed.prefix(12).enumerated()), id: \.offset) { _, o in
                orderRow(o.orderedObject ?? JSONObject(), filled: false)
            }
        }
        if !fills.isEmpty {
            Section("Filled · \(fills.count)") {
                ForEach(Array(fills.prefix(12).enumerated()), id: \.offset) { _, f in
                    orderRow(f.orderedObject ?? JSONObject(), filled: true)
                }
            }
        }
        Section("Mock positions · \(mock.count)") {
            if mock.isEmpty {
                emptyNote("No mock (paper) positions.")
            }
            ForEach(Array(mock.prefix(12).enumerated()), id: \.offset) { _, m in
                mockRow(m.orderedObject ?? JSONObject())
            }
        }
        if !mockHistory.isEmpty {
            Section("Mock filled · \(mockHistory.count)") {
                ForEach(Array(mockHistory.prefix(12).enumerated()), id: \.offset) { _, m in
                    mockHistoryRow(m.orderedObject ?? JSONObject())
                }
            }
        }
    }

    private func emptyNote(_ text: String) -> some View {
        Text(text).foregroundStyle(.secondary)
    }

    /// A crest, the match and pick, and a trailing figure: the order rows'
    /// shared shape.
    private func tradeRow<Trailing: View>(
        logo: String,
        pick: String,
        match: String,
        detail: String?,
        @ViewBuilder trailing: () -> Trailing
    ) -> some View {
        HStack(spacing: 12) {
            MarketsCrest(url: logo, initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")), size: 32)
            VStack(alignment: .leading, spacing: 2) {
                Text(match).font(.headline).lineLimit(1)
                Text(pick).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                if let detail {
                    Text(detail).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trailing()
        }
        .accessibilityElement(children: .combine)
    }

    private func orderRow(_ o: JSONObject, filled: Bool) -> some View {
        let match = KalshiPregame.str(o["match"], o["market_ticker"])
        let pick = KalshiPregame.str(o["pick_label"], o["side"])
        let edge = o["edge"]?.double
        let detail = filled
            ? "\(dartString(o["contracts"])) @ \(dartString(o["price_cents"]))¢"
            : "\(KalshiFormat.firstNonNull(o["contracts"], o["size"], .int(0)))×"
        return tradeRow(logo: KalshiPregame.str(o["pick_logo"]), pick: pick, match: match, detail: detail) {
            VStack(alignment: .trailing, spacing: 4) {
                if !filled, o["in_play"] == .bool(true) {
                    MarketsTag(text: "Live", color: DS.Palette.danger)
                }
                if filled {
                    let action = KalshiPregame.str(o["action"])
                    MarketsTag(text: action.dsSentenceCased, color: o["action"] == .string("sell") ? DS.Palette.warning : DS.Palette.success)
                } else if let edge {
                    Text(KalshiFormat.signedEdge(edge))
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(edge >= 0 ? DS.Palette.success : DS.Palette.danger)
                }
            }
        }
    }

    /// `'${x}'` interpolation of a JSON value (null prints "null").
    private func dartString(_ v: JSON?) -> String {
        (v ?? .null).dartDescription
    }

    /// Settled / expired paper trade: realized P&L (the section says Mock).
    private func mockHistoryRow(_ m: JSONObject) -> some View {
        let match = KalshiPregame.str(m["match"], m["market_ticker"])
        let pick = KalshiPregame.str(m["pick_label"], m["side"])
        let contracts = m["contracts"]?.double.map { Int($0) } ?? 0
        let entryCents = m["price_cents"]?.double.map { Int($0) }
        let rCents = m["realized_pnl_cents"]?.double
        let rPos = (rCents ?? 0) >= 0
        let outcome = KalshiPregame.str(m["outcome"])
        return tradeRow(
            logo: KalshiPregame.str(m["pick_logo"]),
            pick: pick,
            match: match,
            detail: "\(contracts) @ \(entryCents.map(String.init) ?? "—")¢\(outcome.isEmpty ? "" : " · \(outcome)")"
        ) {
            Text(rCents.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")
                .font(.body.monospacedDigit())
                .foregroundStyle(rCents == nil ? Color.secondary : (rPos ? DS.Palette.success : DS.Palette.danger))
        }
    }

    /// Paper position: live unrealized P&L plus the entry → mark trail.
    private func mockRow(_ m: JSONObject) -> some View {
        let match = KalshiPregame.str(m["match"], m["market_ticker"])
        let pick = KalshiPregame.str(m["pick_label"], m["side"])
        let contracts = m["contracts"]?.double.map { Int($0) } ?? 0
        let entryCents = m["entry_cents"]?.double.map { Int($0) }
        let markCents = m["mark_cents"]?.double.map { Int($0) }
        let upCents = m["unrealized_pnl_cents"]?.double
        let upPos = (upCents ?? 0) > 0
        // Total current value = contracts × current mark (fallback entry).
        let value = Double(contracts * (markCents ?? entryCents ?? 0)) / 100
        return tradeRow(
            logo: KalshiPregame.str(m["pick_logo"]),
            pick: pick,
            match: match,
            detail: "\(contracts) @ \(entryCents.map(String.init) ?? "—")¢ → \(markCents.map(String.init) ?? "—")¢"
        ) {
            EntityRowValue(
                "$\(dartToStringAsFixed(value, 2))",
                detail: upCents.map { "\(upPos ? "+" : "-")$\(dartToStringAsFixed(abs($0) / 100, 2)) P&L" } ?? "—",
                detailColor: upCents == nil ? Color.secondary : (upPos ? DS.Palette.success : DS.Palette.danger)
            )
        }
    }

    // MARK: Pregame analysis

    private func pregameAnalysis(_ model: KalshiInstanceDetailModel) -> some View {
        Section("Pregame analysis") {
            switch model.decisions {
            case .loading:
                LoadingState()
            case .failed(let e):
                ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.refresh() } })
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            case .loaded:
                if model.pregameRowsEmpty {
                    Text("No games analyzed yet — picks will appear here once the bot scans the slate.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(model.pregameGames.enumerated()), id: \.offset) { _, sides in
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            pregameRow(sides, now: context.date)
                        }
                    }
                }
            }
        }
    }

    private func pregameRow(_ sides: [JSONObject], now: Date) -> some View {
        let head = sides.first ?? JSONObject()
        let match = KalshiPregame.str(head["match"])
        let home = KalshiPregame.str(head["home"])
        let away = KalshiPregame.str(head["away"])
        let title = !match.isEmpty ? match : (!home.isEmpty || !away.isEmpty ? "\(home) vs \(away)" : "Match")
        let best = KalshiPregame.bestEdge(sides)
        let bestPos = best > 0
        let cd = KalshiPregame.kickoffCountdown(head["kickoff_ts"]?.double, now: now)
        let elo = KalshiPregame.pair(head["home_elo"], head["away_elo"], decimals: 0)
        let xg = KalshiPregame.pair(head["home_xg"], head["away_xg"], decimals: 2)
        let parts = [elo.map { "Elo \($0)" }, xg.map { "xG \($0)" }].compactMap { $0 }
        let ordered = ["home", "draw", "away"].compactMap { s in sides.first { KalshiPregame.str($0["side"]) == s } }
        let shown = ordered.isEmpty ? sides : ordered

        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                MarketsCrest(url: KalshiPregame.str(head["pick_logo"]), initials: KalshiFormat.initials(home.isEmpty ? title : home), size: 32)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline).lineLimit(1)
                    let meta = ([cd] + parts).filter { !$0.isEmpty }
                    if !meta.isEmpty {
                        Text(meta.joined(separator: "  ·  "))
                            .font(.footnote.monospacedDigit())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Text("\(bestPos ? "+" : "")\(dartToStringAsFixed(best * 100, 1))% edge")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(bestPos ? DS.Palette.success : DS.Palette.danger)
            }
            ForEach(Array(shown.enumerated()), id: \.offset) { _, r in
                Divider().padding(.top, 8)
                pregameSideRow(r, now: now)
            }
        }
        .padding(.vertical, 4)
    }

    private func pregameSideRow(_ r: JSONObject, now: Date) -> some View {
        let pick = KalshiPregame.str(r["pick_label"], r["side"])
        let fair = r["fused_fair"]?.double
        let edge = r["edge"]?.double
        let edgePos = (edge ?? 0) > 0
        let price = KalshiPregame.priceCents(r)
        let dec = KalshiPregame.str(r["decision"])
        let modelOnly = r["sharp_prob"]?.isNull ?? true
        let updated = KalshiPregame.fmtTs(r["ts"].flatMap { $0.isNull ? nil : $0.dartDescription }, now: now)
        let spark = KalshiPregame.edgeSeries(r["edge_history"])
        let placed = dec.lowercased() == "placed"
        let entryEdge = r["entry_edge"]?.double

        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                HStack(spacing: 6) {
                    Text(pick).font(.subheadline.weight(.semibold)).lineLimit(1)
                    if modelOnly {
                        Text("model-only").font(.caption).italic().foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
                if spark.count >= 2 {
                    KalshiEdgeSparkline(values: spark)
                        .frame(width: 60, height: 16)
                }
                sidePill(dec)
            }
            let metrics = HStack(spacing: 14) {
                metric("fair", fair.map { "\(dartToStringAsFixed($0 * 100, 0))%" } ?? "—")
                metric("price", price.map { "\($0)¢" } ?? "—")
                metric("edge", edge.map { "\(edgePos ? "+" : "")\(dartToStringAsFixed($0 * 100, 1))%" } ?? "—",
                       color: edge == nil ? nil : (edgePos ? DS.Palette.success : DS.Palette.danger))
            }
            let stamp = Text("updated \(updated)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
            if updated.isEmpty {
                metrics
            } else {
                // One line when it fits, else the stamp under the figures.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 14) {
                        metrics
                        Spacer(minLength: 0)
                        stamp
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        metrics
                        stamp
                    }
                }
            }
            if placed, let entryEdge {
                Text("placed @ \(KalshiFormat.signedEdge(entryEdge))")
                    .font(.caption).italic()
                    .foregroundStyle(DS.Palette.success)
            }
        }
        .padding(.top, 8)
    }

    private func metric(_ label: String, _ value: String, color: Color? = nil) -> some View {
        let l = Text("\(label) ").foregroundStyle(.secondary)
        let v = Text(value).fontWeight(.semibold).foregroundStyle(color ?? .primary)
        return Text("\(l)\(v)").font(.caption.monospacedDigit())
    }

    /// Placed success, Blocked warning, anything else muted.
    private func sidePill(_ decision: String) -> some View {
        let d = decision.lowercased()
        let c: Color = d == "placed" ? DS.Palette.success : (d == "blocked" ? DS.Palette.warning : .secondary)
        return MarketsTag(text: decision.isEmpty ? "—" : decision.dsSentenceCased, color: c)
    }

    // MARK: Decision log

    private func decisionLog(_ model: KalshiInstanceDetailModel) -> some View {
        Section("Decision log") {
            switch model.decisions {
            case .loading:
                LoadingState()
            case .failed(let e):
                ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.refresh() } })
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            case .loaded(let d):
                let rows = d["decisions"]?.arrayValue ?? []
                if rows.isEmpty {
                    Text("No decisions logged yet.")
                        .foregroundStyle(.secondary)
                } else {
                    let p = KalshiPregame.page(rows, requested: model.decPage)
                    ForEach(Array(p.slice.enumerated()), id: \.offset) { j, r in
                        decisionRow(model, r.orderedObject ?? JSONObject(), index: p.start + j)
                    }
                    if p.pages > 1 {
                        HStack {
                            pageButton("chevron_left", label: "Previous page", enabled: p.page > 0) { model.setPage(p.page - 1) }
                            Spacer()
                            Text("Page \(p.page + 1) / \(p.pages)")
                                .font(.footnote.monospacedDigit())
                                .foregroundStyle(.secondary)
                            Spacer()
                            pageButton("chevron_right", label: "Next page", enabled: p.page < p.pages - 1) { model.setPage(p.page + 1) }
                        }
                    }
                }
            }
        }
    }

    private func pageButton(_ icon: String, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon))
                .font(.body.weight(.semibold))
                .frame(width: 44, height: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.borderless)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }

    private func decColor(_ d: String) -> Color {
        switch d {
        case "placed": DS.Palette.success
        case "queued": DS.Palette.warning
        case "blocked": DS.Palette.danger
        default: .secondary
        }
    }

    /// One decision: crest, match, pick (and "Mock" for paper), the edge and
    /// the decision word; tap to expand the probabilities, the LLM rationale
    /// and any block reason.
    private func decisionRow(_ model: KalshiInstanceDetailModel, _ r: JSONObject, index i: Int) -> some View {
        let open = model.expanded.contains(i)
        let dec = KalshiPregame.str(r["decision"])
        let match = KalshiPregame.str(r["match"], r["market_ticker"])
        let pick = KalshiPregame.str(r["pick_label"], r["side"])
        let edge = r["edge"]?.double
        let paper = r["paper"] == .bool(true)
        let rationale = r["llm_rationale"]?.string ?? ""
        let blockReason = r["block_reason"]?.string ?? ""

        return Button {
            withAnimation(.snappy) { model.toggleExpanded(i) }
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    MarketsCrest(url: KalshiPregame.str(r["pick_logo"]), initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")), size: 32)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(match).font(.headline).foregroundStyle(.primary).lineLimit(1)
                        Text(paper ? "\(pick) · Mock" : pick).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .trailing, spacing: 4) {
                        if let edge {
                            Text(KalshiFormat.signedEdge(edge))
                                .font(.subheadline.monospacedDigit())
                                .foregroundStyle(edge >= 0 ? DS.Palette.success : DS.Palette.danger)
                        }
                        StatusBadge(label: dec.dsSentenceCased, color: decColor(dec))
                    }
                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(open ? 90 : 0))
                        .accessibilityHidden(true)
                }
                if open {
                    MarketsFlowLayout(spacing: 14, runSpacing: 6) {
                        kv("Model", KalshiFormat.pct(r["model_prob"]?.double))
                        kv("Sharp", KalshiFormat.pct(r["sharp_prob"]?.double))
                        kv("LLM", KalshiFormat.pct(r["llm_adjustment"]?.double))
                        kv("Fair", KalshiFormat.pct(r["fused_fair"]?.double))
                        kv("Size", KalshiFormat.firstNonNull(r["size"], .int(0)))
                        if paper, let rc = r["realized_pnl_cents"], !rc.isNull {
                            kv("Paper P&L", "$\(dartToStringAsFixed((rc.double ?? 0) / 100, 2))")
                        }
                    }
                    if !rationale.isEmpty {
                        Label {
                            Text(rationale).foregroundStyle(.primary).multilineTextAlignment(.leading)
                        } icon: {
                            Image(systemName: Symbol.named("psychology")).foregroundStyle(.secondary)
                        }
                        .font(.footnote)
                    }
                    if !blockReason.isEmpty {
                        Text("\(dec == "blocked" ? "Blocked: " : "Skipped — ")\(blockReason)")
                            .font(.footnote)
                            .foregroundStyle(dec == "blocked" ? DS.Palette.danger : Color.secondary)
                            .multilineTextAlignment(.leading)
                    }
                }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityHint(open ? "Collapses the details" : "Shows the details")
    }

    private func kv(_ k: String, _ v: String) -> some View {
        let key = Text("\(k) ").foregroundStyle(.secondary)
        let value = Text(v).fontWeight(.semibold)
        return Text("\(key)\(value)").font(.caption.monospacedDigit())
    }
}

/// The edge-over-time sparkline — `_EdgeSparkPainter`: the side's edge
/// history normalised into the box (flat → centre line), green when the
/// latest edge is ≥ 0, else red. It draws itself in from the left like
/// `Sparkline`.
struct KalshiEdgeSparkline: View {
    let values: [Double]

    var body: some View {
        Canvas { context, size in
            guard values.count >= 2 else { return }
            let lo = values.min()!
            let hi = values.max()!
            let span = abs(hi - lo)
            let pad = 1.5
            let h = size.height - pad * 2
            let dx = size.width / Double(values.count - 1)
            var path = Path()
            for (i, v) in values.enumerated() {
                let norm = span == 0 ? 0.5 : (v - lo) / span
                let point = CGPoint(x: Double(i) * dx, y: pad + (1 - norm) * h)
                if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
            }
            let color = values.last! >= 0 ? DS.Palette.success : DS.Palette.danger
            context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 1.4, lineCap: .round, lineJoin: .round))
        }
        .chartDrawIn(duration: ChartDrawIn.sparkDuration, bleed: 2)
        .accessibilityHidden(true)
    }
}
