import SwiftUI

/// Symbol search — `SymbolSearchScreen` in `symbol_search_screen.dart`.
/// The custom field becomes the system search bar (focused on appear); the
/// results are an inset-grouped list priced from today's history.
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
            .onAppear { searchPresented = true }
    }

    @ViewBuilder
    private var content: some View {
        if model.loading, model.results == nil {
            skeletonList
        } else if let error = model.error {
            errorState(error)
        } else if let results = model.results {
            if results.isEmpty {
                centered("No matching symbols")
            } else {
                List {
                    ForEach(Array(results.enumerated()), id: \.offset) { _, r in
                        resultRow(r)
                    }
                }
                .listStyle(.insetGrouped)
                .scrollContentBackground(.hidden)
            }
        } else {
            centered("Find an investment")
        }
    }

    private func centered(_ text: String) -> some View {
        Text(text)
            .font(.body)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func resultRow(_ r: SearchInstrument) -> some View {
        let quote = model.quotes?[r.symbol]
        return Button {
            services.router.push(.stock(StockRoute(symbol: r.symbol)))
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(r.symbol)
                            .font(.body.weight(.heavy))
                            .lineLimit(1)
                        if let pct = quote?.changePct {
                            Text(fmtPct(pct))
                                .font(.footnote.weight(.bold).monospacedDigit())
                                .foregroundStyle(pnlColor(pct))
                        }
                    }
                    Text(r.name)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if model.quotesLoading {
                    Skeleton(width: 70, height: 16, radius: 4)
                } else {
                    Text(fmtMoney(quote?.price))
                        .font(.body.weight(.bold).monospacedDigit())
                        .multilineTextAlignment(.trailing)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }

    private var skeletonList: some View {
        List {
            ForEach(0..<6, id: \.self) { _ in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 7) {
                        Skeleton(width: 72, height: 15, radius: 4)
                        Skeleton(width: 160, height: 12, radius: 4)
                    }
                    Spacer()
                    Skeleton(width: 70, height: 16, radius: 4)
                }
                .padding(.vertical, 2)
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .accessibilityLabel("Loading")
    }

    private func errorState(_ message: String) -> some View {
        VStack {
            Card(padding: EdgeInsets(top: 28, leading: 24, bottom: 24, trailing: 24)) {
                VStack(alignment: .leading, spacing: 0) {
                    IconTile(
                        systemImage: dashboardSymbol("query_stats", fallback: "chart.bar.xaxis.ascending"),
                        color: DS.Palette.accent,
                        size: 48
                    )
                    Text("MARKET SEARCH")
                        .font(.footnote.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(.tint)
                        .padding(.top, 22)
                    Text("We’re reconnecting")
                        .font(.title3.bold())
                        .padding(.top, 6)
                    Text(searchUnavailableMessage(message))
                        .font(.body)
                        .foregroundStyle(.secondary)
                        .padding(.top, 8)
                    Button {
                        model.retry()
                    } label: {
                        Label("Retry Search", systemImage: Symbol.named("refresh"))
                            .frame(maxWidth: .infinity)
                    }
                    .dsProminentButton()
                    .controlSize(.large)
                    .padding(.top, 24)
                }
            }
            .frame(maxWidth: 360)
            .padding(.horizontal, 24)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
