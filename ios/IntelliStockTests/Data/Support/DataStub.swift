import Foundation
@testable import IntelliStock

/// A request stub for repository tests that is safe to use from suites
/// running in parallel.
///
/// `StubURLProtocol` keeps one global handler, so two suites that use it at
/// the same time (Swift Testing runs suites in parallel; `.serialized` only
/// orders tests inside one suite) overwrite each other's handler and request
/// log. Each `DataStub` instead owns a unique host: its `client` points at
/// that host, and `DataStubProtocol` routes every request by host to the stub
/// that owns it. Records requests (with bodies) so tests assert method, path,
/// query and body exactly as the Dart fakes did.
nonisolated final class DataStub: @unchecked Sendable {
    typealias Handler = @Sendable (URLRequest) throws -> (status: Int, body: String)

    let host = "s\(UUID().uuidString.prefix(8).lowercased()).stub.test"
    private let lock = NSLock()
    private var _handler: Handler
    private var _requests: [URLRequest] = []

    init(status: Int = 200, json: String = "{}") {
        _handler = { _ in (status, json) }
        DataStubProtocol.register(self)
    }

    deinit { DataStubProtocol.unregister(host) }

    /// An `ApiClient` whose requests reach this stub only.
    var client: ApiClient {
        ApiClient(baseURL: "https://\(host)", tokens: nil, session: DataStubProtocol.session)
    }

    var handler: Handler {
        get { lock.withLock { _handler } }
        set { lock.withLock { _handler = newValue } }
    }

    /// Answer every request with `status` and `json`.
    func respond(status: Int = 200, json: String) {
        handler = { _ in (status, json) }
    }

    var requests: [URLRequest] { lock.withLock { _requests } }

    /// The most recent request.
    var last: URLRequest? { requests.last }

    fileprivate func handle(_ request: URLRequest) throws -> (Int, Data) {
        let handler = lock.withLock { () -> Handler in
            _requests.append(request)
            return _handler
        }
        let (status, body) = try handler(request)
        return (status, Data(body.utf8))
    }
}

nonisolated final class DataStubProtocol: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var stubs: [String: Weak] = [:]

    private struct Weak {
        weak var stub: DataStub?
    }

    static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [DataStubProtocol.self]
        return URLSession(configuration: config)
    }()

    static func register(_ stub: DataStub) {
        lock.withLock { stubs[stub.host] = Weak(stub: stub) }
    }

    static func unregister(_ host: String) {
        lock.withLock { _ = stubs.removeValue(forKey: host) }
    }

    private static func stub(for request: URLRequest) -> DataStub? {
        guard let host = request.url?.host else { return nil }
        return lock.withLock { stubs[host]?.stub }
    }

    override class func canInit(with request: URLRequest) -> Bool { stub(for: request) != nil }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var recorded = request
        if recorded.httpBody == nil, let stream = request.httpBodyStream {
            recorded.httpBody = Data(reading: stream)
        }
        guard let stub = Self.stub(for: recorded) else {
            client?.urlProtocol(self, didFailWithError: URLError(.unknown))
            return
        }
        do {
            let (status, data) = try stub.handle(recorded)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: [:])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

nonisolated extension URLRequest {
    /// The request path, e.g. `/instances/i1/wheel`.
    var path: String { url?.path ?? "" }

    var method: String { httpMethod ?? "" }

    /// Every query item in order, duplicates kept.
    var queryPairs: [(String, String)] {
        guard let url, let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return [] }
        return items.map { ($0.name, $0.value ?? "") }
    }
}

/// `closeTo` for optional doubles.
nonisolated func close(_ a: Double?, _ b: Double, _ tolerance: Double = 1e-9) -> Bool {
    guard let a else { return false }
    return abs(a - b) <= tolerance
}
