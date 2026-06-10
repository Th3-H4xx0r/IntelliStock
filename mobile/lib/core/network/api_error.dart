import 'package:dio/dio.dart';

/// A user-facing API error, derived from a FastAPI `{detail}` envelope.
///
/// `detail` may be a String, a list of `{msg|message}` validation objects, or
/// an arbitrary object. We flatten all three into a single message string.
class ApiError implements Exception {
  ApiError(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => message;

  static ApiError fromDio(DioException e) {
    final status = e.response?.statusCode;
    final data = e.response?.data;
    final detail = (data is Map) ? data['detail'] : null;

    String message;
    if (detail is String) {
      message = detail;
    } else if (detail is List) {
      message = detail
          .map((d) {
            if (d is Map) return d['msg'] ?? d['message'] ?? d.toString();
            return d.toString();
          })
          .join('; ');
    } else if (detail != null) {
      message = detail.toString();
    } else {
      message = switch (e.type) {
        DioExceptionType.connectionTimeout ||
        DioExceptionType.sendTimeout ||
        DioExceptionType.receiveTimeout =>
          'Request timed out. Check your connection and try again.',
        DioExceptionType.connectionError =>
          'Cannot reach the server. Check your connection.',
        _ => e.message ?? 'Something went wrong.',
      };
    }
    return ApiError(message, statusCode: status);
  }
}
