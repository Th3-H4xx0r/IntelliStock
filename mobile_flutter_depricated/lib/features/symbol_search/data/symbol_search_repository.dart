import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_client.dart';
import 'symbol_search_models.dart';

class SymbolSearchRepository {
  const SymbolSearchRepository(this._client);

  final ApiClient _client;

  Future<List<SearchInstrument>> search(String query) async {
    final data = await _client.get<Map<String, dynamic>>(
      '/symbols/search',
      query: {'q': query},
    );
    return (data['results'] as List? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(SearchInstrument.fromJson)
        .where((result) => result.symbol.isNotEmpty)
        .toList();
  }
}

final symbolSearchRepositoryProvider = Provider<SymbolSearchRepository>(
  (ref) => SymbolSearchRepository(ref.read(apiClientProvider)),
);
