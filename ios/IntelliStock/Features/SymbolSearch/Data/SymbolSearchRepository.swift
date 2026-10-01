import Foundation

/// Ported from features/symbol_search/data/symbol_search_repository.dart.
nonisolated struct SymbolSearchRepository: Sendable {
    let client: ApiClient

    func search(_ query: String) async throws -> [SearchInstrument] {
        let data = try await client.get("/symbols/search", query: ["q": .string(query)])
        return data["results"].objectElements
            .map(SearchInstrument.init(json:))
            .filter { !$0.symbol.isEmpty }
    }
}
