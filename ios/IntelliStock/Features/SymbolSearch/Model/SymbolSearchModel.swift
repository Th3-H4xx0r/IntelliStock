import Foundation
import Observation

// Ported from features/symbol_search/presentation/symbol_search_screen.dart
// (`_SymbolSearchScreenState` and `searchQuotesProvider`).

/// The friendly copy for a failed search: a 404 means the search service is
/// still coming online; anything else is a connection problem.
nonisolated func searchUnavailableMessage(_ message: String) -> String {
    if message == "Not Found" {
        return "Search is taking a moment to come online. Your dashboard is still up to date."
    }
    return "We could not reach market search. Check your connection and try again."
}

/// The search screen's state: a 250 ms debounced query, its results, and
/// one quote batch per result set.
@Observable
final class SymbolSearchModel {
    static let debounce: Duration = .milliseconds(250)

    /// The field's text (`_controller.text`).
    private(set) var query = ""
    /// nil before the first search and while a new one runs.
    private(set) var results: [SearchInstrument]?
    private(set) var error: String?
    private(set) var loading = false
    /// `searchQuotesProvider(symbols)`: nil while loading or after a failure.
    private(set) var quotes: [String: SearchQuote]?
    private(set) var quotesLoading = false

    @ObservationIgnored private let search: (String) async throws -> [SearchInstrument]
    @ObservationIgnored private let historicals: ([String], String) async throws -> [String: [HistPoint]]
    @ObservationIgnored private let sleep: PollingSleep
    @ObservationIgnored private var pending: Task<Void, Never>?
    @ObservationIgnored private var quotesTask: Task<Void, Never>?

    init(
        search: @escaping (String) async throws -> [SearchInstrument],
        historicals: @escaping ([String], String) async throws -> [String: [HistPoint]],
        sleep: @escaping PollingSleep = realPollingSleep
    ) {
        self.search = search
        self.historicals = historicals
        self.sleep = sleep
    }

    /// `_onQueryChanged`: empty → reset; otherwise clear, show loading, and
    /// search after the debounce. A reply for a query that is no longer in
    /// the field is dropped.
    func onQueryChanged(_ text: String) {
        query = text
        pending?.cancel()
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            results = nil
            error = nil
            loading = false
            setQuotes(for: nil)
            return
        }
        results = nil
        loading = true
        error = nil
        setQuotes(for: nil)
        let sleep = sleep
        pending = Task { [weak self] in
            do { try await sleep(Self.debounce) } catch { return }
            guard let self, !Task.isCancelled else { return }
            await self.run(trimmed)
        }
    }

    /// The Retry button: re-run the current text.
    func retry() { onQueryChanged(query) }

    /// Waits for the pending debounced search (tests).
    func settle() async { await pending?.value; await quotesTask?.value }

    private func run(_ trimmed: String) async {
        do {
            let found = try await search(trimmed)
            guard query.trimmingCharacters(in: .whitespacesAndNewlines) == trimmed else { return }
            results = found
            loading = false
            setQuotes(for: found)
        } catch is CancellationError {
            // A cancelled request leaves the state as it is.
        } catch let api as ApiError {
            guard query.trimmingCharacters(in: .whitespacesAndNewlines) == trimmed else { return }
            error = api.message
            loading = false
        } catch {
            guard query.trimmingCharacters(in: .whitespacesAndNewlines) == trimmed else { return }
            self.error = "Couldn't search symbols right now."
            loading = false
        }
    }

    /// One quote batch for the visible results (unique symbols, first-seen
    /// order), priced from today's 1D history.
    private func setQuotes(for results: [SearchInstrument]?) {
        quotesTask?.cancel()
        quotes = nil
        guard let results, !results.isEmpty else {
            quotesLoading = false
            return
        }
        let symbols = searchSymbolsForSparklines(results)
        quotesLoading = true
        let historicals = historicals
        quotesTask = Task { [weak self] in
            let values = try? await historicals(symbols, "1D")
            guard let self, !Task.isCancelled else { return }
            if let values {
                var out: [String: SearchQuote] = [:]
                for (symbol, points) in values {
                    if let quote = searchQuoteFromHistory(points.map(\.value)) { out[symbol] = quote }
                }
                quotes = out
            }
            quotesLoading = false
        }
    }
}
