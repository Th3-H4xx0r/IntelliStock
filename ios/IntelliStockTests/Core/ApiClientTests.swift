import Foundation
import Testing
@testable import IntelliStock

/// Ported from test/api_error_test.dart and
/// test/core/network/auth_interceptor_test.dart, plus the Dio interceptor
/// behaviour those tests leaned on (bearer header, content type, refreshed
/// token, 401 → session cleared).
@MainActor
final class FakeTokens: ApiTokenSource {
    var token: String?
    var setTokens: [String] = []
    var clears = 0

    init(token: String? = nil) { self.token = token }

    func setToken(_ token: String) async {
        setTokens.append(token)
        self.token = token
    }

    func clear() async {
        clears += 1
        token = nil
    }
}

struct ApiErrorTests {
    private func error(status: Int = 400, body: String) -> ApiError {
        ApiError.from(status: status, body: Data(body.utf8), transport: nil)
    }

    @Test func stringDetail() {
        let e = error(body: #"{"detail": "bad creds"}"#)
        #expect(e.message == "bad creds")
        #expect(e.statusCode == 400)
    }

    @Test func listDetailJoinsMsgs() {
        let e = error(body: #"{"detail": [{"msg": "field a required"}, {"msg": "field b required"}]}"#)
        #expect(e.message == "field a required; field b required")
    }

    @Test func listDetailFallsBackToMessageThenToString() {
        let e = error(body: #"{"detail": [{"message": "m"}, "plain"]}"#)
        #expect(e.message == "m; plain")
    }

    @Test func objectDetailStringifies() {
        let e = error(body: #"{"detail": {"code": 7}}"#)
        #expect(e.message.contains("7"))
    }

    @Test func connectionErrorMentionsReach() {
        let e = ApiError.from(status: nil, body: nil, transport: URLError(.cannotConnectToHost))
        #expect(e.message == "Cannot reach the server. Check your connection.")
        #expect(e.message.lowercased().contains("reach"))
    }

    @Test func timeoutMessage() {
        let e = ApiError.from(status: nil, body: nil, transport: URLError(.timedOut))
        #expect(e.message == "Request timed out. Check your connection and try again.")
    }

    @Test func badResponseWithoutDetailNamesTheStatus() {
        let e = error(status: 502, body: "<html>Bad Gateway</html>")
        #expect(e.message == "The server responded with status 502.")
        #expect(e.statusCode == 502)
    }
}

/// Each test owns a `DataStub` host, so this suite runs in parallel with the
/// rest.
@MainActor
struct ApiClientTests {
    private let stub = DataStub()

    private func client(_ tokens: FakeTokens? = nil) -> ApiClient {
        stub.client(tokens: tokens)
    }

    @Test func refreshedTokenHeaderIsReadCaseInsensitively() {
        let r = HTTPURLResponse(url: URL(string: "https://x")!, statusCode: 200, httpVersion: nil,
                                headerFields: ["X-Refreshed-Token": "new.jwt.token"])!
        #expect(ApiClient.refreshedToken(from: r) == "new.jwt.token")
        let absent = HTTPURLResponse(url: URL(string: "https://x")!, statusCode: 200, httpVersion: nil,
                                     headerFields: ["Content-Type": "application/json"])!
        #expect(ApiClient.refreshedToken(from: absent) == nil)
        let blank = HTTPURLResponse(url: URL(string: "https://x")!, statusCode: 200, httpVersion: nil,
                                    headerFields: ["X-Refreshed-Token": ""])!
        #expect(ApiClient.refreshedToken(from: blank) == nil)
    }

    @Test func getSendsBearerAndAcceptAndParsesJSON() async throws {
        stub.respond(json: #"{"ok": true, "n": 3}"#)
        let json = try await client(FakeTokens(token: "abc")).get("/instances")
        #expect(json["ok"].bool)
        #expect(json["n"].int == 3)
        let req = try #require(stub.requests.first)
        #expect(req.httpMethod == "GET")
        #expect(req.url?.absoluteString == "\(stub.baseURL)/instances")
        #expect(req.value(forHTTPHeaderField: "Authorization") == "Bearer abc")
        #expect(req.value(forHTTPHeaderField: "Accept") == "application/json")
        #expect(req.value(forHTTPHeaderField: "Content-Type") == nil)
    }

    @Test func noTokenMeansNoAuthorizationHeader() async throws {
        _ = try await client(FakeTokens(token: nil)).get("/health")
        #expect(stub.requests.first?.value(forHTTPHeaderField: "Authorization") == nil)
    }

    @Test func postPutPatchSendJSONContentTypeAndBody() async throws {
        let c = client()
        _ = try await c.post("/a", body: ["x": 1, "y": "z"])
        _ = try await c.put("/b", body: ["k": true])
        _ = try await c.patch("/c", body: nil)
        let reqs = stub.requests
        #expect(reqs.map(\.httpMethod) == ["POST", "PUT", "PATCH"])
        #expect(reqs.allSatisfy { $0.value(forHTTPHeaderField: "Content-Type") == "application/json" })
        #expect(reqs[0].jsonBody == ["x": 1, "y": "z"])
        #expect(reqs[1].jsonBody == ["k": true])
    }

    @Test func queryEncodesLikeDioAndDropsNulls() async throws {
        _ = try await client().get("/s", query: ["q": "BRK+B a&b", "limit": 20, "on": true, "skip": nil])
        let url = try #require(stub.requests.first?.url?.absoluteString)
        #expect(url.contains("q=BRK%2BB+a%26b"))
        #expect(url.contains("limit=20"))
        #expect(url.contains("on=true"))
        #expect(!url.contains("skip"))
    }

    @Test func refreshedTokenOnSuccessIsHandedToTheSession() async throws {
        stub.respond(json: "{}", headers: ["x-refreshed-token": "slid.jwt"])
        let tokens = FakeTokens(token: "old")
        _ = try await client(tokens).get("/me")
        #expect(await eventually { tokens.setTokens == ["slid.jwt"] })
    }

    @Test func unauthorizedClearsTheSessionAndThrows() async {
        stub.respond(status: 401, json: #"{"detail": "Not authenticated"}"#)
        let tokens = FakeTokens(token: "stale")
        await #expect(throws: ApiError(message: "Not authenticated", statusCode: 401)) {
            _ = try await client(tokens).get("/instances")
        }
        #expect(tokens.clears == 1)
    }

    @Test func otherErrorsDoNotClearTheSession() async {
        stub.respond(status: 404, json: #"{"detail": "Not Found"}"#)
        let tokens = FakeTokens(token: "t")
        await #expect(throws: ApiError(message: "Not Found", statusCode: 404)) {
            _ = try await client(tokens).delete("/x/1")
        }
        #expect(tokens.clears == 0)
    }

    @Test func transportFailureMapsToFriendlyMessage() async {
        stub.fail(.notConnectedToInternet)
        await #expect(throws: ApiError(message: "Cannot reach the server. Check your connection.", statusCode: nil)) {
            _ = try await client().get("/x")
        }
    }

    @Test func emptyBodyIsNullAndPlainTextIsAString() async throws {
        stub.respond(json: "")
        #expect(try await client().post("/a").isNull)
        stub.respond(json: "pong")
        #expect(try await client().get("/b") == .string("pong"))
    }

    // MARK: Cancellation (fix round 1, I2)

    /// A request whose task is cancelled (its view went away) throws
    /// `CancellationError`, never an `ApiError` "Something went wrong.".
    @Test func cancellingTheCallerThrowsCancellationError() async {
        let c = client()
        let task = Task { try await c.get("/slow") }
        task.cancel()
        let result = await task.result
        guard case .failure(let error) = result else {
            Issue.record("expected the cancelled request to throw")
            return
        }
        #expect(error is CancellationError)
        #expect(error.isCancellation)
    }

    @Test func aCancelledUrlErrorIsACancellation() async {
        stub.fail(.cancelled)
        await #expect(throws: CancellationError.self) {
            _ = try await client().get("/x")
        }
    }

    // MARK: Token-scoped refresh and 401 (fix round 1, I5)
    //
    // The stub handler changes the session on the main thread while the
    // request is in flight, so the response lands "late".

    /// A renewal that lands after Sign Out must not sign the old session
    /// back in.
    @Test func aLateRefreshedTokenAfterSignOutIsIgnored() async throws {
        let tokens = FakeTokens(token: "old")
        stub.headers = ["X-Refreshed-Token": "slid.jwt"]
        stub.handler = { _ in
            DispatchQueue.main.sync { MainActor.assumeIsolated { tokens.token = nil } }
            return (200, "{}")
        }
        _ = try await client(tokens).get("/me")
        try await Task.sleep(for: .milliseconds(50))
        #expect(tokens.setTokens.isEmpty)
        #expect(tokens.token == nil)
    }

    /// A renewal issued for a token the session has since replaced is ignored.
    @Test func aLateRefreshedTokenForAReplacedTokenIsIgnored() async throws {
        let tokens = FakeTokens(token: "old")
        stub.headers = ["X-Refreshed-Token": "slid.jwt"]
        stub.handler = { _ in
            DispatchQueue.main.sync { MainActor.assumeIsolated { tokens.token = "new" } }
            return (200, "{}")
        }
        _ = try await client(tokens).get("/me")
        try await Task.sleep(for: .milliseconds(50))
        #expect(tokens.setTokens.isEmpty)
        #expect(tokens.token == "new")
    }

    /// A 401 for an old token must not clear the newer session.
    @Test func aLate401ForAReplacedTokenDoesNotClearTheNewSession() async {
        let tokens = FakeTokens(token: "old")
        stub.handler = { _ in
            DispatchQueue.main.sync { MainActor.assumeIsolated { tokens.token = "new" } }
            return (401, #"{"detail": "expired"}"#)
        }
        _ = try? await client(tokens).get("/instances")
        #expect(tokens.clears == 0)
        #expect(tokens.token == "new")
    }

    /// A request sent without a token never clears a session that signed in
    /// meanwhile.
    @Test func a401ForAnUnauthenticatedRequestKeepsALaterSession() async {
        let tokens = FakeTokens(token: nil)
        stub.handler = { _ in
            DispatchQueue.main.sync { MainActor.assumeIsolated { tokens.token = "fresh" } }
            return (401, "{}")
        }
        _ = try? await client(tokens).get("/me")
        #expect(tokens.clears == 0)
    }
}
