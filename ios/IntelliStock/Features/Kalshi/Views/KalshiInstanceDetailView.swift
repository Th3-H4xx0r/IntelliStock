import SwiftUI

/// A Kalshi instance — `KalshiInstanceDetailScreen`, in the web page's order:
/// the status and mode under the name, the portfolio card, the decision
/// summary, live matches, open positions, pregame analysis, the LLM-reasoned
/// decision log, the orders and the live logs. Start / Stop is a toolbar
/// button and the rest sit in its More menu.
struct KalshiInstanceDetailView: View {
    let instanceId: String

    @Environment(AppServices.self) private var services
    @State private var model: KalshiInstanceDetailModel?
    @State private var toast: Toast?
    @State private var confirm: ConfirmRequest?
    @State private var editing = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if let model, let detail = model.detailValue {
                content(model, detail: detail)
            } else {
                KalshiDetailSkeleton()
            }
        }
        .background(DS.Surface.page)
        .navigationTitle(model?.title ?? "Kalshi instance")
        .navigationBarTitleDisplayMode(.large)
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
                            Label("Edit config", systemImage: Symbol.named("tune"))
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
                .disabled(model.busy)
            }
        }
    }

    // MARK: Content

    private func content(_ model: KalshiInstanceDetailModel, detail: JSONObject) -> some View {
        List {
            // The web header's subtitle: the run state and the mode.
            Section {
                HStack(spacing: 8) {
                    StatusDot(
                        model.running ? "Running" : "Stopped",
                        tone: model.running ? .live : .off,
                        pulsing: model.running,
                        font: DS.Font.pageSub
                    )
                    Text("·").foregroundStyle(DS.Text.faint)
                    Text(model.isLive ? "Live" : "Paper")
                }
                .font(DS.Font.pageSub)
                .foregroundStyle(DS.Text.muted)
                .dsClearRow(top: 0)
            }
            if !model.brokerageId.isEmpty {
                KalshiPortfolioHero(
                    title: "Portfolio value",
                    state: model.portfolio,
                    onRetry: { Task { await model.reloadPortfolio() } }
                )
            }
            summarySection(model.decisions.value?["summary"]?.orderedObject)
            liveSection(model.live.value)
            positionsSection(model.positions?.value ?? [])
            pregameAnalysis(model)
            decisionLog(model)
            ordersSections(model.orders.value ?? JSONObject())
            // The log tail opens in place from its header.
            DSSection("Live logs", style: .plain) {
                LiveLogsPanel(instanceId: instanceId)
                    .id(instanceId)
                    .dsClearRow()
            }
        }
        .dsList()
        .refreshable { await model.refresh() }
    }

    // MARK: Decision summary

    private func summarySection(_ s: JSONObject?) -> some View {
        func count(_ k: String) -> String {
            guard let v = s?[k], !v.isNull else { return "0" }
            return v.dartDescription
        }
        let blocked = count("blocked")
        return DSSection("Decision summary") {
            StatGrid(columns: 2, verticalSpacing: 20) {
                StatCell(label: "Placed", value: count("placed"))
                StatCell(label: "Skipped", value: count("skipped"))
                StatCell(label: "Queued", value: count("queued"))
                StatCell(label: "Blocked", value: blocked, valueColor: blocked == "0" ? nil : DS.Palette.loss)
            }
            .statValueStyle(.figure)
            .padding(.vertical, 12)
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
            VStack(alignment: .leading, spacing: 4) {
                Text("Paper P&L · mock")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(realC.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")
                        .font(DS.Font.figure)
                        .foregroundStyle(realC == nil ? DS.Text.primary : ((realC ?? 0) >= 0 ? DS.Palette.gain : DS.Palette.loss))
                    Text("realized")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                .monospacedDigit()
                let unrealized = Text(unrealC.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")
                    .foregroundStyle(unrealC == nil ? DS.Text.muted : ((unrealC ?? 0) >= 0 ? DS.Palette.gain : DS.Palette.loss))
                let open = Text(openPos.map { " · \($0) open" } ?? "").foregroundStyle(DS.Text.faint)
                Text("Unrealized \(unrealized) (live)\(open)")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
            }
            .padding(.vertical, 8)
            .accessibilityElement(children: .combine)
        }
    }

    // MARK: Live matches

    @ViewBuilder
    private func liveSection(_ live: JSONObject?) -> some View {
        let matches = live?["matches"]?.arrayValue ?? []
        if !matches.isEmpty {
            DSSection("Live now", subtitle: "\(matches.count) match\(matches.count == 1 ? "" : "es") in play") {
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

        return VStack(alignment: .leading, spacing: 16) {
            HStack {
                teamBadge(logo: KalshiPregame.str(m["home_logo"]), name: home)
                VStack(spacing: 8) {
                    (Text(goals("home")) + Text("  :  ").foregroundStyle(DS.Text.faint) + Text(goals("away")))
                        .font(DS.Font.figureLarge)
                        .foregroundStyle(DS.Text.primary)
                        .monospacedDigit()
                    StatusDot(clock, tone: .live, pulsing: true, font: DS.Font.caption)
                }
                .frame(maxWidth: .infinity)
                teamBadge(logo: KalshiPregame.str(m["away_logo"]), name: away)
            }
            if !probs.entries.isEmpty {
                VStack(spacing: 8) {
                    ForEach(probs.entries, id: \.key) { e in
                        let v = min(max(e.value.double ?? 0, 0), 1)
                        HStack(spacing: 12) {
                            Text(sideLabel(m, e.key))
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.secondary)
                                .lineLimit(1)
                                .frame(width: 80, alignment: .leading)
                            KalshiBar(fraction: v)
                            Text("\(Int((v * 100).rounded()))%")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.label)
                                .monospacedDigit()
                                .frame(width: 40, alignment: .trailing)
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            }
            if !news.isEmpty {
                Text(news.components(separatedBy: "\n").first ?? "")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
                    .lineLimit(2)
            }
            if !decisions.isEmpty {
                decisionsLine(Array(decisions.prefix(4)))
            }
        }
        .padding(.vertical, 12)
    }

    /// The in-play decisions as one quiet line: "Open 2 · Reduce 1".
    private func decisionsLine(_ decisions: [JSON]) -> some View {
        var line = Text("")
        for (i, d) in decisions.enumerated() {
            let act = KalshiPregame.str(d["action"])
            let size = d["size"]
            let sz = (!size.isNull && size != .int(0) && size != .double(0)) ? " \(size.dartDescription)" : ""
            if i > 0 { line = line + Text(" · ").foregroundStyle(DS.Text.faint) }
            line = line + Text(act.dsSentenceCased).foregroundStyle(actionColor(act)) + Text(sz).foregroundStyle(DS.Text.secondary)
        }
        return line
            .font(DS.Font.caption)
            .monospacedDigit()
    }

    private func sideLabel(_ m: JSONObject, _ side: String) -> String {
        if side == "home" { return m["home"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "Home" }
        if side == "away" { return m["away"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "Away" }
        return "Draw"
    }

    /// The web's in-play action shades: open and add brightest, reduce and
    /// exit a step down, hold quiet.
    private func actionColor(_ a: String) -> Color {
        switch a {
        case "open", "add": DS.Text.soft
        case "reduce", "exit": DS.Text.label
        case "hold": DS.Text.muted
        default: DS.Text.secondary
        }
    }

    private func teamBadge(logo: String, name: String) -> some View {
        VStack(spacing: 8) {
            MarketsCrest(url: logo, initials: KalshiFormat.badgeInitials(name), size: 40)
            Text(name)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.label)
                .lineLimit(1)
        }
        .frame(width: 80)
    }

    // MARK: Positions and orders

    @ViewBuilder
    private func positionsSection(_ positions: [KalshiPosition]) -> some View {
        if !positions.isEmpty {
            DSSection("Open positions", subtitle: "\(positions.count) position\(positions.count == 1 ? "" : "s")", rowMinHeight: 64) {
                ForEach(Array(positions.enumerated()), id: \.offset) { _, p in
                    KalshiPositionRow(position: p)
                }
            }
        }
    }

    /// The web "Orders" card's four groups (resting, filled and mock), each
    /// its own card: pending, mock positions, filled and mock filled.
    @ViewBuilder
    private func ordersSections(_ data: JSONObject) -> some View {
        let placed = data["placed"]?.arrayValue ?? []
        let fills = data["fills"]?.arrayValue ?? []
        let mock = data["mock"]?.arrayValue ?? []
        let mockHistory = data["mock_history"]?.arrayValue ?? []
        // The four groups read as one Orders block: 20 pt apart, not 32.
        DSSection("Orders", subtitle: "Pending · \(placed.count)") {
            if placed.isEmpty {
                emptyNote("No resting orders — everything filled")
            }
            ForEach(Array(placed.prefix(12).enumerated()), id: \.offset) { _, o in
                orderRow(o.orderedObject ?? JSONObject(), filled: false)
            }
        }
        .listSectionSpacing(20)
        DSSection("", subtitle: "Mock positions · \(mock.count)") {
            if mock.isEmpty {
                emptyNote("No open mock positions")
            }
            ForEach(Array(mock.prefix(12).enumerated()), id: \.offset) { _, m in
                mockRow(m.orderedObject ?? JSONObject())
            }
        }
        .listSectionSpacing(20)
        if !fills.isEmpty {
            DSSection("", subtitle: "Filled · \(fills.count)") {
                ForEach(Array(fills.prefix(12).enumerated()), id: \.offset) { _, f in
                    orderRow(f.orderedObject ?? JSONObject(), filled: true)
                }
            }
            .listSectionSpacing(20)
        }
        if !mockHistory.isEmpty {
            DSSection("", subtitle: "Mock filled · \(mockHistory.count)") {
                ForEach(Array(mockHistory.prefix(12).enumerated()), id: \.offset) { _, m in
                    mockHistoryRow(m.orderedObject ?? JSONObject())
                }
            }
        }
    }

    private func emptyNote(_ text: String) -> some View {
        Text(text)
            .font(DS.Font.body)
            .foregroundStyle(DS.Text.muted)
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
                Text(match).font(DS.Font.body).foregroundStyle(DS.Text.primary).lineLimit(1)
                Text(pick).font(DS.Font.caption).foregroundStyle(DS.Text.muted).lineLimit(1)
                if let detail {
                    Text(detail).font(DS.Font.caption).foregroundStyle(DS.Text.muted).monospacedDigit().lineLimit(1)
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
                    StatusDot("Live", tone: .live, font: DS.Font.caption)
                }
                if filled {
                    Text(KalshiPregame.str(o["action"]).dsSentenceCased)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                } else if let edge {
                    Text(KalshiFormat.signedEdge(edge))
                        .font(DS.Font.body)
                        .monospacedDigit()
                        .foregroundStyle(edge >= 0 ? DS.Palette.gain : DS.Palette.loss)
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
                .font(DS.Font.body)
                .monospacedDigit()
                .foregroundStyle(rCents == nil ? DS.Text.muted : (rPos ? DS.Palette.gain : DS.Palette.loss))
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
                detailColor: upCents == nil ? DS.Text.muted : (upPos ? DS.Palette.gain : DS.Palette.loss)
            )
        }
    }

    // MARK: Pregame analysis

    private func pregameAnalysis(_ model: KalshiInstanceDetailModel) -> some View {
        DSSection("Pregame analysis", subtitle: "Home, draw and away edges per match, soonest kickoff first") {
            switch model.decisions {
            case .loading:
                Skeleton(width: 220, height: 14)
                    .accessibilityLabel("Loading")
            case .failed(let e):
                failedRow(KalshiFormat.errorText(e)) { Task { await model.refresh() } }
            case .loaded:
                if model.pregameRowsEmpty {
                    Text("No matches analyzed yet. Once the engine reviews a game's sides, the home, draw and away edges show together here.")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.vertical, 6)
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

    /// An error inside a card row, with Retry beside it.
    private func failedRow(_ message: String, retry: @escaping () -> Void) -> some View {
        HStack(spacing: 16) {
            Text(message)
                .font(DS.Font.body)
                .foregroundStyle(DS.Palette.loss)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button("Retry", action: retry)
                .buttonStyle(.dsSecondary)
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
                    Text(title)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                    let meta = ([cd] + parts).filter { !$0.isEmpty }
                    if !meta.isEmpty {
                        Text(meta.joined(separator: " · "))
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .monospacedDigit()
                            .lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            (Text("Best edge ") + Text("\(bestPos ? "+" : "")\(dartToStringAsFixed(best * 100, 1))%").foregroundStyle(bestPos ? DS.Palette.gain : DS.Palette.loss))
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .padding(.top, 8)
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(shown.enumerated()), id: \.offset) { _, r in
                    pregameSideRow(r, now: now)
                }
            }
            .padding(.top, 12)
        }
        .padding(.vertical, 12)
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
        let figures = "Fair \(fair.map { "\(dartToStringAsFixed($0 * 100, 0))%" } ?? "—") · \(price.map { "\($0)¢" } ?? "—")"

        return HStack(spacing: 12) {
            MarketsCrest(
                url: KalshiPregame.str(r["pick_logo"]),
                initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")),
                size: 24
            )
            VStack(alignment: .leading, spacing: 2) {
                (Text(pick).foregroundStyle(DS.Text.soft) + Text(modelOnly ? " · Model only" : "").foregroundStyle(DS.Text.muted))
                    .font(DS.Font.small)
                    .lineLimit(1)
                (Text(figures) + Text(updated.isEmpty ? "" : " · \(updated)").foregroundStyle(DS.Text.faint))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .lineLimit(1)
                if placed, let entryEdge {
                    Text("Placed at \(KalshiFormat.signedEdge(entryEdge))")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .monospacedDigit()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if spark.count >= 2 {
                KalshiEdgeSparkline(values: spark)
                    .frame(width: 40, height: 16)
            }
            // The edge over the decision word, as the decision log stacks them.
            VStack(alignment: .trailing, spacing: 2) {
                Text(edge.map { "\(edgePos ? "+" : "")\(dartToStringAsFixed($0 * 100, 1))%" } ?? "—")
                    .font(DS.Font.body)
                    .monospacedDigit()
                    .foregroundStyle(edge == nil ? DS.Text.muted : (edgePos ? DS.Palette.gain : DS.Palette.loss))
                Text(dec.isEmpty ? "—" : dec.dsSentenceCased)
                    .font(DS.Font.caption)
                    .foregroundStyle(Self.decisionColor(dec))
                    .lineLimit(1)
            }
            .frame(minWidth: 56, alignment: .trailing)
        }
        .accessibilityElement(children: .combine)
    }

    /// The web's decision words: placed brightest, queued a step down,
    /// skipped quiet, blocked in loss red. No pills.
    static func decisionColor(_ decision: String) -> Color {
        switch decision.lowercased() {
        case "placed": DS.Text.soft
        case "skipped": DS.Text.muted
        case "queued": DS.Text.secondary
        case "blocked": DS.Palette.loss
        default: DS.Text.secondary
        }
    }

    // MARK: Decision log

    private func decisionLog(_ model: KalshiInstanceDetailModel) -> some View {
        DSSection("Decision log", subtitle: "Every bet placed and every candidate considered") {
            switch model.decisions {
            case .loading:
                Skeleton(width: 220, height: 14)
                    .accessibilityLabel("Loading")
            case .failed(let e):
                failedRow(KalshiFormat.errorText(e)) { Task { await model.refresh() } }
            case .loaded(let d):
                let rows = d["decisions"]?.arrayValue ?? []
                if rows.isEmpty {
                    Text("No decisions logged yet. The engine writes a row for every bet placed and every candidate it considered.")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.vertical, 6)
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
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .monospacedDigit()
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
        }
        .buttonStyle(.dsIcon(side: 44))
        .disabled(!enabled)
        .accessibilityLabel(label)
    }

    /// One decision: crest, match, pick, the edge and the decision word
    /// ("Mock · " for paper); tap to expand the probabilities, the LLM
    /// rationale and any block reason.
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
            withAnimation(DSMotion.standard(reduceMotion: reduceMotion)) { model.toggleExpanded(i) }
        } label: {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    MarketsCrest(url: KalshiPregame.str(r["pick_logo"]), initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")), size: 32)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(match)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                            .lineLimit(1)
                        Text(pick)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .lineLimit(1)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    VStack(alignment: .trailing, spacing: 2) {
                        if let edge {
                            Text(KalshiFormat.signedEdge(edge))
                                .font(DS.Font.body)
                                .monospacedDigit()
                                .foregroundStyle(edge >= 0 ? DS.Palette.gain : DS.Palette.loss)
                        }
                        (Text(paper ? "Mock · " : "").foregroundStyle(DS.Text.muted) + Text(dec.dsSentenceCased).foregroundStyle(Self.decisionColor(dec)))
                            .font(DS.Font.caption)
                    }
                    Image(systemName: "chevron.down")
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Text.muted)
                        .rotationEffect(.degrees(open ? 180 : 0))
                        .accessibilityHidden(true)
                }
                if open {
                    MarketsFlowLayout(spacing: 16, runSpacing: 6) {
                        kv("Model", KalshiFormat.pct(r["model_prob"]?.double))
                        kv("Sharp", KalshiFormat.pct(r["sharp_prob"]?.double))
                        kv("LLM adj", KalshiFormat.pct(r["llm_adjustment"]?.double))
                        kv("Fair", KalshiFormat.pct(r["fused_fair"]?.double))
                        kv("Size", KalshiFormat.firstNonNull(r["size"], .int(0)))
                        if paper, let rc = r["realized_pnl_cents"], !rc.isNull {
                            kv("Paper P&L", "$\(dartToStringAsFixed((rc.double ?? 0) / 100, 2))")
                        }
                    }
                    if !rationale.isEmpty {
                        Text(rationale)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.label)
                            .multilineTextAlignment(.leading)
                            .lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(DS.Surface.raised, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                    }
                    if !blockReason.isEmpty {
                        Text("\(dec == "blocked" ? "Blocked: " : "Skipped — ")\(blockReason)")
                            .font(DS.Font.caption)
                            .foregroundStyle(dec == "blocked" ? DS.Palette.loss : DS.Text.muted)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityHint(open ? "Collapses the details" : "Shows the details")
    }

    private func kv(_ k: String, _ v: String) -> some View {
        (Text("\(k) ").foregroundStyle(DS.Text.muted) + Text(v).foregroundStyle(DS.Text.label))
            .font(DS.Font.caption)
            .monospacedDigit()
    }
}

/// A 4 pt progress bar (the web's `h-1` track): zinc-300 on the raised
/// track.
private struct KalshiBar: View {
    let fraction: Double

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(DS.Surface.raised)
                Capsule()
                    .fill(DS.Text.label)
                    .frame(width: geo.size.width * min(max(fraction, 0), 1))
            }
        }
        .frame(height: 4)
        .accessibilityHidden(true)
    }
}

/// The first load: the header line and two cards, as static blocks.
private struct KalshiDetailSkeleton: View {
    var body: some View {
        List {
            Section {
                Skeleton(width: 160, height: 14)
                    .dsClearRow(top: 0)
            }
            DSSection {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton(width: 110, height: 14)
                    Skeleton(width: 150, height: 24)
                    Skeleton(height: 160)
                }
                .padding(.vertical, 12)
            }
            DSSection {
                Skeleton(height: 96)
                    .padding(.vertical, 12)
            }
        }
        .dsList()
        .allowsHitTesting(false)
        .accessibilityLabel("Loading instance")
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
            let color = values.last! >= 0 ? DS.Palette.gain : DS.Palette.loss
            context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 1.25, lineCap: .round, lineJoin: .round))
        }
        .chartDrawIn(duration: ChartDrawIn.sparkDuration, bleed: 2)
        .accessibilityHidden(true)
    }
}
