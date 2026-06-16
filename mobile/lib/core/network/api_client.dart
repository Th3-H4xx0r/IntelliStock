import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'api_config.dart';
import 'api_error.dart';
import 'session.dart';

/// Response header the backend uses to hand back a slid (renewed) token.
const _kRefreshedTokenHeader = 'x-refreshed-token';

/// The renewed JWT carried by a response, or null when absent/blank.
String? refreshedTokenFromHeaders(Headers headers) {
  final v = headers.value(_kRefreshedTokenHeader);
  return (v == null || v.isEmpty) ? null : v;
}

/// Adds the bearer token + JSON content-type, and clears the session on 401.
class AuthInterceptor extends Interceptor {
  AuthInterceptor(this._session, this._onUnauthorized);

  final SessionStore _session;
  final void Function() _onUnauthorized;

  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    final token = _session.token;
    if (token != null && token.isNotEmpty) {
      options.headers['Authorization'] = 'Bearer $token';
    }
    final method = options.method.toUpperCase();
    if (method == 'POST' || method == 'PUT' || method == 'PATCH') {
      options.headers.putIfAbsent('Content-Type', () => 'application/json');
    }
    handler.next(options);
  }

  @override
  void onResponse(Response response, ResponseInterceptorHandler handler) {
    final fresh = refreshedTokenFromHeaders(response.headers);
    if (fresh != null) {
      // Fire-and-forget: persist the slid token + re-mirror it to the widget.
      _session.setToken(fresh);
    }
    handler.next(response);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    if (err.response?.statusCode == 401) {
      _onUnauthorized();
    }
    handler.next(err);
  }
}

/// Thin typed wrapper over dio that throws [ApiError] on failure.
class ApiClient {
  ApiClient(this._dio);

  final Dio _dio;

  Future<T> get<T>(String path, {Map<String, dynamic>? query}) =>
      _run<T>(() => _dio.get(path, queryParameters: query));

  Future<T> post<T>(String path, {Object? body, Map<String, dynamic>? query}) =>
      _run<T>(() => _dio.post(path, data: body, queryParameters: query));

  Future<T> put<T>(String path, {Object? body}) =>
      _run<T>(() => _dio.put(path, data: body));

  Future<T> patch<T>(String path, {Object? body}) =>
      _run<T>(() => _dio.patch(path, data: body));

  Future<T> delete<T>(String path, {Map<String, dynamic>? query}) =>
      _run<T>(() => _dio.delete(path, queryParameters: query));

  Future<T> _run<T>(Future<Response> Function() call) async {
    try {
      final res = await call();
      return res.data as T;
    } on DioException catch (e) {
      throw ApiError.fromDio(e);
    }
  }
}

final dioProvider = Provider<Dio>((ref) {
  final session = ref.watch(sessionProvider);
  final dio = Dio(BaseOptions(
    baseUrl: ApiConfig.baseUrl,
    connectTimeout: ApiConfig.connectTimeout,
    receiveTimeout: ApiConfig.receiveTimeout,
    headers: {'Accept': 'application/json'},
  ));
  dio.interceptors.add(AuthInterceptor(session, () {
    // Clearing the session triggers the router's redirect to /login.
    session.clear();
  }));
  return dio;
});

final apiClientProvider = Provider<ApiClient>(
  (ref) => ApiClient(ref.watch(dioProvider)),
);
