/// One searchable market instrument returned by the backend.
class SearchInstrument {
  const SearchInstrument({
    required this.symbol,
    required this.name,
    required this.type,
  });

  final String symbol;
  final String name;
  final String type;

  factory SearchInstrument.fromJson(Map<String, dynamic> json) =>
      SearchInstrument(
        symbol: (json['symbol'] ?? '').toString(),
        name: (json['name'] ?? '').toString(),
        type: (json['type'] ?? '').toString(),
      );

  bool matches(String query) {
    final normalized = query.trim().toLowerCase();
    return normalized.isEmpty ||
        symbol.toLowerCase().contains(normalized) ||
        name.toLowerCase().contains(normalized);
  }
}

/// The latest quote shown alongside a search result.
class SearchQuote {
  const SearchQuote({required this.price, this.changePct});

  final double price;
  final double? changePct;
}

SearchQuote? searchQuoteFromHistory(List<double> values) {
  if (values.isEmpty) return null;
  final price = values.last;
  if (values.length < 2 || values.first == 0) {
    return SearchQuote(price: price);
  }
  return SearchQuote(
    price: price,
    changePct: (price - values.first) / values.first * 100,
  );
}

/// Builds the single quote batch needed for the visible results.
List<String> searchSymbolsForSparklines(List<SearchInstrument> results) {
  final symbols = <String>{};
  for (final result in results) {
    if (result.symbol.isNotEmpty) symbols.add(result.symbol);
  }
  return symbols.toList();
}
