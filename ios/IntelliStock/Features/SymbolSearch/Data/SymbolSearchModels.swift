import Foundation

// Ported from features/symbol_search/data/symbol_search_models.dart.

/// One searchable market instrument returned by the backend.
nonisolated struct SearchInstrument: Hashable, Sendable {
    let symbol: String
    let name: String
    let type: String

    init(symbol: String, name: String, type: String) {
        self.symbol = symbol
        self.name = name
        self.type = type
    }

    init(json j: JSON) {
        self.init(symbol: j["symbol"].stringOr(""), name: j["name"].stringOr(""), type: j["type"].stringOr(""))
    }

    func matches(_ query: String) -> Bool {
        let normalized = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return normalized.isEmpty
            || symbol.lowercased().contains(normalized)
            || name.lowercased().contains(normalized)
    }
}

/// The latest quote shown alongside a search result.
nonisolated struct SearchQuote: Hashable, Sendable {
    let price: Double
    let changePct: Double?

    init(price: Double, changePct: Double? = nil) {
        self.price = price
        self.changePct = changePct
    }
}

nonisolated func searchQuoteFromHistory(_ values: [Double]) -> SearchQuote? {
    guard let price = values.last else { return nil }
    if values.count < 2 || values[0] == 0 {
        return SearchQuote(price: price)
    }
    return SearchQuote(price: price, changePct: (price - values[0]) / values[0] * 100)
}

/// Builds the single quote batch needed for the visible results: unique
/// non-empty symbols in first-appearance order.
nonisolated func searchSymbolsForSparklines(_ results: [SearchInstrument]) -> [String] {
    var seen = Set<String>()
    var symbols: [String] = []
    for result in results where !result.symbol.isEmpty && seen.insert(result.symbol).inserted {
        symbols.append(result.symbol)
    }
    return symbols
}
