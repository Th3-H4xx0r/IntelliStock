import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_base_url.dart';

void main() {
  group('normalizeBaseUrl', () {
    test('trims whitespace and strips a single trailing slash', () {
      expect(normalizeBaseUrl('  https://api.example.com/  '), 'https://api.example.com');
      expect(normalizeBaseUrl('https://api.example.com'), 'https://api.example.com');
      expect(normalizeBaseUrl('http://1.2.3.4:8000/'), 'http://1.2.3.4:8000');
    });
    test('empty/whitespace -> empty string', () {
      expect(normalizeBaseUrl(''), '');
      expect(normalizeBaseUrl('   '), '');
    });
  });

  group('isValidBaseUrl', () {
    test('accepts http/https with a host', () {
      expect(isValidBaseUrl('https://api.example.com'), isTrue);
      expect(isValidBaseUrl('http://1.2.3.4:8000'), isTrue);
      expect(isValidBaseUrl('  https://api.example.com/  '), isTrue);
    });
    test('rejects empty, schemeless, non-http, hostless', () {
      expect(isValidBaseUrl(''), isFalse);
      expect(isValidBaseUrl('notaurl'), isFalse);
      expect(isValidBaseUrl('ftp://example.com'), isFalse);
      expect(isValidBaseUrl('https://'), isFalse);
      expect(isValidBaseUrl('api.example.com'), isFalse);
    });
  });
}
