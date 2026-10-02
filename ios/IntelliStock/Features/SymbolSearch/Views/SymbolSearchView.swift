import SwiftUI

/// Symbol search — `SymbolSearchScreen` in `symbol_search_screen.dart`.
/// The custom field becomes the system search bar (focused on appear); the
/// results are an inset-grouped list priced from today's history, each row
/// Stocks style: the ticker over the name, the price over today's move.
struct SymbolSearchView: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        SymbolSearchContent(services: services)
    }
}

private struct SymbolSearchContent: View {
    let services: AppServices

    @State private var model: SymbolSearchModel
    @State private var text = ""
    @State private var searchPresented = false
    /// Flutter's `autofocus`: the first appear only, not every return from a
    /// result.
    @State private var autofocused = false

    init(services: AppServices) {
        self.services = services
        _model = State(initialValue: SymbolSearchModel(
            search: { [unowned services] in try await services.symbolSearchRepository.search($0) },
            historicals: { [unowned services] in try await services.liveRepository.symbolHistoricals($0, $1) }
        ))
    }

    var body: some View {
        content
            .background(DS.Surface.canvas)
            .navigationTitle("Search")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(
                text: $text,
                isPresented: $searchPresented,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Search stocks, ETFs, crypto"
            )
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .onChange(of: text) { _, new in model.onQueryChanged(new) }
            .onAppear {
                guard !autofocused else { return }
                autofocused = true
                searchPresented = true
            }
    }

    @ViewBuilder
    private var content: some View {
        if model.loading, model.results == nil {
            skeletonList
        } else if let error = model.error {
            errorState(error)
        } else if let results = model.results {
            if results.isEmpty {
                ContentUnavailableView("No matching symbols", systemImage: "magnifyingglass")
            } else {
                List {
                    ForEach(Array(results.enumerated()), id: \.offset) { _, r in
                        resultRow(r)
                    }
                }
                .listStyle(.insetGrouped)
            }
        } else {
            ContentUnavailableView("Find an investment", systemImage: "magnifyingglass")
        }
    }

    /// One result. The row opens the stock screen.
    private func resultRow(_ r: SearchInstrument) -> some View {
        let quote = model.quotes?[r.symbol]
        return NavigationLink(value: Route.stock(StockRoute(symbol: r.symbol))) {
            EntityRow(r.symbol, subtitle: r.name) {
                if model.quotesLoading {
                    EntityRowValue("$000.00", detail: "+0.00%")
                        .redacted(reason: .placeholder)
                } else {
                    EntityRowValue(
                        fmtMoney(quote?.price),
                        detail: quote?.changePct.map { fmtPct($0) },
                        detailColor: quote?.changePct.map { pnlColor($0) }
                    )
                }
            }
        }
    }

    private var skeletonList: some View {
        List {
            ForEach(0..<6, id: \.self) { _ in
                EntityRow("TICK", subtitle: "Instrument name placeholder") {
                    EntityRowValue("$000.00", detail: "+0.00%")
                }
                .redacted(reason: .placeholder)
            }
        }
        .listStyle(.insetGrouped)
        .accessibilityLabel("Loading")
    }

    private func errorState(_ message: String) -> some View {
        ContentUnavailableView {
            Label("We’re reconnecting", systemImage: dashboardSymbol("query_stats", fallback: "chart.bar.xaxis.ascending"))
        } description: {
            Text(searchUnavailableMessage(message))
        } actions: {
            Button {
                model.retry()
            } label: {
                Label("Retry Search", systemImage: Symbol.named("refresh"))
            }
            .dsProminentButton()
        }
    }
}
