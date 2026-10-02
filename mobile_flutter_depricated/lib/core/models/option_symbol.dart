/// OCC option-symbol helpers. Pure Dart, no Flutter.
///
/// Live-state positions carry `asset_class` (spec 2026-09-24 section 6.1), so
/// these are a display fallback for rows that do not: recent trades and the
/// dashboard's /brokerages/{id}/positions holdings. The engine identifies
/// contracts by Alpaca's contract fields, never by this shape (spec fix 10).
library;

/// Shares per US equity option contract.
const kOptionMultiplier = 100;

// Root (1-6), YYMMDD, C|P, strike x 1000 in 8 digits.
final _occ = RegExp(r'^([A-Z][A-Z0-9]{0,5})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$');

class OccContract {
  const OccContract({
    required this.underlying,
    required this.expiry,
    required this.optionType,
    required this.strike,
  });

  final String underlying;

  /// YYYY-MM-DD.
  final String expiry;

  /// "put" | "call".
  final String optionType;
  final double strike;
}

OccContract? parseOccSymbol(String? symbol) {
  final m = _occ.firstMatch((symbol ?? '').trim().toUpperCase());
  if (m == null) return null;
  return OccContract(
    underlying: m[1]!,
    expiry: '20${m[2]}-${m[3]}-${m[4]}',
    optionType: m[5] == 'P' ? 'put' : 'call',
    strike: int.parse(m[6]!) / 1000,
  );
}

bool isOccOptionSymbol(String? symbol) => parseOccSymbol(symbol) != null;

/// "APH $130 Put · 2026-10-02". Explicit fields win; the symbol fills gaps.
String describeOptionContract({
  required String symbol,
  String? underlying,
  double? strike,
  String? optionType,
  String? expiry,
}) {
  final occ = parseOccSymbol(symbol);
  final u = (underlying?.isNotEmpty ?? false) ? underlying! : (occ?.underlying ?? '');
  final k = strike ?? occ?.strike;
  final t = (optionType?.isNotEmpty ?? false) ? optionType! : (occ?.optionType ?? '');
  final e = (expiry?.isNotEmpty ?? false) ? expiry! : (occ?.expiry ?? '');
  final parts = <String>[
    if (u.isNotEmpty) u,
    if (k != null)
      k == k.roundToDouble()
          ? '\$${k.toStringAsFixed(0)}'
          : '\$${k.toStringAsFixed(2)}',
    if (t.isNotEmpty) t.toLowerCase() == 'put' ? 'Put' : 'Call',
  ];
  return e.isEmpty ? parts.join(' ') : '${parts.join(' ')} · $e';
}
