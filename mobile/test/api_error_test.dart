import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/network/api_error.dart';

DioException _err(dynamic data, {int status = 400}) {
  final req = RequestOptions(path: '/x');
  return DioException(
    requestOptions: req,
    response: Response(requestOptions: req, statusCode: status, data: data),
    type: DioExceptionType.badResponse,
  );
}

void main() {
  test('string detail', () {
    final e = ApiError.fromDio(_err({'detail': 'bad creds'}));
    expect(e.message, 'bad creds');
    expect(e.statusCode, 400);
  });

  test('list detail joins msgs', () {
    final e = ApiError.fromDio(_err({
      'detail': [
        {'msg': 'field a required'},
        {'msg': 'field b required'},
      ]
    }));
    expect(e.message, 'field a required; field b required');
  });

  test('object detail stringifies', () {
    final e = ApiError.fromDio(_err({'detail': {'code': 7}}));
    expect(e.message, contains('7'));
  });

  test('no detail falls back to type message', () {
    final req = RequestOptions(path: '/x');
    final e = ApiError.fromDio(DioException(
      requestOptions: req,
      type: DioExceptionType.connectionError,
    ));
    expect(e.message.toLowerCase(), contains('reach'));
  });
}
