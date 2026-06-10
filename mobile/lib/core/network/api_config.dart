/// Backend API configuration.
///
/// The web frontend talks to `/api` in dev (a Vite proxy that strips the
/// prefix) and to `VITE_API_URL` in prod. The real backend serves bare paths,
/// so the mobile app concatenates bare paths onto the base URL directly.
abstract class ApiConfig {
  /// Override at build time with `--dart-define=API_URL=https://...`.
  static const String baseUrl = String.fromEnvironment(
    'API_URL',
    defaultValue: 'https://intellistock-api.pkrishna.dev',
  );

  static const Duration connectTimeout = Duration(seconds: 15);
  static const Duration receiveTimeout = Duration(seconds: 30);
}
