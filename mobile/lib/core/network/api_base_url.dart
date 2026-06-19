import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'session.dart';

const _kApiBaseUrl = 'api_base_url';

/// Trim, drop empties, strip a single trailing slash. Scheme left untouched.
String normalizeBaseUrl(String raw) {
  var s = raw.trim();
  if (s.isEmpty) return '';
  if (s.endsWith('/')) s = s.substring(0, s.length - 1);
  return s;
}

/// A syntactically usable backend base URL: http/https scheme + non-empty host.
bool isValidBaseUrl(String raw) {
  final s = normalizeBaseUrl(raw);
  if (s.isEmpty) return false;
  final uri = Uri.tryParse(s);
  if (uri == null) return false;
  if (uri.scheme != 'http' && uri.scheme != 'https') return false;
  return uri.host.isNotEmpty;
}

/// The active backend base URL, persisted in the platform keychain/keystore.
/// Mirrors [SessionStore]: a [ChangeNotifier] so the router can use it as a
/// refreshListenable and Dio can rebuild when it changes.
class ApiBaseUrlStore extends ChangeNotifier {
  ApiBaseUrlStore(this._storage);

  final FlutterSecureStorage _storage;
  String _baseUrl = '';

  String get baseUrl => _baseUrl;
  bool get isConfigured => _baseUrl.isNotEmpty;

  Future<void> load() async {
    _baseUrl = normalizeBaseUrl(await _storage.read(key: _kApiBaseUrl) ?? '');
    notifyListeners();
  }

  Future<void> set(String url) async {
    final next = normalizeBaseUrl(url);
    _baseUrl = next;
    if (next.isEmpty) {
      await _storage.delete(key: _kApiBaseUrl);
    } else {
      await _storage.write(key: _kApiBaseUrl, value: next);
    }
    notifyListeners();
  }
}

final apiBaseUrlProvider = ChangeNotifierProvider<ApiBaseUrlStore>(
  (ref) => ApiBaseUrlStore(ref.watch(secureStorageProvider)),
);
