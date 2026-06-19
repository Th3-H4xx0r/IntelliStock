/// Backend API timeouts. The base URL is configured at runtime by the user
/// (see ApiBaseUrlStore) — there is intentionally no hardcoded default.
abstract class ApiConfig {
  static const Duration connectTimeout = Duration(seconds: 15);
  static const Duration receiveTimeout = Duration(seconds: 30);
}
