import SwiftUI

/// The Strategies tab — `StrategiesScreen`: the agent's top-5, every
/// strategy with its best backtest, sorting and paging.
struct StrategiesView: View {
    @Environment(AppServices.self) private var services
    @Environment(\.colorScheme) private var colorScheme
    @State private var model: StrategiesModel?

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Strategies")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                if model?.loading == true {
                    ProgressView()
                } else {
                    Button {
                        Task { await model?.fetchAll() }
                    } label: {
                        Label("Refresh", systemImage: Symbol.named("refresh"))
                    }
                }
            }
        }
        .task {
            if model == nil {
                model = StrategiesModel(repository: { [services] in services.strategyRepository })
            }
            if let model, model.needsLoad { await model.fetchAll() }
        }
    }

    private func content(_ model: StrategiesModel) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                Text("All trading strategies with their best AI backtest results.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                HStack(spacing: 8) {
                    Button {
                        // Flutter pushed /instances?createStrategy=1: the
                        // Instances tab.
                        services.router.go("/instances")
                    } label: {
                        Label("Create Strategy", systemImage: Symbol.named("add_circle"))
                            .fontWeight(.semibold)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(DS.Palette.accent)
                    Picker("Per page", selection: Binding(get: { model.perPage }, set: { model.setPerPage($0) })) {
                        ForEach(StrategiesModel.perPageOptions, id: \.self) { Text("\($0)/page").tag($0) }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                }
                .padding(.bottom, 10)

                if model.loading {
                    ForEach(0..<3, id: \.self) { _ in skeletonTop5 }
                    ForEach(0..<5, id: \.self) { _ in skeletonRow }
                } else {
                    let top5 = model.top5Enriched
                    if !top5.isEmpty {
                        HStack(spacing: 6) {
                            Image(systemName: Symbol.named("emoji_events")).foregroundStyle(DS.Palette.warning)
                            Text("TOP \(top5.count) BEST STRATEGIES")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(DS.Palette.onTint(DS.Palette.warning, in: colorScheme))
                            Text("ranked by P&L%").font(.caption2).foregroundStyle(.tertiary)
                        }
                        ForEach(top5) { e in top5Card(e) }
                            .padding(.bottom, 0)
                        Spacer().frame(height: 10)
                    }
                    sortBar(model)
                    let paged = model.pagedRows
                    if paged.isEmpty {
                        EmptyState(
                            systemImage: Symbol.named("schema"),
                            title: "No strategies found.",
                            subtitle: "Create your first strategy to get started."
                        )
                    } else {
                        ForEach(paged) { row in strategyCard(row) }
                    }
                    pagination(model)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .refreshable { await model.fetchAll() }
    }

    // MARK: Top 5

    private func top5Card(_ e: StrategiesModel.Top5Entry) -> some View {
        let accent = StrategyRank.accent(e.rank)
        let text = StrategyRank.text(e.rank, in: colorScheme)
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 10) {
                    rankTile(e.rank, size: 38, showMedal: e.rank <= 3)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(e.name).font(.subheadline.weight(.semibold)).foregroundStyle(text).lineLimit(1)
                            Spacer(minLength: 0)
                            MarketsTag(text: "RANK \(e.rank)", color: accent)
                        }
                        Text("\(e.subs.count) sub-strategies").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                HStack(spacing: 20) {
                    miniStat("BEST P&L", fmtPnl(e.pnl), pnlColor(e.pnl))
                    miniStat("BEST P&L%", fmtPct(e.pct), pnlColor(e.pct))
                }
                if !e.subs.isEmpty {
                    MarketsFlowLayout(spacing: 4, runSpacing: 4) {
                        ForEach(Array(e.subs.prefix(5).enumerated()), id: \.offset) { _, s in
                            MarketsChip(text: s, color: text, tint: accent)
                        }
                        if e.subs.count > 5 {
                            Text("+\(e.subs.count - 5) more").font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
                HStack(spacing: 8) {
                    if let sid = e.strategyId {
                        Button {
                            services.router.push(.strategy(sid.dartDescription))
                        } label: {
                            Label("View Strategy", systemImage: Symbol.named("open_in_new")).font(.caption.weight(.semibold))
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .tint(accent)
                    }
                    if let bid = e.backtestId {
                        Button {
                            services.router.push(.backtest(bid.dartDescription))
                        } label: {
                            Label("Backtest", systemImage: Symbol.named("analytics")).font(.caption.weight(.semibold))
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .tint(.secondary)
                    }
                }
            }
        }
    }

    private func miniStat(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption2).tracking(0.8).foregroundStyle(.tertiary)
            Text(value).font(.headline.monospacedDigit()).foregroundStyle(color)
        }
    }

    private func rankTile(_ rank: Int, size: CGFloat, showMedal: Bool) -> some View {
        let accent = StrategyRank.accent(rank)
        return Group {
            if showMedal {
                Text(StrategyRank.medal(rank)).font(size > 36 ? .title3 : .body)
            } else {
                Text("#\(rank)").font(.footnote.weight(.bold)).foregroundStyle(StrategyRank.text(rank, in: colorScheme))
            }
        }
        .frame(width: size, height: size)
        .background(accent.opacity(DS.tintFill), in: .rect(cornerRadius: 10, style: .continuous))
        .accessibilityLabel("Rank \(rank)")
    }

    // MARK: Sort bar

    private func sortBar(_ model: StrategiesModel) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                Text("Sort:").font(.caption2).foregroundStyle(.tertiary)
                ForEach([(StrategySortField.name, "Name"), (.bestPnl, "Best P&L"), (.bestPct, "Best P&L%"), (.backtests, "Backtests")], id: \.0) { field, label in
                    let active = model.sortField == field
                    Button {
                        model.setSort(field)
                    } label: {
                        HStack(spacing: 3) {
                            Text(label)
                            if active {
                                Image(systemName: Symbol.named(model.sortAsc ? "arrow_upward" : "arrow_downward"))
                                    .font(.caption2)
                            }
                        }
                        .font(.caption.weight(active ? .semibold : .regular))
                        .foregroundStyle(active ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(active ? DS.Palette.accent.opacity(DS.tintFill) : DS.Surface.panel, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(active ? .isSelected : [])
                }
            }
        }
        .padding(.bottom, 2)
    }

    // MARK: Strategy card

    private func strategyCard(_ row: StrategyListRow) -> some View {
        let rankText = row.rank.map { StrategyRank.text($0, in: colorScheme) }
        return Button {
            services.router.push(.strategy(String(row.id)))
        } label: {
            Card(padding: 14) {
                HStack(spacing: 10) {
                    if let rank = row.rank {
                        rankTile(rank, size: 34, showMedal: rank <= 3)
                    } else {
                        Image(systemName: Symbol.named("schema"))
                            .foregroundStyle(.tertiary)
                            .frame(width: 34, height: 34)
                            .background(DS.Surface.inset, in: .rect(cornerRadius: 8, style: .continuous))
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(row.name)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(rankText ?? .primary)
                                .lineLimit(1)
                            if let rank = row.rank {
                                MarketsTag(text: "RANK \(rank)", color: StrategyRank.accent(rank))
                            }
                        }
                        HStack(spacing: 8) {
                            Text("ID \(row.id)")
                            Text("\(row.subCount) subs")
                            if row.runCount > 0 { Text("\(row.runCount) runs") }
                        }
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                    }
                    Spacer(minLength: 8)
                    VStack(alignment: .trailing, spacing: 0) {
                        if let pnl = row.bestPnl {
                            Text(fmtPnl(pnl)).font(.footnote.monospaced().weight(.semibold)).foregroundStyle(pnlColor(pnl))
                        }
                        if let pct = row.bestPct {
                            Text(fmtPct(pct)).font(.caption.monospaced().weight(.semibold)).foregroundStyle(pnlColor(pct))
                        }
                        if row.bestPnl == nil && row.bestPct == nil {
                            Text("—").font(.footnote).foregroundStyle(.tertiary)
                        }
                    }
                    if let bid = row.bestPnlBid {
                        Button {
                            services.router.push(.backtest(bid))
                        } label: {
                            Image(systemName: Symbol.named("analytics"))
                                .font(.footnote)
                                .frame(width: 44, height: 44)
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Best backtest")
                    }
                }
            }
        }
        .buttonStyle(.plain)
    }

    // MARK: Pagination

    @ViewBuilder
    private func pagination(_ model: StrategiesModel) -> some View {
        let total = model.rows.count
        let pages = model.totalPages
        if pages <= 1 {
            Text("\(total) strategies").font(.caption2).foregroundStyle(.tertiary).padding(.top, 4)
        } else {
            HStack(spacing: 4) {
                Text("\(total) strategies · page \(model.page) of \(pages)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
                pageButton("arrow_back", "Previous page", enabled: model.page > 1) { model.setPage(model.page - 1) }
                ForEach(StrategiesModel.pageWindow(page: model.page, totalPages: pages), id: \.self) { p in
                    let active = p == model.page
                    Button {
                        model.setPage(p)
                    } label: {
                        Text("\(p)")
                            .font(.caption.weight(active ? .semibold : .regular).monospacedDigit())
                            .foregroundStyle(active ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                            .frame(minWidth: 28, minHeight: 28)
                            .background(active ? DS.Palette.accent.opacity(DS.tintFill) : .clear, in: .rect(cornerRadius: 6, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(active ? .isSelected : [])
                }
                pageButton("arrow_forward", "Next page", enabled: model.page < pages) { model.setPage(model.page + 1) }
            }
            .padding(.top, 4)
        }
    }

    private func pageButton(_ icon: String, _ label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon)).font(.caption).frame(width: 28, height: 28)
        }
        .buttonStyle(.bordered)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }

    // MARK: Skeletons

    private var skeletonTop5: some View {
        Card(padding: 14) {
            VStack(alignment: .leading, spacing: 12) {
                HStack { Text("🥇"); Text("Strategy name placeholder"); Spacer(); Text("RANK 1") }
                HStack(spacing: 20) { Text("+$1,234.56"); Text("+12.34%") }
                Text("graph_nexus_analysis  momentum")
            }
        }
        .redacted(reason: .placeholder)
    }

    private var skeletonRow: some View {
        Card(padding: 14) {
            HStack { Text("Strategy name"); Spacer(); Text("+$123.45") }
        }
        .redacted(reason: .placeholder)
    }
}
