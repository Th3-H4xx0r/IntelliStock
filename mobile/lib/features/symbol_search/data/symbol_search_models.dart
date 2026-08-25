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

/// Builds the single price-history batch needed to draw the visible results.
List<String> searchSymbolsForSparklines(List<SearchInstrument> results) {
  final symbols = <String>{};
  for (final result in results) {
    if (result.symbol.isNotEmpty) symbols.add(result.symbol);
  }
  return symbols.toList();
}
