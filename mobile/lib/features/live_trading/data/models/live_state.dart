/// Plain immutable models for the live-trading endpoint.
/// All fromJson factories are nullable-tolerant.
library;

import '../../../../core/models/option_symbol.dart';

class LiveState {
  const LiveState({
    required this.status,
    required this.equity,
    required this.cash,
    required this.buyingPower,
    required this.totalPnl,
    required this.totalPnlPct,
    required this.dayPnl,
    required this.dayPnlPct,
    required this.uptimeSec,
    required this.tradingActive,
    this.brokerFetchError,
    this.containerStale = false,
    this.lookback,
    this.positions = const [],
    this.recentTrades = const [],
  });

  final String status;
  final double equity;
  final double cash;
  final double buyingPower;
  final double totalPnl;
  final double totalPnlPct;
  final double dayPnl;
  final double dayPnlPct;
  final double uptimeSec;
  final bool tradingActive;
  final String? brokerFetchError;
  final bool containerStale;
  final Lookback? lookback;
  final List<Position> positions;
  final List<Trade> recentTrades;

  factory LiveState.fromJson(Map<String, dynamic> json) {
    return LiveState(
      status: (json['status'] as String?) ?? 'unknown',
      equity: (json['equity'] as num?)?.toDouble() ?? 0,
      cash: (json['cash'] as num?)?.toDouble() ?? 0,
      buyingPower: (json['buying_power'] as num?)?.toDouble() ??
          (json['cash'] as num?)?.toDouble() ??
          0,
      totalPnl: (json['total_pnl'] as num?)?.toDouble() ?? 0,
      totalPnlPct: (json['total_pnl_pct'] as num?)?.toDouble() ?? 0,
      dayPnl: (json['day_pnl'] as num?)?.toDouble() ?? 0,
      dayPnlPct: (json['day_pnl_pct'] as num?)?.toDouble() ?? 0,
      uptimeSec: (json['uptime_sec'] as num?)?.toDouble() ?? 0,
      tradingActive: json['trading_active'] == true,
      brokerFetchError: json['broker_fetch_error'] as String?,
      containerStale: json['container_stale'] == true,
      lookback: json['lookback'] is Map<String, dynamic>
          ? Lookback.fromJson(json['lookback'] as Map<String, dynamic>)
          : null,
      positions: (json['positions'] as List?)
              ?.whereType<Map<String, dynamic>>()
              .map(Position.fromJson)
              .toList() ??
          const [],
      recentTrades: (json['recent_trades'] as List?)
              ?.whereType<Map<String, dynamic>>()
              .map(Trade.fromJson)
              .toList() ??
          const [],
    );
  }
}

class Position {
  const Position({
    required this.symbol,
    required this.qty,
    this.marketValue,
    this.lastPrice,
    this.avgEntryPrice,
    this.unrealizedPnl,
    this.unrealizedPnlPct,
    this.assetClass,
    this.side,
    this.multiplier,
    this.underlying,
    this.strike,
    this.expiry,
  });

  final String symbol;

  /// Signed: negative for a short option.
  final double qty;

  /// Broker-reported dollars, contract multiplier already included. Null when
  /// the broker has no quote (an illiquid option): render a dash, never 0.
  final double? marketValue;

  /// Per share. For an option, the per-share premium.
  final double? lastPrice;
  final double? avgEntryPrice;
  final double? unrealizedPnl;
  final double? unrealizedPnlPct;

  /// "us_equity" | "us_option" (spec 2026-09-24 section 6.1). Null from an
  /// API build that predates the field.
  final String? assetClass;

  /// "long" | "short".
  final String? side;
  final int? multiplier;
  final String? underlying;
  final double? strike;

  /// YYYY-MM-DD.
  final String? expiry;

  bool get isOption => (assetClass?.isNotEmpty ?? false)
      ? assetClass!.toLowerCase() == 'us_option'
      : isOccOptionSymbol(symbol);

  bool get isShort =>
      (side?.isNotEmpty ?? false) ? side!.toLowerCase() == 'short' : qty < 0;

  int get contractMultiplier {
    if (!isOption) return 1;
    final m = multiplier;
    return (m != null && m > 0) ? m : kOptionMultiplier;
  }

  String get quantityLabel => isOption ? 'CONTRACTS' : 'SHARES';

  /// Options: unsigned whole contracts (the SHORT badge carries the sign).
  String get quantityText =>
      isOption ? qty.abs().truncate().toString() : qty.toStringAsFixed(4);

  /// The close_position command reads the adapter's equity book only, so a
  /// Close on an option would fail. The wheel lane manages its own buy-backs.
  bool get canClose => !isOption;

  String get optionDescription => isOption
      ? describeOptionContract(
          symbol: symbol,
          underlying: underlying,
          strike: strike,
          expiry: expiry,
        )
      : '';

  factory Position.fromJson(Map<String, dynamic> json) {
    return Position(
      symbol: (json['symbol'] as String?) ?? '',
      qty: (json['qty'] as num?)?.toDouble() ?? 0,
      marketValue: (json['market_value'] as num?)?.toDouble(),
      lastPrice: (json['last_price'] as num?)?.toDouble(),
      avgEntryPrice: (json['avg_entry_price'] as num?)?.toDouble(),
      unrealizedPnl: (json['unrealized_pnl'] as num?)?.toDouble(),
      unrealizedPnlPct: (json['unrealized_pnl_pct'] as num?)?.toDouble(),
      assetClass: json['asset_class'] as String?,
      side: json['side'] as String?,
      multiplier: (json['multiplier'] as num?)?.toInt(),
      underlying: json['underlying'] as String?,
      strike: (json['strike'] as num?)?.toDouble(),
      expiry: json['expiry'] as String?,
    );
  }
}

class Trade {
  const Trade({
    required this.side,
    required this.symbol,
    required this.price,
    required this.qty,
    this.ts,
    this.orderId,
    this.assetClass,
  });

  final String side;
  final String symbol;

  /// Per share. For an option fill, the per-share premium.
  final double price;
  final double qty;
  final dynamic ts; // ISO string or epoch
  final String? orderId;

  /// Not sent by today's recent_trades; read if a later build adds it.
  final String? assetClass;

  bool get isOption => (assetClass?.isNotEmpty ?? false)
      ? assetClass!.toLowerCase() == 'us_option'
      : isOccOptionSymbol(symbol);

  String get quantityLabel => isOption ? 'CONTRACTS' : 'SHARES';

  String get quantityText =>
      isOption ? qty.abs().truncate().toString() : qty.toStringAsFixed(4);

  /// Fill value in dollars: price x qty, x100 for an option contract.
  double get total => price * qty * (isOption ? kOptionMultiplier : 1);

  factory Trade.fromJson(Map<String, dynamic> json) {
    return Trade(
      side: (json['side'] as String?) ?? '',
      symbol: (json['symbol'] as String?) ?? '',
      price: (json['price'] as num?)?.toDouble() ?? 0,
      qty: (json['qty'] as num?)?.toDouble() ?? 0,
      ts: json['ts'],
      orderId: json['order_id'] as String?,
      assetClass: json['asset_class'] as String?,
    );
  }
}

class Lookback {
  const Lookback({
    required this.specName,
    required this.startDate,
    required this.endDate,
    required this.current,
    required this.total,
    required this.currentDate,
  });

  final String specName;
  final String startDate;
  final String endDate;
  final int current;
  final int total;
  final String currentDate;

  double get pct {
    if (total == 0) return 0;
    final v = (current / total) * 100;
    return v.clamp(0, 100);
  }

  factory Lookback.fromJson(Map<String, dynamic> json) {
    return Lookback(
      specName: (json['spec_name'] as String?) ?? '',
      startDate: (json['start_date'] as String?) ?? '',
      endDate: (json['end_date'] as String?) ?? '',
      current: (json['current'] as num?)?.toInt() ?? 0,
      total: (json['total'] as num?)?.toInt() ?? 0,
      currentDate: (json['current_date'] as String?) ?? '',
    );
  }
}
