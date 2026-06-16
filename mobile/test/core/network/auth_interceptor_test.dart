import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_client.dart';

void main() {
  group('refreshedTokenFromHeaders', () {
    test('returns the token when X-Refreshed-Token is present', () {
      final h = Headers.fromMap({
        'X-Refreshed-Token': ['new.jwt.token']
      });
      expect(refreshedTokenFromHeaders(h), 'new.jwt.token');
    });

    test('returns null when the header is absent', () {
      final h = Headers.fromMap({
        'Content-Type': ['application/json']
      });
      expect(refreshedTokenFromHeaders(h), isNull);
    });

    test('returns null when the header is blank', () {
      final h = Headers.fromMap({
        'X-Refreshed-Token': ['']
      });
      expect(refreshedTokenFromHeaders(h), isNull);
    });
  });
}
