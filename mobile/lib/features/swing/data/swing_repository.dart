import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/api_client.dart';

// Shapes: SwingSignals is section 5 of
// docs/superpowers/plans/2026-09-24-swing-port-interfaces.md; the wheel
// payload is the Contract addendum of
// docs/superpowers/plans/2026-09-24-swing-port-C-ui.md. If plan B ships a
// different wheel shape, WheelSnapshot.fromJson is the only reader to change.

double? _num(Object? v) =>
    v is num ? v.toDouble() : (v is String ? double.tryParse(v) : null);

int? _int(Object? v) =>
    v is num ? v.toInt() : (v is String ? int.tryParse(v) : null);

String _str(Object? v) => v == null ? '' : v.toString();

// ── Models ────────────────────────────────────────────────────────────────────

/// One AI-scored candidate awaiting (or past) an operator decision.
class SwingSignal {
  const SwingSignal({
    required this.id,
    required this.lane,
    required this.symbol,
    required this.session,
    required this.createdAt,
    required this.score,
    required this.recommendation,
    required this.reasoning,
    required this.keyRisks,
    required this.sizeAdjustment,
    required this.proposal,
    required this.status,
    this.decidedAt,
  });

  final String id;

  /// "swing" | "wheel".
  final String lane;
  final String symbol;

  /// NY trading date, YYYY-MM-DD.
  final String session;
  final String createdAt;
  final int? score;
  final String recommendation;
  final String reasoning;
  final List<String> keyRisks;
  final double? sizeAdjustment;
  final Map<String, dynamic> proposal;
  final String status;

  /// When the operator decided it (UTC); null while pending or unparseable.
  final DateTime? decidedAt;

  bool get isWheel => lane == 'wheel';

  /// Approve-half exists for swing entries only; the wheel sizes in whole
  /// contracts.
  bool get allowsHalf => lane == 'swing';

  String get keyRisksText => keyRisks
      .map((r) => r.trim())
      .where((r) => r.isNotEmpty)
      .join(' · ');

  // swing proposal
  double? get entry => _num(proposal['entry']);
  double? get stop => _num(proposal['stop']);
  double? get target => _num(proposal['target']);
  int? get shares => _int(proposal['shares']);

  // wheel proposal
  String get contract => _str(proposal['contract']);
  double? get strike => _num(proposal['strike']);
  String get expiry => _str(proposal['expiry']);
  int? get qty => _int(proposal['qty']);
  double? get limitPrice => _num(proposal['limit_price']);
  double? get premiumEst => _num(proposal['premium_est']);

  /// Premium for the whole order: per-share premium x 100 x contracts.
  double? get creditEst {
    final p = premiumEst;
    final q = qty;
    return (p == null || q == null) ? null : p * 100 * q;
  }

  /// Cash a sold put ties up: strike x 100 x contracts.
  double? get collateral {
    final s = strike;
    final q = qty;
    return (s == null || q == null) ? null : s * 100 * q;
  }

  factory SwingSignal.fromJson(Map<String, dynamic> j) => SwingSignal(
        id: _str(j['id']),
        lane: _str(j['lane']).isEmpty ? 'swing' : _str(j['lane']),
        symbol: _str(j['symbol']),
        session: _str(j['session']),
        createdAt: _str(j['created_at']),
        score: _int(j['score']),
        recommendation: _str(j['recommendation']),
        reasoning: _str(j['reasoning']),
        keyRisks: ((j['key_risks'] as List?) ?? const [])
            .map((e) => e.toString())
            .toList(),
        sizeAdjustment: _num(j['size_adjustment']),
        proposal: (j['proposal'] as Map?)?.cast<String, dynamic>() ?? const {},
        status: _str(j['status']).isEmpty ? 'pending' : _str(j['status']),
        decidedAt: DateTime.tryParse(_str(j['decided_at']))?.toUtc(),
      );
}

/// One open cash-secured put.
class WheelPut {
  const WheelPut({
    required this.contract,
    required this.underlying,
    this.strike,
    required this.expiry,
    this.qty,
    this.avgEntryPrice,
    this.currentPrice,
    this.underlyingPrice,
    this.itmPct,
    this.dte,
    this.collateral,
    this.unrealizedPl,
  });

  final String contract;
  final String underlying;
  final double? strike;
  final String expiry;
  final int? qty;
  final double? avgEntryPrice;
  final double? currentPrice;
  final double? underlyingPrice;

  /// Percent the underlying sits below the strike; > 0 means in the money.
  final double? itmPct;
  final int? dte;
  final double? collateral;
  final double? unrealizedPl;

  /// Exactly the puts the 15:45 ET monitor buys back (spec section 5.2).
  bool get monitorWillBuyBack {
    final itm = itmPct;
    if (itm == null) return false;
    final d = dte;
    return itm >= 10 ||
        (itm >= 5 && d != null && d <= 2) ||
        (itm > 0 && d == 0);
  }

  factory WheelPut.fromJson(Map<String, dynamic> j) => WheelPut(
        contract: _str(j['contract']),
        underlying: _str(j['underlying']),
        strike: _num(j['strike']),
        expiry: _str(j['expiry']),
        qty: _int(j['qty']),
        avgEntryPrice: _num(j['avg_entry_price']),
        currentPrice: _num(j['current_price']),
        underlyingPrice: _num(j['underlying_price']),
        itmPct: _num(j['itm_pct']),
        dte: _int(j['dte']),
        collateral: _num(j['collateral']),
        unrealizedPl: _num(j['unrealized_pl']),
      );
}

/// One row of the weekly wheel scan log (SwingWheelScans).
class WheelScan {
  const WheelScan({
    required this.id,
    required this.session,
    required this.symbol,
    this.strike,
    required this.expiry,
    this.score,
    required this.status,
    required this.skipReason,
  });

  final String id;
  final String session;
  final String symbol;
  final double? strike;
  final String expiry;
  final int? score;

  /// "placed" | "pending" | "rejected" | "skipped".
  final String status;
  final String skipReason;

  factory WheelScan.fromJson(Map<String, dynamic> j) => WheelScan(
        id: _str(j['id']),
        session: _str(j['session']),
        symbol: _str(j['symbol']),
        strike: _num(j['strike']),
        expiry: _str(j['expiry']),
        score: _int(j['score']),
        status: _str(j['status']),
        skipReason: _str(j['skip_reason']),
      );
}

/// GET /instances/{id}/wheel.
class WheelSnapshot {
  const WheelSnapshot({
    this.openPuts = const [],
    this.collateralTotal,
    this.cash,
    this.recentScans = const [],
    this.fetchedAt,
  });

  static const empty = WheelSnapshot();

  final List<WheelPut> openPuts;
  final double? collateralTotal;
  final double? cash;
  final List<WheelScan> recentScans;

  /// When this book was fetched (local time). The card loads only on open
  /// and pull-to-refresh, so it says how old the book is (FW item 4, M-3).
  final DateTime? fetchedAt;

  factory WheelSnapshot.fromJson(Map<String, dynamic> j,
          {DateTime? fetchedAt}) =>
      WheelSnapshot(
        openPuts: ((j['open_puts'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => WheelPut.fromJson(m.cast<String, dynamic>()))
            .toList(),
        collateralTotal: _num(j['collateral_total']),
        cash: _num(j['cash']),
        recentScans: ((j['recent_scans'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => WheelScan.fromJson(m.cast<String, dynamic>()))
            .toList(),
        fetchedAt: fetchedAt,
      );
}

/// What a 2xx from POST .../decision said. FW-api-I1: a 202 carries
/// `{"uncertain": true, "detail"}` — the approval is recorded, but the broker
/// command may or may not be queued, so the order may be in flight.
class DecisionReceipt {
  const DecisionReceipt({this.uncertain = false, this.detail = ''});

  static const recorded = DecisionReceipt();

  final bool uncertain;
  final String detail;

  factory DecisionReceipt.fromJson(Object? data) => data is Map
      ? DecisionReceipt(
          uncertain: data['uncertain'] == true,
          detail: _str(data['detail']).trim(),
        )
      : recorded;
}

// ── Repository ────────────────────────────────────────────────────────────────

class SwingRepository {
  const SwingRepository(this._client);

  final ApiClient _client;

  Future<List<SwingSignal>> _signals(String instanceId, String status) async {
    final data = await _client.get<dynamic>(
      '/instances/$instanceId/swing/signals',
      query: {'status': status},
    );
    final rows = data is List
        ? data
        : (data is Map ? (data['signals'] as List?) ?? const [] : const []);
    return rows
        .whereType<Map>()
        .map((m) => SwingSignal.fromJson(m.cast<String, dynamic>()))
        .where((s) => s.id.isNotEmpty && s.status == status)
        .toList();
  }

  /// Pending signals, newest first. Accepts a bare list or {signals: [...]}
  /// and drops anything not pending, in case an older API build ignores the
  /// status filter.
  Future<List<SwingSignal>> pendingSignals(String instanceId) async =>
      (await _signals(instanceId, 'pending'))
        ..sort((a, b) => b.createdAt.compareTo(a.createdAt));

  /// Signals that still read approved or approved_half, newest decision
  /// first: the ones a broker command has not claimed yet (fix wave item 3).
  Future<List<SwingSignal>> approvedSignals(String instanceId) async {
    final lists = await Future.wait([
      _signals(instanceId, 'approved'),
      _signals(instanceId, 'approved_half'),
    ]);
    final rows = [...lists[0], ...lists[1]];
    final epoch = DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
    return rows
      ..sort((a, b) => (b.decidedAt ?? epoch).compareTo(a.decidedAt ?? epoch));
  }

  /// POST .../resend: queue a stuck approval's broker command again. Throws
  /// ApiError on non-2xx (409 not approved or a command still queued, 503
  /// not queued).
  Future<DecisionReceipt> resend(String instanceId, String signalId) async {
    final data = await _client.post<dynamic>(
      '/instances/$instanceId/swing/signals/$signalId/resend',
    );
    return DecisionReceipt.fromJson(data);
  }

  /// POST .../decision with {decision, reason?}. decision is
  /// "approve" | "approve_half" | "reject". Throws ApiError on non-2xx.
  Future<DecisionReceipt> decide(
    String instanceId,
    String signalId,
    String decision, {
    String? reason,
  }) async {
    final body = <String, dynamic>{'decision': decision};
    final r = reason?.trim();
    if (r != null && r.isNotEmpty) body['reason'] = r;
    final data = await _client.post<dynamic>(
      '/instances/$instanceId/swing/signals/$signalId/decision',
      body: body,
    );
    return DecisionReceipt.fromJson(data);
  }

  Future<WheelSnapshot> wheel(String instanceId) async {
    final data = await _client.get<dynamic>('/instances/$instanceId/wheel');
    return data is Map
        ? WheelSnapshot.fromJson(data.cast<String, dynamic>(),
            fetchedAt: DateTime.now())
        : WheelSnapshot.empty;
  }
}

final swingRepositoryProvider = Provider<SwingRepository>(
  (ref) => SwingRepository(ref.watch(apiClientProvider)),
);
