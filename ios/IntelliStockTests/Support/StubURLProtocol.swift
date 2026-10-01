import Foundation
@testable import IntelliStock

/// Intercepts every request made through `StubURLProtocol.session` and answers
/// it from `handler`. Records the requests (with their bodies) so repository
/// tests can assert method, path, query and body — the same checks the Dart
/// fakes made. Suites that use it must be `.serialized`.
nonisolated final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    typealias Handler = @Sendable (URLRequest) throws -> (Int, [String: String], Data)

    private static let lock = NSLock()
    nonisolated(unsafe) private static var _handler: Handler?
    nonisolated(unsafe) private static var _requests: [URLRequest] = []

    static var handler: Handler? {
        get { lock.withLock { _handler } }
        set { lock.withLock { _handler = newValue } }
    }

    static var requests: [URLRequest] { lock.withLock { _requests } }

    static func reset() {
        lock.withLock {
            _handler = nil
            _requests = []
        }
    }

    /// Answer every request with `status`, `body` and `headers`.
    static func respond(status: Int = 200, json body: String = "{}", headers: [String: String] = [:]) {
        handler = { _ in (status, headers, Data(body.utf8)) }
    }

    /// Fail every request with a transport error.
    static func fail(_ code: URLError.Code) {
        handler = { _ in throw URLError(code) }
    }

    static var session: URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: config)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var recorded = request
        if recorded.httpBody == nil, let stream = request.httpBodyStream {
            recorded.httpBody = Data(reading: stream)
        }
        Self.lock.withLock { Self._requests.append(recorded) }

        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }
        do {
            let (status, headers, data) = try handler(recorded)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

nonisolated extension Data {
    init(reading stream: InputStream) {
        self.init()
        stream.open()
        defer { stream.close() }
        let size = 4096
        var buffer = [UInt8](repeating: 0, count: size)
        while stream.hasBytesAvailable {
            let read = stream.read(&buffer, maxLength: size)
            if read <= 0 { break }
            append(buffer, count: read)
        }
    }
}

extension URLRequest {
    /// The decoded JSON body, or `.null`.
    nonisolated var jsonBody: JSON {
        guard let httpBody, !httpBody.isEmpty else { return .null }
        return (try? JSON(data: httpBody)) ?? .null
    }

    /// Query items as a dictionary (last value wins).
    nonisolated var queryItems: [String: String] {
        guard let url, let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return [:] }
        return Dictionary(items.map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { _, last in last })
    }
}
