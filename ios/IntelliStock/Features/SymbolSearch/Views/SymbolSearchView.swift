import SwiftUI

/// Symbol search — the web's search palette as a screen: the system search
/// bar (focused on appear), then the results on one card, priced from
/// today's history, each row the web's: the ticker (14/500) over the name,
/// the price over today's move in gain or loss.
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
            .background(DS.Surface.page)
            .navigationTitle("Search")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(
                text: $text,
                isPresented: $searchPresented,
                placement: .navigationBarDrawer(displayMode: .always),
                prompt: "Search stocks, ETFs and crypto"
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
                quiet("No matching symbols", detail: "Try a ticker or a company name")
            } else {
                List {
                    DSSection("Stocks and ETFs") {
                        ForEach(Array(results.enumerated()), id: \.offset) { _, r in
                            resultRow(r)
                        }
                    }
                }
                .dsList()
                .entityRowTitleMedium()
            }
        } else {
            quiet("Find an investment", detail: "Search by ticker or company name")
        }
    }

    /// One result. The row opens the stock screen.
    private func resultRow(_ r: SearchInstrument) -> some View {
        let quote = model.quotes?[r.symbol]
        return NavigationLink(value: Route.stock(StockRoute(symbol: r.symbol))) {
            EntityRow(r.symbol, subtitle: r.name) {
                if model.quotesLoading {
                    VStack(alignment: .trailing, spacing: 6) {
                        Skeleton(width: 64, height: 14)
                        Skeleton(width: 44, height: 12)
                    }
                } else {
                    EntityRowValue(
                        fmtMoney(quote?.price),
                        detail: quote?.changePct.map { fmtPct($0) },
                        detailColor: quote?.changePct.map { dashboardChangeColor($0) }
                    )
                }
            }
        }
    }

    /// A quiet centred sentence (the web's empty state), at the top third.
    private func quiet(_ title: String, detail: String) -> some View {
        VStack(spacing: 4) {
            Text(title)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.label)
            Text(detail)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity)
        .padding(.top, 96)
        .frame(maxHeight: .infinity, alignment: .top)
    }

    /// Static skeleton rows shaped like results.
    private var skeletonList: some View {
        List {
            DSSection("Stocks and ETFs") {
                ForEach(0..<6, id: \.self) { _ in
                    HStack {
                        VStack(alignment: .leading, spacing: 6) {
                            Skeleton(width: 48, height: 14)
                            Skeleton(width: 160, height: 12)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 6) {
                            Skeleton(width: 64, height: 14)
                            Skeleton(width: 44, height: 12)
                        }
                    }
                }
            }
        }
        .dsList()
        .accessibilityLabel("Loading")
    }

    private func errorState(_ message: String) -> some View {
        VStack(spacing: 16) {
            VStack(spacing: 4) {
                Text("We’re reconnecting")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.label)
                Text(searchUnavailableMessage(message))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            Button("Retry search") { model.retry() }
                .buttonStyle(.dsSecondary)
        }
        .multilineTextAlignment(.center)
        .padding(.horizontal, 32)
        .frame(maxWidth: .infinity)
        .padding(.top, 96)
        .frame(maxHeight: .infinity, alignment: .top)
    }
}
