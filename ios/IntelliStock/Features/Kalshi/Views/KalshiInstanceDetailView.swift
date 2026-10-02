import SwiftUI

/// A Kalshi instance — `KalshiInstanceDetailScreen`: status, Start / Stop,
/// decision summary, portfolio, live matches, orders, pregame analysis, the
/// LLM-reasoned decision log and live logs.
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
                LoadingState().padding(24).frame(maxHeight: .infinity, alignment: .top)
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
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task {
                        if let message = await model.startStop(!running) {
                            toast = Toast(message, style: .error)
                        }
                    }
                } label: {
                    Label(running ? "Stop" : "Start", systemImage: Symbol.named(running ? "pause" : "play_arrow"))
                        .labelStyle(.titleAndIcon)
                }
                .tint(running ? DS.Palette.warning : DS.Palette.accent)
                .disabled(model.busy)
            }
            ToolbarItem(placement: .primaryAction) {
                Menu {
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
                                if !marketsIsCancellation(error) { toast = Toast(KalshiFormat.errorText(error), style: .error) }
                            }
                        )
                    } label: {
                        Label("Delete", systemImage: Symbol.named("delete"))
                    }
                    .disabled(model.busy)
                } label: {
                    Label("More", systemImage: "ellipsis.circle")
                }
            }
        }
    }

    // MARK: Content

    private func content(_ model: KalshiInstanceDetailModel, detail: JSONObject) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    MarketsTag(text: model.running ? "Running" : "Stopped", color: model.running ? DS.Palette.success : .secondary)
                    MarketsTag(text: model.isLive ? "Live" : "Paper", color: model.isLive ? DS.Palette.danger : DS.Palette.accent)
                }
                summary(model.decisions.value?["summary"]?.orderedObject)
                if !model.brokerageId.isEmpty {
                    KalshiPortfolioHero(
                        title: "PORTFOLIO VALUE",
                        state: model.portfolio,
                        onRetry: { Task { await model.reloadPortfolio() } }
                    )
                }
                liveCards(model.live.value)
                positionsCard(model.positions?.value ?? [])
                ordersCard(model.orders.value ?? JSONObject())
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    pregameAnalysis(model, now: context.date)
                }
                decisionLog(model)
                Card(padding: 14) {
                    VStack(alignment: .leading, spacing: 10) {
                        MarketsCardHeader(icon: "terminal", title: "Live logs")
                        LiveLogsPanel(instanceId: instanceId)
                            .id(instanceId)
                            .frame(height: 300)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 24)
        }
        .refreshable { await model.refresh() }
    }

    // MARK: Summary

    private func summary(_ s: JSONObject?) -> some View {
        func count(_ k: String) -> String {
            guard let v = s?[k], !v.isNull else { return "0" }
            return v.dartDescription
        }
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                StatTile(label: "Placed", value: count("placed"), valueColor: DS.Palette.success)
                StatTile(label: "Skipped", value: count("skipped"))
                StatTile(label: "Queued", value: count("queued"), valueColor: DS.Palette.warning)
                StatTile(label: "Blocked", value: count("blocked"), valueColor: DS.Palette.danger)
            }
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
            HStack(spacing: 6) {
                Image(systemName: Symbol.named("science"))
                    .foregroundStyle(DS.Palette.warning)
                Text("Paper P&L")
                    .fontWeight(.bold)
                    .foregroundStyle(.secondary)
                let realized = Text("realized \(realC.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")").foregroundStyle(realColor).fontWeight(.semibold)
                let dot = Text("  ·  ").foregroundStyle(.tertiary)
                let unrealized = Text("unrealized \(unrealC.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")").foregroundStyle(unrealColor).fontWeight(.semibold)
                let live = Text(openPos.map { " (live · \($0) open)" } ?? " (live)").foregroundStyle(.tertiary)
                Text("\(realized)\(dot)\(unrealized)\(live)")
            }
            .font(.caption)
            .lineLimit(1)
        }
    }

    // MARK: Live matches

    @ViewBuilder
    private func liveCards(_ live: JSONObject?) -> some View {
        let matches = live?["matches"]?.arrayValue ?? []
        if !matches.isEmpty {
            Card(padding: 14) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Circle().fill(DS.Palette.danger).frame(width: 8, height: 8)
                        Text("LIVE NOW · \(matches.count)")
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }
                    ForEach(Array(matches.enumerated()), id: \.offset) { _, raw in
                        liveCard(raw.orderedObject ?? JSONObject())
                    }
                }
            }
        }
    }

    private func liveCard(_ m: JSONObject) -> some View {
        let score = m["score"]?.orderedObject
        let probs = m["market_probs"]?.orderedObject ?? JSONObject()
        let decisions = m["decisions"]?.arrayValue ?? []
        let news = m["news"]?.string ?? ""
        let clock: String = {
            if let c = score?["clock"], !c.isNull, !c.dartDescription.isEmpty { return c.dartDescription }
            if let e = m["elapsed_min"]?.double { return "\(Int(e.rounded()))'" }
            return "LIVE"
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
                    HStack(spacing: 5) {
                        Circle().fill(DS.Palette.success).frame(width: 6, height: 6)
                        Text(clock)
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(DS.Palette.success)
                    }
                }
                .frame(maxWidth: .infinity)
                teamBadge(logo: KalshiPregame.str(m["away_logo"]), name: away)
            }
            ForEach(probs.entries, id: \.key) { e in
                let v = min(max(e.value.double ?? 0, 0), 1)
                HStack(spacing: 8) {
                    Text(sideLabel(m, e.key))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .frame(width: 72, alignment: .leading)
                    ProgressView(value: v)
                        .tint(DS.Palette.accent)
                    Text("\(Int((v * 100).rounded()))%")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            if !news.isEmpty {
                Text(news.components(separatedBy: "\n").first ?? "")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            if !decisions.isEmpty {
                MarketsFlowLayout {
                    ForEach(Array(decisions.prefix(4).enumerated()), id: \.offset) { _, d in
                        let act = KalshiPregame.str(d["action"])
                        let size = d["size"]
                        let sz = (!size.isNull && size != .int(0) && size != .double(0)) ? " \(size.dartDescription)" : ""
                        MarketsTag(text: "\(act.uppercased())\(sz)", color: actionColor(act))
                    }
                }
            }
        }
        .padding(12)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
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
                .font(.caption2)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .frame(width: 76)
    }

    // MARK: Positions and orders

    @ViewBuilder
    private func positionsCard(_ positions: [KalshiPosition]) -> some View {
        if !positions.isEmpty {
            Card(padding: 14) {
                VStack(alignment: .leading, spacing: 10) {
                    MarketsCardHeader(icon: "account_balance_wallet", title: "Open positions · \(positions.count)")
                    ForEach(Array(positions.enumerated()), id: \.offset) { _, p in
                        KalshiPositionTile(position: p)
                    }
                }
            }
        }
    }

    private func ordersCard(_ data: JSONObject) -> some View {
        let placed = data["placed"]?.arrayValue ?? []
        let fills = data["fills"]?.arrayValue ?? []
        let mock = data["mock"]?.arrayValue ?? []
        let mockHistory = data["mock_history"]?.arrayValue ?? []
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 6) {
                MarketsCardHeader(icon: "receipt_long", title: "Orders")
                    .padding(.bottom, 4)
                subHeader("pending", "PENDING · \(placed.count)", DS.Palette.warning)
                if placed.isEmpty {
                    emptyNote("No resting orders — everything filled.")
                }
                ForEach(Array(placed.prefix(12).enumerated()), id: \.offset) { _, o in
                    orderTile(o.orderedObject ?? JSONObject(), filled: false)
                }
                if !fills.isEmpty {
                    subHeader("check_circle_outline", "FILLED · \(fills.count)", DS.Palette.success).padding(.top, 6)
                    ForEach(Array(fills.prefix(12).enumerated()), id: \.offset) { _, f in
                        orderTile(f.orderedObject ?? JSONObject(), filled: true)
                    }
                }
                subHeader("science", "MOCK POSITIONS · \(mock.count)", DS.Palette.warning).padding(.top, 6)
                if mock.isEmpty {
                    emptyNote("No mock (paper) positions.")
                }
                ForEach(Array(mock.prefix(12).enumerated()), id: \.offset) { _, m in
                    mockTile(m.orderedObject ?? JSONObject())
                }
                if !mockHistory.isEmpty {
                    subHeader("history", "MOCK FILLED · \(mockHistory.count)", DS.Palette.warning).padding(.top, 6)
                    ForEach(Array(mockHistory.prefix(12).enumerated()), id: \.offset) { _, m in
                        mockHistoryTile(m.orderedObject ?? JSONObject())
                    }
                }
            }
        }
    }

    private func subHeader(_ icon: String, _ text: String, _ color: Color) -> some View {
        HStack(spacing: 5) {
            Image(systemName: Symbol.named(icon)).foregroundStyle(color)
            Text(text).fontWeight(.bold).foregroundStyle(.secondary)
        }
        .font(.caption2)
    }

    private func emptyNote(_ text: String) -> some View {
        Text(text).font(.caption).foregroundStyle(.secondary)
    }

    private func tileBackground<V: View>(@ViewBuilder _ content: () -> V) -> some View {
        content()
            .padding(11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Surface.inset, in: .rect(cornerRadius: 10, style: .continuous))
    }

    private func orderTile(_ o: JSONObject, filled: Bool) -> some View {
        let match = KalshiPregame.str(o["match"], o["market_ticker"])
        let pick = KalshiPregame.str(o["pick_label"], o["side"])
        let edge = o["edge"]?.double
        return tileBackground {
            HStack(spacing: 10) {
                MarketsCrest(url: KalshiPregame.str(o["pick_logo"]), initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")), size: 30)
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(match).font(.subheadline.weight(.semibold)).lineLimit(1)
                        Spacer(minLength: 4)
                        if !filled, o["in_play"] == .bool(true) {
                            Text("LIVE").font(.caption2.weight(.bold)).foregroundStyle(DS.Palette.danger)
                        }
                        if filled {
                            Text(KalshiPregame.str(o["action"]).uppercased())
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(o["action"] == .string("sell") ? DS.Palette.warning : DS.Palette.success)
                        }
                    }
                    HStack {
                        Text(pick).font(.caption2.weight(.medium)).foregroundStyle(.tint).lineLimit(1)
                        Spacer(minLength: 4)
                        if filled {
                            Text("\(dartString(o["contracts"])) @ \(dartString(o["price_cents"]))¢")
                                .font(.caption2).foregroundStyle(.secondary)
                        } else {
                            Text("\(KalshiFormat.firstNonNull(o["contracts"], o["size"], .int(0)))×  ")
                                .font(.caption2).foregroundStyle(.secondary)
                            if let edge {
                                Text(KalshiFormat.signedEdge(edge))
                                    .font(.caption2.weight(.semibold))
                                    .foregroundStyle(edge >= 0 ? DS.Palette.success : DS.Palette.danger)
                            }
                        }
                    }
                }
            }
        }
    }

    /// `'${x}'` interpolation of a JSON value (null prints "null").
    private func dartString(_ v: JSON?) -> String {
        (v ?? .null).dartDescription
    }

    /// Settled / expired paper trade: realized P&L + MOCK tag.
    private func mockHistoryTile(_ m: JSONObject) -> some View {
        let match = KalshiPregame.str(m["match"], m["market_ticker"])
        let pick = KalshiPregame.str(m["pick_label"], m["side"])
        let contracts = m["contracts"]?.double.map { Int($0) } ?? 0
        let entryCents = m["price_cents"]?.double.map { Int($0) }
        let rCents = m["realized_pnl_cents"]?.double
        let rPos = (rCents ?? 0) >= 0
        let outcome = KalshiPregame.str(m["outcome"])
        return tileBackground {
            HStack(spacing: 10) {
                MarketsCrest(url: KalshiPregame.str(m["pick_logo"]), initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")), size: 30)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(match).font(.subheadline.weight(.semibold)).lineLimit(1)
                        Spacer(minLength: 0)
                        MarketsTag(text: "MOCK", color: DS.Palette.warning)
                        Text(rCents.map { KalshiFormat.signedDollars(cents: $0) } ?? "—")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(rCents == nil ? Color.secondary : (rPos ? DS.Palette.success : DS.Palette.danger))
                    }
                    HStack {
                        Text(pick).font(.caption2.weight(.medium)).foregroundStyle(.tint).lineLimit(1)
                        Spacer(minLength: 4)
                        Text("\(contracts) @ \(entryCents.map(String.init) ?? "—")¢\(outcome.isEmpty ? "" : " · \(outcome)")")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    /// Paper position: live unrealized P&L plus the entry → mark trail.
    private func mockTile(_ m: JSONObject) -> some View {
        let match = KalshiPregame.str(m["match"], m["market_ticker"])
        let pick = KalshiPregame.str(m["pick_label"], m["side"])
        let contracts = m["contracts"]?.double.map { Int($0) } ?? 0
        let entryCents = m["entry_cents"]?.double.map { Int($0) }
        let markCents = m["mark_cents"]?.double.map { Int($0) }
        let upCents = m["unrealized_pnl_cents"]?.double
        let upPos = (upCents ?? 0) > 0
        // Total current value = contracts × current mark (fallback entry).
        let value = Double(contracts * (markCents ?? entryCents ?? 0)) / 100
        return tileBackground {
            HStack(spacing: 10) {
                MarketsCrest(url: KalshiPregame.str(m["pick_logo"]), initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")), size: 30)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(match).font(.subheadline.weight(.semibold)).lineLimit(1)
                        Spacer(minLength: 0)
                        MarketsTag(text: "MOCK", color: DS.Palette.warning)
                        Text("$\(dartToStringAsFixed(value, 2))")
                            .font(.headline.monospacedDigit())
                    }
                    HStack {
                        Text(pick).font(.caption2.weight(.medium)).foregroundStyle(.tint).lineLimit(1)
                        Spacer(minLength: 4)
                        Text(upCents.map { "\(upPos ? "+" : "-")$\(dartToStringAsFixed(abs($0) / 100, 2)) P&L" } ?? "—")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(upCents == nil ? Color.secondary : (upPos ? DS.Palette.success : DS.Palette.danger))
                    }
                    Text("\(contracts) @ \(entryCents.map(String.init) ?? "—")¢ → \(markCents.map(String.init) ?? "—")¢")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }

    // MARK: Pregame analysis

    private func pregameAnalysis(_ model: KalshiInstanceDetailModel, now: Date) -> some View {
        Card(padding: 14) {
            VStack(alignment: .leading, spacing: 12) {
                MarketsCardHeader(icon: "sports_soccer", title: "Pregame analysis")
                switch model.decisions {
                case .loading:
                    LoadingState()
                case .failed(let e):
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.refresh() } })
                case .loaded:
                    if model.pregameRowsEmpty {
                        Text("No games analyzed yet — picks will appear here once the bot scans the slate.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(spacing: 10) {
                            ForEach(Array(model.pregameGames.enumerated()), id: \.offset) { _, sides in
                                pregameCard(sides, now: now)
                            }
                        }
                    }
                }
            }
        }
    }

    private func pregameCard(_ sides: [JSONObject], now: Date) -> some View {
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
            HStack(spacing: 10) {
                MarketsCrest(url: KalshiPregame.str(head["pick_logo"]), initials: KalshiFormat.initials(home.isEmpty ? title : home), size: 30)
                HStack(spacing: 6) {
                    Text(title).font(.subheadline.weight(.bold)).lineLimit(1)
                    if !cd.isEmpty {
                        Text(cd).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 8)
                MarketsTag(text: "\(bestPos ? "+" : "")\(dartToStringAsFixed(best * 100, 1))% edge", color: bestPos ? DS.Palette.success : DS.Palette.danger)
            }
            if !parts.isEmpty {
                Text(parts.joined(separator: "  ·  "))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(.top, 6)
            }
            ForEach(Array(shown.enumerated()), id: \.offset) { _, r in
                pregameSideRow(r, now: now)
            }
            .padding(.top, 2)
        }
        .padding(12)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
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
                    Text(pick).font(.caption.weight(.semibold)).foregroundStyle(.tint).lineLimit(1)
                    if modelOnly {
                        Text("model-only").font(.caption2).italic().foregroundStyle(.tertiary)
                    }
                }
                Spacer(minLength: 0)
                if spark.count >= 2 {
                    KalshiEdgeSparkline(values: spark)
                        .frame(width: 60, height: 16)
                }
                sidePill(dec)
            }
            HStack(spacing: 14) {
                metric("fair", fair.map { "\(dartToStringAsFixed($0 * 100, 0))%" } ?? "—")
                metric("price", price.map { "\($0)¢" } ?? "—")
                metric("edge", edge.map { "\(edgePos ? "+" : "")\(dartToStringAsFixed($0 * 100, 1))%" } ?? "—",
                       color: edge == nil ? nil : (edgePos ? DS.Palette.success : DS.Palette.danger))
                if !updated.isEmpty {
                    Spacer(minLength: 0)
                    Text("updated \(updated)").font(.caption2).foregroundStyle(.tertiary)
                }
            }
            if placed, let entryEdge {
                Text("placed @ \(KalshiFormat.signedEdge(entryEdge))")
                    .font(.caption2).italic()
                    .foregroundStyle(DS.Palette.success)
            }
        }
        .padding(.top, 8)
    }

    private func metric(_ label: String, _ value: String, color: Color? = nil) -> some View {
        let l = Text("\(label) ").foregroundStyle(.secondary)
        let v = Text(value).fontWeight(.semibold).foregroundStyle(color ?? .primary)
        return Text("\(l)\(v)").font(.caption2.monospacedDigit())
    }

    /// PLACED success, BLOCKED warning, anything else muted.
    private func sidePill(_ decision: String) -> some View {
        let d = decision.lowercased()
        let c: Color = d == "placed" ? DS.Palette.success : (d == "blocked" ? DS.Palette.warning : .secondary)
        return MarketsTag(text: decision.isEmpty ? "—" : decision.uppercased(), color: c)
    }

    // MARK: Decision log

    private func decisionLog(_ model: KalshiInstanceDetailModel) -> some View {
        Card(padding: 14) {
            VStack(alignment: .leading, spacing: 12) {
                MarketsCardHeader(icon: "hub", title: "Decision log")
                switch model.decisions {
                case .loading:
                    LoadingState()
                case .failed(let e):
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.refresh() } })
                case .loaded(let d):
                    let rows = d["decisions"]?.arrayValue ?? []
                    if rows.isEmpty {
                        Text("No decisions logged yet.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    } else {
                        let p = KalshiPregame.page(rows, requested: model.decPage)
                        VStack(spacing: 8) {
                            ForEach(Array(p.slice.enumerated()), id: \.offset) { j, r in
                                decisionCard(model, r.orderedObject ?? JSONObject(), index: p.start + j)
                            }
                            if p.pages > 1 {
                                HStack {
                                    pageButton("chevron_left", label: "Previous page", enabled: p.page > 0) { model.setPage(p.page - 1) }
                                    Spacer()
                                    Text("Page \(p.page + 1) / \(p.pages)")
                                        .font(.caption2.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                    pageButton("chevron_right", label: "Next page", enabled: p.page < p.pages - 1) { model.setPage(p.page + 1) }
                                }
                                .padding(.top, 8)
                            }
                        }
                    }
                }
            }
        }
    }

    private func pageButton(_ icon: String, label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon))
                .frame(width: 30, height: 30)
        }
        .buttonStyle(.bordered)
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

    private func decisionCard(_ model: KalshiInstanceDetailModel, _ r: JSONObject, index i: Int) -> some View {
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
                HStack(spacing: 10) {
                    MarketsCrest(url: KalshiPregame.str(r["pick_logo"]), initials: KalshiFormat.initials(pick.replacingOccurrences(of: " to win", with: "")), size: 30)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(match).font(.subheadline.weight(.semibold)).foregroundStyle(.primary).lineLimit(1)
                        Text(pick).font(.caption2).foregroundStyle(.tint).lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    VStack(alignment: .trailing, spacing: 2) {
                        if let edge {
                            Text(KalshiFormat.signedEdge(edge))
                                .font(.footnote.weight(.bold).monospacedDigit())
                                .foregroundStyle(edge >= 0 ? DS.Palette.success : DS.Palette.danger)
                        }
                        HStack(spacing: 4) {
                            if paper { MarketsTag(text: "MOCK", color: DS.Palette.warning) }
                            MarketsTag(text: dec.uppercased(), color: decColor(dec))
                        }
                    }
                    Image(systemName: Symbol.named(open ? "expand_less" : "expand_more"))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
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
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: Symbol.named("psychology")).foregroundStyle(.tint)
                            Text(rationale).foregroundStyle(.primary).multilineTextAlignment(.leading)
                        }
                        .font(.caption2)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                    }
                    if !blockReason.isEmpty {
                        Text("\(dec == "blocked" ? "Blocked: " : "Skipped — ")\(blockReason)")
                            .font(.caption2)
                            .foregroundStyle(dec == "blocked" ? DS.Palette.danger.opacity(0.85) : Color.secondary)
                            .multilineTextAlignment(.leading)
                    }
                }
            }
            .padding(12)
            .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityHint(open ? "Collapses the details" : "Shows the details")
    }

    private func kv(_ k: String, _ v: String) -> some View {
        let key = Text("\(k) ").foregroundStyle(.secondary)
        let value = Text(v).fontWeight(.semibold)
        return Text("\(key)\(value)").font(.caption2.monospacedDigit())
    }
}

/// The edge-over-time sparkline — `_EdgeSparkPainter`: the side's edge
/// history normalised into the box (flat → centre line), green when the
/// latest edge is ≥ 0, else red.
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
        .accessibilityHidden(true)
    }
}
