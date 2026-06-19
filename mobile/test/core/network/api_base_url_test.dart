import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_base_url.dart';

void main() {
  group('normalizeBaseUrl', () {
    test('trims whitespace and reduces to the origin', () {
      expect(normalizeBaseUrl('  https://api.example.com/  '), 'https://api.example.com');
      expect(normalizeBaseUrl('https://api.example.com'), 'https://api.example.com');
      expect(normalizeBaseUrl('http://1.2.3.4:8000/'), 'http://1.2.3.4:8000');
    });
    test('strips multiple trailing slashes and any path/query/fragment', () {
      expect(normalizeBaseUrl('https://host//'), 'https://host');
      expect(normalizeBaseUrl('https://api.example.com/v1/'), 'https://api.example.com');
      expect(normalizeBaseUrl('https://host:8000/api?x=1#f'), 'https://host:8000');
    });
    test('empty/whitespace -> empty string', () {
      expect(normalizeBaseUrl(''), '');
      expect(normalizeBaseUrl('   '), '');
    });
    test('non-http(s) / hostless inputs returned (for isValid to reject)', () {
      expect(normalizeBaseUrl('notaurl'), 'notaurl');
      expect(normalizeBaseUrl('ftp://example.com'), 'ftp://example.com');
    });
  });

  group('isValidBaseUrl', () {
    test('accepts http/https with a host (path is allowed but stripped)', () {
      expect(isValidBaseUrl('https://api.example.com'), isTrue);
      expect(isValidBaseUrl('http://1.2.3.4:8000'), isTrue);
      expect(isValidBaseUrl('  https://api.example.com/  '), isTrue);
      expect(isValidBaseUrl('https://host/api'), isTrue);
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
