import Foundation

/// Where `ApiClient` reads the bearer token and reports token changes.
/// `SessionStore` conforms; tests use a fake.
@MainActor
protocol ApiTokenSource: AnyObject, Sendable {
    var token: String? { get }
    /// A slid (renewed) JWT handed back in `x-refreshed-token`.
    func setToken(_ token: String) async
    /// Called on any 401 — the gates then route back to Login.
    func clear() async
}

extension ApiTokenSource {
    /// A 401 ends the session only if the session still holds the token the
    /// request was sent with: a late 401 for a signed-out or replaced token
    /// must not clear a newer session.
    func clear(ifCurrent sentToken: String?) async {
        guard token == sentToken else { return }
        await clear()
    }

    /// Accepts a renewed token only for the session it was issued to: a late
    /// response after Sign Out or a server change must not write the old
    /// session back.
    func setToken(_ fresh: String, replacing sentToken: String?) async {
        guard let sentToken, !sentToken.isEmpty, token == sentToken else { return }
        await setToken(fresh)
    }
}

/// The typed HTTP client every repository uses, ported from `api_client.dart`
/// (Dio + `AuthInterceptor`).
///
/// - Sends `Accept: application/json`, `Authorization: Bearer <token>` when a
///   token exists, and `Content-Type: application/json` on POST/PUT/PATCH.
/// - A successful response carrying `x-refreshed-token` updates the session
///   without waiting — if the session still holds the token the request was
///   sent with.
/// - A 401 clears the session — under the same condition.
/// - Failures throw `ApiError`; a cancelled request (the calling task was
///   cancelled, e.g. its view went away) throws `CancellationError`, which
///   callers leave out of their state.
///
/// Requests run off the main actor; only the token read and the session
/// callbacks hop to it.
nonisolated final class ApiClient: Sendable {
    static let refreshedTokenHeader = "x-refreshed-token"

    let baseURL: String
    private let tokens: (any ApiTokenSource)?
    private let session: URLSession

    init(baseURL: String, tokens: (any ApiTokenSource)?, session: URLSession = ApiClient.makeSession()) {
        self.baseURL = baseURL
        self.tokens = tokens
        self.session = session
    }

    /// Dio used a 15 s connect and 30 s receive timeout. URLSession has no
    /// separate connect timeout; its request timeout is the idle time between
    /// bytes, which matches the receive timeout.
    static func makeSession() -> URLSession {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.waitsForConnectivity = false
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: config)
    }

    // MARK: Verbs

    func get(_ path: String, query: [String: JSON] = [:]) async throws -> JSON {
        try await send("GET", path, query: query, body: nil)
    }

    func post(_ path: String, body: JSON? = nil, query: [String: JSON] = [:]) async throws -> JSON {
        try await send("POST", path, query: query, body: body)
    }

    func put(_ path: String, body: JSON? = nil) async throws -> JSON {
        try await send("PUT", path, query: [:], body: body)
    }

    func patch(_ path: String, body: JSON? = nil) async throws -> JSON {
        try await send("PATCH", path, query: [:], body: body)
    }

    func delete(_ path: String, query: [String: JSON] = [:]) async throws -> JSON {
        try await send("DELETE", path, query: query, body: nil)
    }

    // MARK: Plumbing

    /// The renewed JWT carried by a response, or nil when absent or blank.
    static func refreshedToken(from response: HTTPURLResponse) -> String? {
        guard let value = response.value(forHTTPHeaderField: refreshedTokenHeader), !value.isEmpty else { return nil }
        return value
    }

    @concurrent
    private func send(_ method: String, _ path: String, query: [String: JSON], body: JSON?) async throws -> JSON {
        guard let url = makeURL(path: path, query: query) else {
            throw ApiError(message: "Something went wrong.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // The token this request carries; the 401 and refreshed-token
        // handling below only act while the session still holds it.
        let sentToken = await tokens?.token
        if let sentToken, !sentToken.isEmpty {
            request.setValue("Bearer \(sentToken)", forHTTPHeaderField: "Authorization")
        }
        if method == "POST" || method == "PUT" || method == "PATCH" {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let body {
            request.httpBody = try body.data()
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as URLError {
            throw ApiError.from(status: nil, body: nil, transport: error)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw ApiError(message: "Something went wrong.")
        }

        guard let http = response as? HTTPURLResponse else {
            throw ApiError(message: "Something went wrong.")
        }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 401, let tokens {
                await tokens.clear(ifCurrent: sentToken)
            }
            throw ApiError.from(status: http.statusCode, body: data, transport: nil)
        }

        if let fresh = Self.refreshedToken(from: http), let tokens {
            // Fire-and-forget, as the Dio interceptor did.
            Task { @MainActor in await tokens.setToken(fresh, replacing: sentToken) }
        }
        return Self.decode(data)
    }

    /// Dio decoded JSON bodies and handed anything else back as a String.
    private static func decode(_ data: Data) -> JSON {
        if data.isEmpty { return .null }
        if let json = try? JSON(data: data) { return json }
        return .string(String(decoding: data, as: UTF8.self))
    }

    private func makeURL(path: String, query: [String: JSON]) -> URL? {
        var text = baseURL + path
        let pairs = query.keys.sorted().flatMap { key -> [String] in
            let value = query[key]!
            switch value {
            case .null:
                return []
            case .array(let items):
                return items.filter { !$0.isNull }.map { "\(Self.encode(key))=\(Self.encode($0.dartDescription))" }
            default:
                return ["\(Self.encode(key))=\(Self.encode(value.dartDescription))"]
            }
        }
        if !pairs.isEmpty {
            text += (text.contains("?") ? "&" : "?") + pairs.joined(separator: "&")
        }
        return URL(string: text)
            ?? text.addingPercentEncoding(withAllowedCharacters: .urlFragmentAllowed).flatMap(URL.init(string:))
    }

    /// Dart's `Uri.encodeQueryComponent`: letters, digits and `-_.!~*'()` pass
    /// through, a space becomes `+`, everything else is percent-encoded UTF-8.
    private static func encode(_ s: String) -> String {
        var out = ""
        for byte in s.utf8 {
            switch byte {
            case UInt8(ascii: "a")...UInt8(ascii: "z"), UInt8(ascii: "A")...UInt8(ascii: "Z"),
                 UInt8(ascii: "0")...UInt8(ascii: "9"),
                 UInt8(ascii: "-"), UInt8(ascii: "_"), UInt8(ascii: "."), UInt8(ascii: "!"),
                 UInt8(ascii: "~"), UInt8(ascii: "*"), UInt8(ascii: "'"), UInt8(ascii: "("), UInt8(ascii: ")"):
                out.unicodeScalars.append(Unicode.Scalar(byte))
            case UInt8(ascii: " "):
                out += "+"
            default:
                out += String(format: "%%%02X", byte)
            }
        }
        return out
    }
}
