import SwiftUI

/// Picks a symbol for the analysis screen: the app's symbol search (same
/// model, same rows as Search) in a sheet, returning the ticker instead of
/// opening the stock screen. Return on the keyboard takes the typed ticker
/// as is, for when search is down.
struct AnalysisSymbolSearchSheet: View {
    let services: AppServices
    let onPick: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var model: SymbolSearchModel
    @State private var text = ""
    @State private var searchPresented = true

    init(services: AppServices, onPick: @escaping (String) -> Void) {
        self.services = services
        self.onPick = onPick
        _model = State(initialValue: SymbolSearchModel(
            search: { [unowned services] in try await services.symbolSearchRepository.search($0) },
            historicals: { [unowned services] in try await services.liveRepository.symbolHistoricals($0, $1) }
        ))
    }

    var body: some View {
        NavigationStack {
            content
                .background(DS.Surface.page)
                .navigationTitle("Choose a symbol")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }
                    }
                }
                .searchable(
                    text: $text,
                    isPresented: $searchPresented,
                    placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Ticker or company"
                )
                .autocorrectionDisabled()
                .textInputAutocapitalization(.characters)
                .onChange(of: text) { _, new in model.onQueryChanged(new) }
                .onSubmit(of: .search) { pick(text) }
        }
        .presentationBackground(DS.Surface.page)
    }

    private func pick(_ raw: String) {
        guard let symbol = AnalysisModel.normalize(raw) else { return }
        onPick(symbol)
        dismiss()
    }

    @ViewBuilder
    private var content: some View {
        if model.loading, model.results == nil {
            List {
                DSSection("Stocks and ETFs") {
                    DashboardPlaceholderRows(count: 5)
                }
            }
            .dsList()
            .accessibilityLabel("Loading")
        } else if let error = model.error {
            VStack(spacing: 16) {
                EmptyState(title: "Search is unavailable", subtitle: searchUnavailableMessage(error))
                Button("Retry search") { model.retry() }
                    .buttonStyle(.dsSecondary)
            }
            .frame(maxHeight: .infinity, alignment: .top)
        } else if let results = model.results {
            if results.isEmpty {
                EmptyState(title: "No matching symbols", subtitle: "Press return to use \(text.uppercased()) anyway")
                    .frame(maxHeight: .infinity, alignment: .top)
            } else {
                List {
                    DSSection("Stocks and ETFs") {
                        ForEach(Array(results.enumerated()), id: \.offset) { _, r in
                            row(r)
                        }
                    }
                }
                .dsList()
                .entityRowTitleMedium()
            }
        } else {
            EmptyState(title: "Find a symbol", subtitle: "Search by ticker or company name")
                .frame(maxHeight: .infinity, alignment: .top)
        }
    }

    private func row(_ r: SearchInstrument) -> some View {
        let quote = model.quotes?[r.symbol]
        return Button {
            pick(r.symbol)
        } label: {
            EntityRow(r.symbol, subtitle: r.name) {
                EntityRowValue(
                    fmtMoney(quote?.price),
                    detail: quote?.changePct.map { fmtPct($0) },
                    detailColor: quote?.changePct.map { dashboardChangeColor($0) }
                )
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
