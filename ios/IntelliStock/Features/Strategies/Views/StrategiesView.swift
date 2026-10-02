import SwiftUI

/// The Strategies tab — `StrategiesScreen`: the agent's top-5, every
/// strategy with its best backtest, sorting and paging. An inset-grouped list
/// under a large title: sorting and page size are toolbar menus, Create
/// Strategy is the `+`, and each row's backtest button is a swipe action and
/// a context-menu item.
struct StrategiesView: View {
    @Environment(AppServices.self) private var services
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
            if let model {
                ToolbarItem(placement: .topBarTrailing) { sortMenu(model) }
                ToolbarItem(placement: .topBarTrailing) { pageMenu(model) }
            }
            ToolbarItem(placement: .topBarTrailing) {
                // Flutter pushed /instances?createStrategy=1: the Instances tab.
                ToolbarAddButton("Create Strategy") { services.router.go("/instances") }
            }
        }
        .task {
            if model == nil {
                model = StrategiesModel(repository: { [services] in services.strategyRepository })
            }
            if let model, model.needsLoad { await model.fetchAll() }
        }
    }

    // MARK: Toolbar

    private static let sortFields: [(StrategySortField, String)] = [
        (.name, "Name"), (.bestPnl, "Best P&L"), (.bestPct, "Best P&L%"), (.backtests, "Backtests"),
    ]

    /// The Dart sort chips as a menu: choosing the active field again flips
    /// its direction, shown by the arrow beside it (Files does the same).
    private func sortMenu(_ model: StrategiesModel) -> some View {
        Menu {
            ForEach(Self.sortFields, id: \.0) { field, label in
                Button {
                    model.setSort(field)
                } label: {
                    if model.sortField == field {
                        Label(label, systemImage: Symbol.named(model.sortAsc ? "arrow_upward" : "arrow_downward"))
                    } else {
                        Text(label)
                    }
                }
                .accessibilityAddTraits(model.sortField == field ? .isSelected : [])
            }
        } label: {
            Label("Sort", systemImage: "arrow.up.arrow.down")
        }
    }

    /// Per page and the page jump.
    private func pageMenu(_ model: StrategiesModel) -> some View {
        ToolbarMenu("Page Options") {
            Picker("Per Page", selection: Binding(get: { model.perPage }, set: { model.setPerPage($0) })) {
                ForEach(StrategiesModel.perPageOptions, id: \.self) { Text("\($0)/page").tag($0) }
            }
            .pickerStyle(.menu)
            if model.totalPages > 1 {
                // The Dart page buttons: the five-page window around the
                // current page.
                Picker("Go to Page", selection: Binding(get: { model.page }, set: { model.setPage($0) })) {
                    ForEach(StrategiesModel.pageWindow(page: model.page, totalPages: model.totalPages), id: \.self) { Text("Page \($0)").tag($0) }
                }
                .pickerStyle(.menu)
            }
        }
    }

    // MARK: Content

    private func content(_ model: StrategiesModel) -> some View {
        List {
            if model.loading {
                Section {
                    ForEach(0..<3, id: \.self) { _ in skeletonRow }
                }
                Section {
                    ForEach(0..<5, id: \.self) { _ in skeletonRow }
                }
            } else {
                let top5 = model.top5Enriched
                if !top5.isEmpty {
                    Section {
                        ForEach(top5) { e in top5Row(e) }
                    } header: {
                        Text("Top \(top5.count) best strategies")
                    } footer: {
                        Text("ranked by P&L%")
                    }
                }
                let paged = model.pagedRows
                if paged.isEmpty {
                    Section {
                        EmptyState(
                            systemImage: Symbol.named("schema"),
                            title: "No strategies found.",
                            subtitle: "Create your first strategy to get started."
                        )
                        .listRowBackground(Color.clear)
                    }
                } else {
                    Section {
                        ForEach(paged) { row in strategyRow(row) }
                    } footer: {
                        Text("All trading strategies with their best AI backtest results.")
                    }
                }
                pagination(model)
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.fetchAll() }
    }

    // MARK: Rows

    /// The rank: a medal for the top three, "#n" for the rest.
    private func rankMark(_ rank: Int) -> some View {
        Group {
            if rank <= 3 {
                Image(systemName: "medal.fill")
                    .font(.title3)
                    .foregroundStyle(StrategyRank.accent(rank))
            } else {
                Text("#\(rank)")
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
        .frame(width: EntityRowMetrics.iconSize)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Rank \(rank)")
    }

    private func pnlValue(_ pnl: Double?, _ pct: Double?) -> some View {
        Group {
            if pnl == nil && pct == nil {
                Text("—").foregroundStyle(.secondary)
            } else {
                EntityRowValue(
                    pnl.map(fmtPnl) ?? "—",
                    color: pnlColor(pnl),
                    detail: pct.map(fmtPct),
                    detailColor: pnlColor(pct)
                )
            }
        }
    }

    private func top5Row(_ e: StrategiesModel.Top5Entry) -> some View {
        let subs = e.subs.prefix(5).joined(separator: ", ") + (e.subs.count > 5 ? " +\(e.subs.count - 5) more" : "")
        let row = StrategyRowLabel(
            title: e.name,
            subtitle: e.subs.isEmpty ? "\(e.subs.count) sub-strategies" : "\(e.subs.count) sub-strategies · \(subs)"
        ) {
            rankMark(e.rank)
        } trailing: {
            pnlValue(e.pnl, e.pct)
        }
        let backtest: (() -> Void)? = e.backtestId.map { bid in { services.router.push(.backtest(bid.dartDescription)) } }
        return Group {
            if let sid = e.strategyId {
                NavigationLink(value: Route.strategy(sid.dartDescription)) { row }
            } else {
                row
            }
        }
        .swipeActions(edge: .trailing) {
            if let backtest {
                Button(action: backtest) {
                    Label("Backtest", systemImage: Symbol.named("analytics"))
                }
                .tint(DS.Palette.info)
            }
        }
        .contextMenu {
            if let sid = e.strategyId {
                Button {
                    services.router.push(.strategy(sid.dartDescription))
                } label: {
                    Label("View Strategy", systemImage: Symbol.named("open_in_new"))
                }
            }
            if let backtest {
                Button(action: backtest) {
                    Label("Backtest", systemImage: Symbol.named("analytics"))
                }
            }
        }
    }

    private func strategyRow(_ row: StrategyListRow) -> some View {
        let subtitle = "ID \(row.id) · \(row.subCount) subs" + (row.runCount > 0 ? " · \(row.runCount) runs" : "")
        let backtest: (() -> Void)? = row.bestPnlBid.map { bid in { services.router.push(.backtest(bid)) } }
        return NavigationLink(value: Route.strategy(String(row.id))) {
            StrategyRowLabel(title: row.name, subtitle: subtitle) {
                if let rank = row.rank {
                    rankMark(rank)
                } else {
                    Image(systemName: Symbol.named("schema"))
                        .font(.title3)
                        .foregroundStyle(.secondary)
                        .frame(width: EntityRowMetrics.iconSize)
                        .accessibilityHidden(true)
                }
            } trailing: {
                pnlValue(row.bestPnl, row.bestPct)
            }
        }
        .swipeActions(edge: .trailing) {
            if let backtest {
                Button(action: backtest) {
                    Label("Best Backtest", systemImage: Symbol.named("analytics"))
                }
                .tint(DS.Palette.info)
            }
        }
        .contextMenu {
            if let backtest {
                Button(action: backtest) {
                    Label("Best Backtest", systemImage: Symbol.named("analytics"))
                }
            }
        }
    }

    // MARK: Pagination

    @ViewBuilder
    private func pagination(_ model: StrategiesModel) -> some View {
        let total = model.rows.count
        let pages = model.totalPages
        if pages <= 1 {
            Section {
            } footer: {
                Text("\(total) strategies")
            }
        } else {
            Section {
                HStack {
                    pageButton("arrow_back", "Previous page", enabled: model.page > 1) { model.setPage(model.page - 1) }
                    Spacer()
                    Text("\(total) strategies · page \(model.page) of \(pages)")
                        .font(.footnote.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Spacer()
                    pageButton("arrow_forward", "Next page", enabled: model.page < pages) { model.setPage(model.page + 1) }
                }
            }
        }
    }

    private func pageButton(_ icon: String, _ label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
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

    // MARK: Skeleton

    private var skeletonRow: some View {
        EntityRow("Strategy name placeholder", subtitle: "ID 000 · 2 subs") {
            EntityRowValue("+$1,234.56", detail: "+12.34%")
        }
        .redacted(reason: .placeholder)
    }
}

/// A strategy row: the `EntityRow` layout with room for long names — the
/// title takes two lines before it truncates, since strategy names run long
/// beside a two-line P&L.
private struct StrategyRowLabel<Leading: View, Trailing: View>: View {
    let title: String
    let subtitle: String
    @ViewBuilder let leading: () -> Leading
    @ViewBuilder let trailing: () -> Trailing

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let stacked = dynamicTypeSize.isAccessibilitySize
        let layout = stacked
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
        layout {
            HStack(spacing: 12) {
                leading()
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.headline)
                        .lineLimit(stacked ? nil : 2)
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(stacked ? nil : 1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            trailing()
                .layoutPriority(1)
        }
        .accessibilityElement(children: .combine)
    }
}
