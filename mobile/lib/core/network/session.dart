import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

const _kToken = 'intellistock_token';
const _kUser = 'intellistock_user';

/// Holds the JWT + cached user, persisted in the platform keychain/keystore.
///
/// The token is mirrored in memory so the [AuthInterceptor] can read it
/// synchronously on every request.
class SessionStore extends ChangeNotifier {
  SessionStore(this._storage);

  final FlutterSecureStorage _storage;

  String? _token;
  Map<String, dynamic>? _user;

  String? get token => _token;
  Map<String, dynamic>? get user => _user;
  bool get isAuthenticated => _token != null && _token!.isNotEmpty;
  bool get hasCompletedOnboarding => _user?['has_completed_onboarding'] == true;
  String get username => (_user?['username'] ?? _user?['name'] ?? 'User').toString();

  /// Load persisted session on app start.
  Future<void> load() async {
    _token = await _storage.read(key: _kToken);
    final rawUser = await _storage.read(key: _kUser);
    if (rawUser != null) {
      try {
        _user = jsonDecode(rawUser) as Map<String, dynamic>;
      } catch (_) {
        _user = null;
      }
    }
    notifyListeners();
  }

  Future<void> setSession(String token, Map<String, dynamic>? user) async {
    _token = token;
    _user = user;
    await _storage.write(key: _kToken, value: token);
    if (user != null) {
      await _storage.write(key: _kUser, value: jsonEncode(user));
    } else {
      await _storage.delete(key: _kUser);
    }
    notifyListeners();
  }

  Future<void> setUser(Map<String, dynamic> user) async {
    _user = user;
    await _storage.write(key: _kUser, value: jsonEncode(user));
    notifyListeners();
  }

  Future<void> clear() async {
    _token = null;
    _user = null;
    await _storage.delete(key: _kToken);
    await _storage.delete(key: _kUser);
    notifyListeners();
  }
}

final secureStorageProvider = Provider<FlutterSecureStorage>(
  (ref) => const FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  ),
);

final sessionProvider = ChangeNotifierProvider<SessionStore>(
  (ref) => SessionStore(ref.watch(secureStorageProvider)),
);
