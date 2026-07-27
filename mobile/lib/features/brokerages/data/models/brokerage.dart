/// Plain model for a linked brokerage account.
///
/// All optional fields are nullable-tolerant: missing JSON keys produce null
/// rather than a parse error.
class Brokerage {
  const Brokerage({
    required this.id,
    required this.brokerageType,
    required this.accountName,
    this.status,
    required this.paper,
    this.accountNumber,
    this.alpacaDataFeed,
    this.lastRefreshAt,
    this.lastError,
    this.equity,
    this.buyingPower,
    this.managementType,
  });

  final String id;

  /// Supported brokerage discriminator.
  final String brokerageType;

  final String accountName;

  /// 'active', 'expired', etc.
  final String? status;

  final bool paper;

  final String? accountNumber;

  /// 'iex' or 'sip' — Alpaca only.
  final String? alpacaDataFeed;

  final String? lastRefreshAt;
  final String? lastError;

  final double? equity;
  final double? buyingPower;
  final String? managementType;

  factory Brokerage.fromJson(Map<String, dynamic> json) {
    double? d(dynamic v) {
      if (v == null) return null;
      if (v is num) return v.toDouble();
      if (v is String) return double.tryParse(v);
      return null;
    }

    // Account number can live under different keys depending on brokerage type.
    final acctNum =
        json['account_number'] as String? ??
        json['alpaca_account_number'] as String?;

    return Brokerage(
      id: json['id'] as String? ?? '',
      brokerageType: json['brokerage_type'] as String? ?? 'alpaca',
      accountName: json['account_name'] as String? ?? '',
      status: json['status'] as String?,
      paper: json['alpaca_paper'] as bool? ?? json['paper'] as bool? ?? true,
      accountNumber: acctNum,
      alpacaDataFeed: json['alpaca_data_feed'] as String?,
      lastRefreshAt: json['last_refresh_at'] as String?,
      lastError: json['last_error'] as String?,
      equity: d(json['equity']),
      buyingPower: d(json['buying_power']),
      managementType: json['management_type'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'brokerage_type': brokerageType,
    'account_name': accountName,
    if (status != null) 'status': status,
    'paper': paper,
    if (accountNumber != null) 'account_number': accountNumber,
    if (alpacaDataFeed != null) 'alpaca_data_feed': alpacaDataFeed,
    if (lastRefreshAt != null) 'last_refresh_at': lastRefreshAt,
    if (lastError != null) 'last_error': lastError,
    if (equity != null) 'equity': equity,
    if (buyingPower != null) 'buying_power': buyingPower,
    if (managementType != null) 'management_type': managementType,
  };
}
