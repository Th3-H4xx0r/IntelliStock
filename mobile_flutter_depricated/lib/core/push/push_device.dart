/// A registered APNs device (as returned by GET /push/devices).
class PushDevice {
  const PushDevice({
    required this.deviceToken,
    required this.platform,
    required this.env,
    this.appVersion,
    this.lastSeen,
  });

  final String deviceToken;
  final String platform;
  final String env;
  final String? appVersion;
  final String? lastSeen;

  factory PushDevice.fromJson(Map j) => PushDevice(
        deviceToken: (j['device_token'] ?? '').toString(),
        platform: (j['platform'] ?? 'ios').toString(),
        env: (j['env'] ?? 'prod').toString(),
        appVersion: j['app_version']?.toString(),
        lastSeen: j['last_seen']?.toString(),
      );

  /// Short, human-friendly identifier (APNs tokens are 64 hex chars).
  String get tokenSuffix => deviceToken.length <= 8
      ? deviceToken
      : '…${deviceToken.substring(deviceToken.length - 8)}';
}
