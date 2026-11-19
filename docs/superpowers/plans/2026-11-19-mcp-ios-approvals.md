# iOS MCP Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The operator approves or denies Jarvis's gated MCP requests on their iPhone — every decision signed by a Face ID-gated Secure Enclave key — and registers that iPhone as an approver in Settings.

**Architecture:** A new `Features/Approvals` feature in the native app: wire models and one repository for the contract's `/mcp/approvals` and `/mcp/approval-devices` routes; a signer protocol with a Secure Enclave implementation (device) and a software one (simulator and tests); `@Observable` models for the list, the detail's decision state machine and device registration; SwiftUI screens reached from the More tab, Settings › Security, and a tapped `MCP_APPROVAL` push. The phone recomputes `body_sha256` from the parameters it displays and will not sign an approval whose fingerprint does not match.

**Tech Stack:** Swift 6 / SwiftUI on iOS 26; Security (`SecKey`, Secure Enclave), LocalAuthentication, CryptoKit (SHA-256, `SecureEnclave.isAvailable`, test-side ECDSA verification); Swift Testing; XcodeGen synced folders.

**Spec:** `/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.worktrees/mcp-api-gate/docs/superpowers/specs/2026-11-19-intellistock-mcp-server-design.md` (§5.1 "Approval gate", "Device registration", "Push"; §5.3 iOS) and the binding contract `/Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.worktrees/mcp-api-gate/docs/superpowers/specs/2026-11-19-intellistock-mcp-contract.md` (§5 Approvals). This is build (E) of the spec's delivery list (§8 item 5). It needs no backend change; end-to-end use needs build (C) deployed.

## Global Constraints

- Work in `.worktrees/ios-mcp-approvals` on branch `feat/ios-mcp-approvals` cut from `origin/main` (26cd7546 when this was written). Paths below are relative to that worktree.
- The contract wins. If anything here disagrees with contract §5, follow the contract and note the mismatch in the PR body; do not edit backend code or the contract on this branch.
- Wire values from contract §5, verbatim:
  - `GET /mcp/approvals?status=pending|all&limit=50` → `{"approvals": [Approval]}`; `GET /mcp/approvals/{id}` → Approval.
  - `POST /mcp/approvals/{id}/decision` body `{"decision": "approve"|"deny", "body_sha256": str, "ts": int (unix seconds), "device_id": str, "signature": base64(DER ECDSA P-256 SHA-256)}` → 200 Approval (after execution when approved) | 400 bad input | 403 bad signature / unknown or not-yet-active device | 409 not pending | 410 expired.
  - Signed message: UTF-8 of `f"{id}|{body_sha256}|{decision}|{ts}"`; the server accepts `|now - ts| <= 60`.
  - `POST /mcp/approval-devices {"name": str, "public_key": base64(X9.62 uncompressed P-256 point, 65 bytes)}` → 201 `{"device_id": str, "active_at": ISO-8601 UTC (now+10 min)}`; `GET /mcp/approval-devices` → `{"devices": [...]}`; `DELETE /mcp/approval-devices/{device_id}` → 204.
  - `body_sha256` = sha256 hex of canonical JSON of `{"method", "path", "query", "body"}` (sort_keys, separators `(",", ":")`, ensure_ascii False).
  - Approval object keys: `id, created_at, expires_at, status (pending|approved|denied|expired|executed|failed), summary, money_state (live|unknown|protected|irreversible), client, method, path, query, body, body_sha256, result_status, result, decided_at, device_id`. The human sees `result` unredacted; it is capped at 256 KB, with `"truncated": true` when cut.
  - TTL 15 minutes. Push: category `"MCP_APPROVAL"`, title "Jarvis wants to act", body = summary, data `{"approval_id": id}`. `backend/apns_sender.py _build_payload` puts `category` and every data key at the top level of the payload, beside `aps`.
- Key: Secure Enclave P-256 (`kSecAttrTokenIDSecureEnclave`), access control `[.privateKeyUsage, .biometryCurrentSet]` with `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`, signing `.ecdsaSignatureMessageX962SHA256` (DER); public key = `SecKeyCopyExternalRepresentation` (65 bytes, `0x04‖X‖Y`), standard base64 with padding.
- Device builds never sign with a software key. `SoftwareApprovalSigner` is constructed only by `ApprovalSigners.platformDefault` under `#if targetEnvironment(simulator)`, and by tests. `SecureEnclaveApprovalSigner` is compiled for every destination and never falls back.
- Never log, print, toast or persist a signature, a private key or a JWT. Apart from the key itself, the only approval data written to the keychain store is the device record (server, device id, name, activation time).
- iOS 26.0, Swift 6.0, `SWIFT_DEFAULT_ACTOR_ISOLATION: MainActor`, approachable concurrency (`ios/project.yml`). Mark data types, protocols, repositories, signers and file-level constants `nonisolated`, as the existing `Data/` files do; view models are `@Observable final class` on the main actor.
- Apple frameworks only — no third-party packages (`ios/README.md`).
- Source folders are synced: add files only. Never edit `project.yml` or `project.pbxproj`.
- UI follows `ios/IntelliStock/DesignSystem/README.md`: DS fonts, colours and components; dark only; no gradients or glows; sentence case; no trailing period on titles, labels or buttons; hue only for gain/loss/amber. Text that came from the agent or the server (summary, parameters, results, error details) renders with `Text(verbatim:)` — never as Markdown or links.
- Do not edit — another session owns them: `ios/IntelliStock/Features/Swing/*`, `ios/IntelliStock/Features/Instances/Views/InstanceDetailView.swift`, `ios/IntelliStockTests/Features/Instances/*`, `ios/IntelliStockTests/Snapshots/SnapshotScreens+Shell.swift`. Do not use their helpers (`SwingTestSupport`) either; the approvals tests carry their own support file. Shared files this plan does touch are listed under "Shared files touched"; change only the lines named there. If another PR touching those files (e.g. `feat/wheel-regrade-manual-run`) lands first, rebase onto it before opening the PR.
- Tests: Swift Testing (`import Testing`, `@Test`, `#expect`, `#require`). Network tests go through `DataStub` (`ios/IntelliStockTests/Data/Support/DataStub.swift`). No real server, no Face ID prompt, no Secure Enclave in tests.
- Test command. `xct A B` in a step means the command below, with one `-only-testing:IntelliStockTests/<Suite>` per suite named. It uses its own simulator and DerivedData so it doesn't collide with the other session's builds. Pass = `** TEST SUCCEEDED **`. The full suite is the same command with no `-only-testing`.
  ```
  cd ios && xcodebuild -project IntelliStock.xcodeproj -scheme IntelliStock \
    -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max' \
    -derivedDataPath build/dd-approvals test \
    -only-testing:IntelliStockTests/<Suite> 2>&1 | tail -40
  ```
- CLAUDE.md: before editing an existing function or type, run `mcp__gitnexus__impact({target: "<symbol>", direction: "upstream"})` and report direct callers, affected processes and risk level; stop and warn on HIGH or CRITICAL. GitNexus may not index Swift symbols, or may answer UNKNOWN. In that case grep the callers (`/usr/bin/grep -rn`) and report those. Run `mcp__gitnexus__detect_changes()` before every commit and confirm only the task's files changed.
- No Python changes in this plan. (Project rules, should a fix ever spill into the backend: Python 3.11 syntax and stdlib only; `python -m pytest -q -p no:cacheprovider backend/tests` from the repo root; FakeStore; no network, brokers or LLMs; human JWT behaviour unchanged.) Real money trades through this API (instance alpaca-main). No test and no manual check approves anything against production; on-device checks use a paper or protected target.
- Commits: conventional style (`feat(ios): …`), message ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Merging to main redeploys the whole stack, alpaca-main included. The operator merges, outside market hours; this plan ends at an open PR.

## Review Focus

1. **The decision POST outlives the client's 30 s timeout while the server executes the request** (a money route such as start_instance). The app must not report failure or invite a blind retry. It re-reads the approval, shows "approved" (running) or the final result, and polls fast until it settles. Pinned by Task 4 `aTimeoutReReadsInsteadOfReportingFailure` and `a5xxIsTreatedAsUncertain`.
2. **Phone clock off by more than 60 s.** The server refuses the timestamp. The request stays pending and the app shows the server's reason plus a Date & Time hint. Pinned by Task 4 `clockSkew400AddsTheDateAndTimeHint`.
3. **Parameters with floats, Unicode, escapes or unusual key order.** The phone's fingerprint must equal Python's byte for byte on every realistic body, and fail closed otherwise: Approve blocked, Deny still possible. Pinned by Task 1 vectors A–C, `pythonFloatReprMatchesPythonRepr` and `sortsKeysByCodePointNotSwiftCollation`, and by Task 4 `fingerprintMismatchBlocksApproveButAllowsDeny`.
4. **A push or `intellistock://` link carrying a hostile approval id** (`../brokerages`, `a/b`, empty, 500 characters). The app ignores it; it never reaches an API path or the router. Pinned by Task 1 `approvalIdsAreValidated`, Task 2 `invalidIdsNeverReachTheNetwork`, Task 4 `invalidIdNeverHitsTheNetwork` and Task 7 `findsTheApprovalIdOnlyOnApprovalPushes`.
5. **The app ships before build (C) is deployed**, so every `/mcp/*` call answers 404. The list and device screens say "This server doesn't have Jarvis approvals yet…" instead of looping on a raw "Not Found". Pinned by Task 5 `aServerWithoutApprovalsSaysSo` and Task 6 `aServerWithoutApprovalsSaysSoOnTheList`.

---

## File map

New (all under `ios/`):

| File | Responsibility |
|---|---|
| `IntelliStock/Features/Approvals/Data/McpApproval.swift` | Wire models: approval, device, registration receipt, decision body, enums |
| `IntelliStock/Features/Approvals/Data/ApprovalFingerprint.swift` | Python-compatible canonical JSON + sha256, the signed message bytes, id validation |
| `IntelliStock/Features/Approvals/Data/ApprovalsRepository.swift` | The six HTTP calls; `ApprovalsSource` / `ApprovalDevicesSource` seams |
| `IntelliStock/Features/Approvals/Data/ApprovalDeviceStore.swift` | This iPhone's registration record in `SecureStorage`, per server |
| `IntelliStock/Features/Approvals/Signing/ApprovalSigner.swift` | Signer protocol, errors, shared constants, platform factory |
| `IntelliStock/Features/Approvals/Signing/SecureEnclaveApprovalSigner.swift` | Secure Enclave key + Face ID-gated signing |
| `IntelliStock/Features/Approvals/Signing/SoftwareApprovalSigner.swift` | Software P-256 key (simulator, tests) |
| `IntelliStock/Features/Approvals/Model/ApprovalGate.swift` | Pure "may this phone decide now?" rule and its copy |
| `IntelliStock/Features/Approvals/Model/ApprovalDetailModel.swift` | Load/poll one approval; the sign-and-send state machine |
| `IntelliStock/Features/Approvals/Model/ApprovalsListModel.swift` | Waiting/recent list, polled |
| `IntelliStock/Features/Approvals/Model/ApprovalPresentation.swift` | Countdown, labels, tones, parameter text |
| `IntelliStock/Features/Approvals/Model/ApprovalDevicesModel.swift` | Register / list / remove approver devices |
| `IntelliStock/Features/Approvals/Model/ApprovalPush.swift` | Reads an approval id from a tapped push |
| `IntelliStock/Features/Approvals/Views/ApprovalsView.swift` | The list screen |
| `IntelliStock/Features/Approvals/Views/ApprovalDetailView.swift` | The detail screen with Approve / Deny |
| `IntelliStock/Features/Approvals/Views/ApprovalComponents.swift` | Row, money-state line, JSON block |
| `IntelliStock/Features/Approvals/Views/ApprovalDeviceSettingsView.swift` | Settings › Jarvis approvals |
| `IntelliStockTests/Features/Approvals/*.swift` | Tests, plus `ApprovalsTestSupport.swift` (fakes and fixtures) |

Shared files touched (minimal, each named in its task):

| File | Change | Task |
|---|---|---|
| `IntelliStock/App/AppServices+Repositories.swift` | one line: `approvalsRepository` | 5 |
| `IntelliStock/App/AppServices.swift` | two lazy properties (they need the private `storage`); one new method `openApproval(_:)` | 5, 7 |
| `IntelliStock/Core/Navigation/Route.swift` | cases `approvalDevices`, `approvals`, `approval(String)` and their parse lines | 5, 6 |
| `IntelliStock/Core/Navigation/Route+Path.swift` | their paths | 5, 6 |
| `IntelliStock/Core/Navigation/RouteDestination.swift` | their views | 5, 6 |
| `IntelliStock/Features/Settings/Views/SettingsView.swift` | one row in the Security section | 5 |
| `IntelliStock/App/MoreTabView.swift` | one entry in `items` | 6 |
| `IntelliStock/App/AppDelegate.swift` | approval-push routing beside the update push | 7 |
| `IntelliStock/App/IntelliStockApp.swift` | one `attach(openApproval:)` call | 7 |

## Decisions made without the operator (review these)

1. **The phone checks the fingerprint.** It recomputes `body_sha256` from the method, path, query and body it shows, and blocks Approve on a mismatch. Deny stays possible, and always signs the server's `body_sha256`. The encoder reproduces Python's `json.dumps` byte for byte, Python float `repr` included. A body it cannot reproduce (an integer beyond 64 bits, say) fails closed.
2. **Approve asks once more before Face ID.** In-app Face ID needs no button press, so a stray tap on Approve would pass almost at once. Approve therefore opens a confirm alert (summary + money state) and then Face ID; Deny goes straight to Face ID. The push carries no action buttons, so nothing is approved from the lock screen.
3. **Face ID first, then the timestamp.** `authorize(reason:)` evaluates the key's access control first. Only then does the model take `ts` and sign, using the same `LAContext`, so time spent in the Face ID sheet doesn't eat into the 60 s window. The on-device check in Task 8 confirms there is exactly one Face ID sheet.
4. **Registration self-test.** After creating the key, registration signs and verifies a local test message (one Face ID), so a key that can't sign is never registered. "Register again" creates a new key, then deletes the old device on the server (best effort). A failed or cancelled registration leaves no key and no record.
5. **The registration record is per server.** It is stored with the server URL, so after a server switch the phone reads as unregistered rather than sending another server's device id.
6. **Approval and device ids must match `^[A-Za-z0-9_-]{1,128}$`.** Anything else is refused before any request or navigation. Build (C) must issue ids in that set; uuid4 hex and `secrets.token_urlsafe` both qualify.
7. **The device list's fields aren't pinned by the contract.** E reads `device_id` (falling back to `id`), `name`, `created_at` and `active_at`, and ignores the rest; if (C) names them differently, `McpApprovalDevice.init(json:)` is the only reader to change. If the 201 lacks `active_at`, the app assumes now + 10 min.
8. **One list request.** The list is `GET /mcp/approvals?status=all&limit=50`, split on the phone into "Waiting for you" (pending and unexpired, soonest expiry first) and "Recent" (newest first). The 50/day approval cap keeps every pending one inside the newest 50.
9. **No retry after an unconfirmed decision.** A transport error or a 5xx re-reads the approval instead of retrying. If it is still pending, the operator may decide again with a fresh signature, and the server's compare-and-swap keeps it to one execution.
10. **Entry points.** Approvals is the first item on the More tab; Settings › Security gets a "Jarvis approvals" row. No tab-bar badge (the push is the alert). No snapshot tests for the new screens: the snapshot harness sits beside a file another session is editing.
11. **Simulator.** It uses a software key kept in the keychain store, so the flow can be clicked through there. The device screen says so.

---

### Task 1: Approval wire types, fingerprint and signed message

**Files:**
- Create: `ios/IntelliStock/Features/Approvals/Data/McpApproval.swift`
- Create: `ios/IntelliStock/Features/Approvals/Data/ApprovalFingerprint.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalWireTests.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalFingerprintTests.swift`
- Copy: this plan to `docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md`

**Interfaces:**
- Consumes: `JSON`, `JSONObject` (`Core/JSON/JSON.swift`), `DartDateTime.tryParse` (`Core/Models/DartDateTime.swift`), CryptoKit `SHA256`.
- Produces:
  ```swift
  nonisolated struct McpApproval: Hashable, Sendable, Identifiable {
      let id: String; let status: String; let summary: String; let moneyState: String
      let client: String; let method: String; let path: String; let bodySha256: String
      let createdAt: Date?; let expiresAt: Date?; let decidedAt: Date?
      let query: JSON; let body: JSON; let result: JSON   // exactly as received; a missing key is .null
      let resultStatus: Int?; let deviceId: String?
      init(json: JSON)
      var isPending: Bool                  // status == "pending"
      var isTerminal: Bool                 // denied | expired | executed | failed
      var resultTruncated: Bool            // result["truncated"] == true
      func isExpired(at now: Date) -> Bool // now >= expiresAt; a missing expiresAt counts as expired
  }
  nonisolated struct McpApprovalDevice: Hashable, Sendable, Identifiable {
      let id: String; let name: String; let createdAt: Date?; let activeAt: Date?
      init(json: JSON)                     // id = device_id, else id
      func isActive(at now: Date) -> Bool  // activeAt != nil && now >= activeAt
  }
  nonisolated struct ApprovalDeviceReceipt: Hashable, Sendable { let deviceId: String; let activeAt: Date?; init(json: JSON) }
  nonisolated enum ApprovalDecision: String, Sendable { case approve, deny }
  nonisolated enum ApprovalListFilter: String, Sendable { case pending, all }
  nonisolated struct ApprovalDecisionBody: Hashable, Sendable {
      let decision: ApprovalDecision; let bodySha256: String; let ts: Int; let deviceId: String; let signature: Data
      var json: JSON   // keys in this order: decision, body_sha256, ts, device_id, signature (base64)
  }
  nonisolated enum ApprovalFingerprint {
      static func canonicalJSON(method: String, path: String, query: JSON, body: JSON) -> Data?  // nil: not representable
      static func sha256Hex(method: String, path: String, query: JSON, body: JSON) -> String?     // lowercase hex
      static func matches(_ approval: McpApproval) -> Bool   // sha256Hex(...) == approval.bodySha256.lowercased()
  }
  nonisolated func pythonFloatRepr(_ d: Double) -> String?   // nil for NaN / ±inf
  nonisolated enum ApprovalMessage { static func bytes(id: String, bodySha256: String, decision: ApprovalDecision, ts: Int) -> Data }
  nonisolated enum ApprovalIds { static func isValid(_ id: String) -> Bool }   // ^[A-Za-z0-9_-]{1,128}$
  ```

- [ ] **Step 1: Create the worktree and carry the plan**

  ```bash
  cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock
  git fetch origin && git worktree add .worktrees/ios-mcp-approvals -b feat/ios-mcp-approvals origin/main
  mkdir -p .worktrees/ios-mcp-approvals/docs/superpowers/plans
  cp .worktrees/mcp-api-gate/docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md .worktrees/ios-mcp-approvals/docs/superpowers/plans/
  ```
  Expected: `git -C .worktrees/ios-mcp-approvals status` shows the new branch and one untracked plan file.

- [ ] **Step 2: Write the failing tests**

  These vectors were produced with CPython's `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)` and `hashlib.sha256`. They are the cross-language pin, so copy them exactly.

  ```swift
  // ApprovalFingerprintTests.swift
  nonisolated let kVectorAHash = "15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d"

  struct ApprovalFingerprintTests {
      private func parse(_ text: String) throws -> JSON { try JSON(data: Data(text.utf8)) }

      @Test func matchesPythonForAnOrder() throws {
          let body = try parse(#"{"symbol":"TQQQ","qty":10,"side":"buy","type":"market","limit_price":null}"#)
          let bytes = try #require(ApprovalFingerprint.canonicalJSON(method: "POST", path: "/brokerages/b1/orders", query: .object(JSONObject()), body: body))
          #expect(String(decoding: bytes, as: UTF8.self) == #"{"body":{"limit_price":null,"qty":10,"side":"buy","symbol":"TQQQ","type":"market"},"method":"POST","path":"/brokerages/b1/orders","query":{}}"#)
          #expect(ApprovalFingerprint.sha256Hex(method: "POST", path: "/brokerages/b1/orders", query: .object(JSONObject()), body: body) == kVectorAHash)
      }

      @Test func matchesPythonForFloatsUnicodeAndEscapes() throws {
          // Keys deliberately out of order; numbers as FastAPI's JSON encoder writes them.
          let body = try parse(#"{"z":1.0,"a":[0.1,1e+16,9500000000000000.0,1e-05,0.0001,-0.0,123.45],"name":"Café ☕ \"q\"\n\t\u0001","nested":{"b":true,"a":null},"B":2,"_k":"x"}"#)
          let query = try parse(#"{"dry":"false"}"#)
          let bytes = try #require(ApprovalFingerprint.canonicalJSON(method: "POST", path: "/instances/alpaca-main/live-command", query: query, body: body))
          #expect(String(decoding: bytes, as: UTF8.self) == #"{"body":{"B":2,"_k":"x","a":[0.1,1e+16,9500000000000000.0,1e-05,0.0001,-0.0,123.45],"name":"Café ☕ \"q\"\n\t\u0001","nested":{"a":null,"b":true},"z":1.0},"method":"POST","path":"/instances/alpaca-main/live-command","query":{"dry":"false"}}"#)
          #expect(ApprovalFingerprint.sha256Hex(method: "POST", path: "/instances/alpaca-main/live-command", query: query, body: body)
                  == "75f4624cffd3532685712841e50e14fcd1326bd508569e0d137f76f7f19ea5f8")
      }

      @Test func matchesPythonForANullBodyAndAListQuery() throws {
          let query = try parse(#"{"status":["a","b"]}"#)
          #expect(ApprovalFingerprint.sha256Hex(method: "DELETE", path: "/strategies/doc 7", query: query, body: .null)
                  == "3cabaf62f5af592cd98c01381cf5562cd18fb067d5eee33830ceea18d8c1b016")
      }

      @Test(arguments: [
          (5.0, "5.0"), (0.1, "0.1"), (1e16, "1e+16"), (9.5e15, "9500000000000000.0"), (1e-05, "1e-05"),
          (0.0001, "0.0001"), (-0.0, "-0.0"), (0.0, "0.0"), (123.45, "123.45"), (1e22, "1e+22"),
          (1.5e-07, "1.5e-07"), (123456789.125, "123456789.125"), (-2.5, "-2.5"),
      ])
      func pythonFloatReprMatchesPythonRepr(value: Double, repr: String) {
          #expect(pythonFloatRepr(value) == repr)
      }

      @Test func sortsKeysByCodePointNotSwiftCollation() throws {
          // "e\u{0301}" (a decomposed é) sorts before "f" by code point, as Python does,
          // but after it under Swift's String <, which compares canonically.
          let body: JSON = .object(JSONObject([("f", .int(2)), ("e\u{0301}", .int(1)), ("Z", .int(3)), ("_", .int(4))]))
          let bytes = try #require(ApprovalFingerprint.canonicalJSON(method: "GET", path: "/p", query: .object(JSONObject()), body: body))
          #expect(bytes.starts(with: Data("{\"body\":{\"Z\":3,\"_\":4,\"e\u{0301}\":1,\"f\":2}".utf8)))   // bytes, not String ==
      }

      @Test func nonFiniteNumbersHaveNoFingerprint() {
          #expect(ApprovalFingerprint.sha256Hex(method: "POST", path: "/p", query: .null, body: ["x": .double(.nan)]) == nil)
      }

      @Test func messageBytesAreTheContractString() {
          #expect(ApprovalMessage.bytes(id: "ap_7f3c", bodySha256: kVectorAHash, decision: .approve, ts: 1_763_538_000)
                  == Data("ap_7f3c|\(kVectorAHash)|approve|1763538000".utf8))
          #expect(ApprovalMessage.bytes(id: "ap_7f3c", bodySha256: kVectorAHash, decision: .deny, ts: 7)
                  == Data("ap_7f3c|\(kVectorAHash)|deny|7".utf8))
      }

      @Test func approvalIdsAreValidated() {
          for ok in ["ap_7f3c", "3f2b1c4e-9d1a-4c8e-b2f0-7a6d5e4c3b2a", String(repeating: "a", count: 128)] { #expect(ApprovalIds.isValid(ok)) }
          for bad in ["", "../brokerages", "a/b", "a%2Fb", "ap 1", "ap.1", String(repeating: "a", count: 129)] { #expect(!ApprovalIds.isValid(bad)) }
      }
  }
  ```

  ```swift
  // ApprovalWireTests.swift — its fixture is reused by every later suite
  struct ApprovalWireTests {
      static let full: JSON = [
          "id": "ap_7f3c", "created_at": "2026-11-19T06:00:00Z", "expires_at": "2026-11-19T06:15:00Z",
          "status": "executed", "summary": "BUY 10 TQQQ on alpaca-main — LIVE", "money_state": "live",
          "client": "jarvis", "method": "POST", "path": "/brokerages/b1/orders", "query": [:],
          "body": ["symbol": "TQQQ", "qty": 10, "side": "buy", "type": "market", "limit_price": nil],
          "body_sha256": .string(kVectorAHash), "result_status": 200, "result": ["order_id": "o1"],
          "decided_at": "2026-11-19T06:02:00Z", "device_id": "dev_1",
      ]

      @Test func decisionBodyEncodesTheContractKeysInOrder() throws {
          let body = ApprovalDecisionBody(decision: .approve, bodySha256: kVectorAHash, ts: 1_763_538_000, deviceId: "dev_1", signature: Data([1, 2, 3]))
          #expect(try body.json.dartEncoded() == #"{"decision":"approve","body_sha256":"15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d","ts":1763538000,"device_id":"dev_1","signature":"AQID"}"#)
      }
  }
  ```

  The rest of `ApprovalWireTests` (`1_795_068_000` is 2026-11-19T06:00:00Z):

  | Test | Arrange | Expect |
  |---|---|---|
  | `decodesEveryContractField` | `McpApproval(json: full)` | every field as in `full`; `expiresAt == Date(timeIntervalSince1970: 1_795_068_900)`; `result["order_id"] == "o1"`; `isTerminal && !isPending`; `ApprovalFingerprint.matches(a)` |
  | `missingFieldsAreEmptyAndAMissingExpiryCountsAsExpired` | `["id": "ap_1", "status": "pending"]` | `expiresAt`, `resultStatus`, `deviceId` nil; `body == .null`; `isExpired(at: Date(timeIntervalSince1970: 0))`; `full` not expired at `…899`, expired at `…900` |
  | `truncatedResultsAreFlagged` | `["id": "a", "result": ["truncated": true]]` | `resultTruncated`; `full`'s result is not truncated |
  | `devicesReadDeviceIdOrIdAndActivation` | `["device_id": "dev_1", "name": "iPhone", "active_at": "2026-11-19T06:10:00Z"]`; `["id": "dev_2"]` | ids `dev_1` / `dev_2`; `dev_1` inactive at `…599`, active at `…600`; `dev_2` never active; the matching receipt's `activeAt == …600` |

- [ ] **Step 3: Run them and see them fail**

  Run: `xct ApprovalFingerprintTests ApprovalWireTests`
  Expected: build failure, "cannot find 'ApprovalFingerprint' in scope" (and the other new names).

- [ ] **Step 4: Implement `McpApproval.swift`**

  Strings read `j[key].string ?? ""`; dates go through `DartDateTime.tryParse`; `resultStatus` is `j["result_status"].int`; `deviceId` is nil when absent or empty. `query`, `body` and `result` keep the raw `JSON`, because the fingerprint must see exactly what the server sent. `ApprovalDecisionBody.json` builds a `JSONObject` in the listed key order, with `signature.base64EncodedString()`.

- [ ] **Step 5: Implement `ApprovalFingerprint.swift`**

  `canonicalJSON` writes `{"body":…,"method":…,"path":…,"query":…}` with its own encoder. Don't use `JSON.data()`: it writes Dart doubles and keeps key order.
  - Objects: keys sorted by Unicode scalar values (`a.unicodeScalars.lexicographicallyPrecedes(b.unicodeScalars) { $0.value < $1.value }`), never by Swift `String <`.
  - Strings: escape `"` and `\`; `\b \f \n \r \t` in short form; any other U+0000–U+001F as `\u00xx` in lowercase hex; every other scalar as raw UTF-8.
  - Values: `.int` in decimal; `.double` via `pythonFloatRepr` (nil makes the whole result nil); `true`/`false`/`null`.

  `pythonFloatRepr` reproduces CPython's `repr(float)`:
  1. Non-finite → nil. Zero → `"0.0"`, or `"-0.0"` when `d.sign == .minus`.
  2. Take the shortest round-trip digits from Swift's `abs(d).description`. Split off any `e±E`, drop the `.`, and strip leading and trailing zeros. That leaves digits `D` (length n) and `decpt = (integer-part length) + E − (leading zeros removed)`, so that value = 0.D × 10^decpt.
  3. If `decpt > 16 || decpt < -3`, use exponent form: `D[0]`, then `"." + D[1...]` when n > 1, then `"e"`, the sign (`+`/`-`), and `abs(decpt - 1)` zero-padded to at least 2 digits.
  4. Otherwise use fixed form: `decpt <= 0` → `"0."` + `-decpt` zeros + D; `decpt >= n` → D + `decpt - n` zeros + `".0"`; else D with a `"."` after its first `decpt` digits.
  5. Prefix `"-"` for negatives.

  `sha256Hex` is CryptoKit `SHA256.hash(data:)` as lowercase hex. `ApprovalMessage.bytes` is `Data("\(id)|\(bodySha256)|\(decision.rawValue)|\(ts)".utf8)`.

- [ ] **Step 6: Run them and see them pass**

  Run: `xct ApprovalFingerprintTests ApprovalWireTests`
  Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 7: Commit**

  Run `mcp__gitnexus__detect_changes()` (expect only the new files and the plan), then:
  ```bash
  git add docs/superpowers/plans/2026-11-19-mcp-ios-approvals.md ios/IntelliStock/Features/Approvals/Data ios/IntelliStockTests/Features/Approvals
  git commit -m "feat(ios): MCP approval wire models and Python-compatible fingerprint

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 2: Approvals repository

**Files:**
- Create: `ios/IntelliStock/Features/Approvals/Data/ApprovalsRepository.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalsRepositoryTests.swift`

**Interfaces:**
- Consumes: Task 1 types; `ApiClient` (`get`/`post`/`delete`; throws `ApiError` with `statusCode` on a non-2xx and `statusCode == nil` on a transport failure); `dartEncodeComponent` (`Core/Network/DartUri.swift`).
- Produces:
  ```swift
  nonisolated protocol ApprovalsSource: Sendable {
      func approvals(status: ApprovalListFilter, limit: Int) async throws -> [McpApproval]
      func approval(_ id: String) async throws -> McpApproval
      func decide(_ id: String, _ body: ApprovalDecisionBody) async throws -> McpApproval
  }
  nonisolated protocol ApprovalDevicesSource: Sendable {
      func registerDevice(name: String, publicKey: Data) async throws -> ApprovalDeviceReceipt
      func devices() async throws -> [McpApprovalDevice]
      func deleteDevice(_ deviceId: String) async throws
  }
  nonisolated struct ApprovalsRepository: ApprovalsSource, ApprovalDevicesSource {
      static let listLimit = 50
      let client: ApiClient
  }
  /// Thrown before any request when an id fails ApprovalIds.isValid.
  nonisolated let kInvalidApprovalId = ApiError(message: "Not a valid approval id.")
  /// A 2xx whose body has no "id" (statusCode nil, so callers read it as "unconfirmed").
  nonisolated let kUnexpectedApprovalResponse = ApiError(message: "The server sent an unexpected response.")
  ```

- [ ] **Step 1: Write the failing tests**

  ```swift
  struct ApprovalsRepositoryTests {
      private func encode(_ v: JSON) -> String { (try? v.dartEncoded()) ?? "" }

      @Test func decisionPostsTheExactContractBody() async throws {
          let stub = DataStub(json: encode(ApprovalWireTests.full))
          let body = ApprovalDecisionBody(decision: .deny, bodySha256: kVectorAHash, ts: 1_795_068_030, deviceId: "dev_1", signature: Data([0x30, 0x01]))
          let a = try await ApprovalsRepository(client: stub.client).decide("ap_7f3c", body)
          let r = try #require(stub.last)
          #expect(r.method == "POST" && r.path == "/mcp/approvals/ap_7f3c/decision" && r.queryPairs.isEmpty)
          #expect(r.value(forHTTPHeaderField: "Content-Type") == "application/json")
          #expect(r.bodyBytes == #"{"decision":"deny","body_sha256":"15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d","ts":1795068030,"device_id":"dev_1","signature":"MAE="}"#)
          #expect(a.status == "executed")
      }

      @Test func registerDevicePostsNameAndBase64PublicKey() async throws {
          let pub = Data(base64Encoded: "BPr89QgQ9Ywp13lwYgOI0R9yI5AQggFqAh/0hDhGoqOuyhBMVLcMfHdUbK9OXP+OZk5Z0gntyHv1t6eZnKsrxVU=")!
          let stub = DataStub(status: 201, json: #"{"device_id": "dev_1", "active_at": "2026-11-19T06:10:00Z"}"#)
          let receipt = try await ApprovalsRepository(client: stub.client).registerDevice(name: "Pranav's iPhone", publicKey: pub)
          #expect(stub.last?.method == "POST" && stub.last?.path == "/mcp/approval-devices")
          #expect(stub.last?.bodyBytes == #"{"name":"Pranav's iPhone","public_key":"BPr89QgQ9Ywp13lwYgOI0R9yI5AQggFqAh/0hDhGoqOuyhBMVLcMfHdUbK9OXP+OZk5Z0gntyHv1t6eZnKsrxVU="}"#)
          #expect(receipt == ApprovalDeviceReceipt(json: ["device_id": "dev_1", "active_at": "2026-11-19T06:10:00Z"]))
      }

      @Test func invalidIdsNeverReachTheNetwork() async {
          let stub = DataStub()
          let repo = ApprovalsRepository(client: stub.client)
          let body = ApprovalDecisionBody(decision: .approve, bodySha256: "x", ts: 1, deviceId: "d", signature: Data())
          await #expect(throws: kInvalidApprovalId) { _ = try await repo.approval("../brokerages") }
          await #expect(throws: kInvalidApprovalId) { _ = try await repo.decide("a/b", body) }
          await #expect(throws: kInvalidApprovalId) { try await repo.deleteDevice("") }
          #expect(stub.requests.isEmpty)
      }
  }
  ```

  | Test | Stub answers | Expect |
  |---|---|---|
  | `listAsksForAllWithLimit50` | `{"approvals": [full, {"id": ""}, "junk"]}` | GET `/mcp/approvals`; `queryItems == ["status": "all", "limit": "50"]`; ids `["ap_7f3c"]` |
  | `pendingFilterSendsStatusPending` | `{"approvals": []}` | `queryItems["status"] == "pending"` |
  | `detailGetsOneApproval` | `full` | GET `/mcp/approvals/ap_7f3c`; summary "BUY 10 TQQQ on alpaca-main — LIVE" |
  | `decisionErrorsCarryTheirStatusAndDetail` (arguments: 400 "ts outside window", 403 "bad signature", 409 "not pending", 410 "expired") | that status with `{"detail": …}` | throws `ApiError(message: detail, statusCode: status)` |
  | `aSuccessWithoutAnIdIsUnexpected` | 200 `{}` | `approval("ap_1")` throws `kUnexpectedApprovalResponse` |
  | `listsAndDeletesDevices` | `{"devices": [{"device_id": "dev_1", "name": "iPhone"}, "junk", {"name": "no id"}]}`, then 204 `""` | ids `["dev_1"]` from GET `/mcp/approval-devices`; then DELETE `/mcp/approval-devices/dev_1` succeeds |

  `bodyBytes` is the `URLRequest` extension in `IntelliStockTests/Data/SwingApprovalRepositoryTests.swift`. That file is not on the do-not-edit list; use the extension as is.

- [ ] **Step 2: Run them and see them fail**

  Run: `xct ApprovalsRepositoryTests`
  Expected: build failure, "cannot find 'ApprovalsRepository' in scope".

- [ ] **Step 3: Implement `ApprovalsRepository`**

  - `approvals`: `client.get("/mcp/approvals", query: ["status": .string(status.rawValue), "limit": JSON(limit)])`. Read `data["approvals"]`, or the body itself if it is a bare array. Keep the object rows that have a non-empty `id`.
  - `approval` and `decide`: validate the id first (`kInvalidApprovalId`). Then GET `/mcp/approvals/\(dartEncodeComponent(id))`, or POST `…/decision` with `body.json`. Throw `kUnexpectedApprovalResponse` when the decoded approval's `id` is empty.
  - `registerDevice`: POST `/mcp/approval-devices` with `JSONObject([("name", .string(name)), ("public_key", .string(publicKey.base64EncodedString()))])`.
  - `devices`: map `data["devices"].objectElements` to `McpApprovalDevice`, keeping those with a non-empty id.
  - `deleteDevice`: validate, then DELETE `/mcp/approval-devices/\(dartEncodeComponent(id))`.

- [ ] **Step 4: Run them and see them pass**

  Run: `xct ApprovalsRepositoryTests ApprovalWireTests`
  Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 5: Commit**

  Run `mcp__gitnexus__detect_changes()`, then:
  ```bash
  git add ios/IntelliStock/Features/Approvals/Data/ApprovalsRepository.swift ios/IntelliStockTests/Features/Approvals/ApprovalsRepositoryTests.swift
  git commit -m "feat(ios): approvals and approval-devices repository

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 3: Signers and the device record

**Files:**
- Create: `ios/IntelliStock/Features/Approvals/Signing/ApprovalSigner.swift`
- Create: `ios/IntelliStock/Features/Approvals/Signing/SecureEnclaveApprovalSigner.swift`
- Create: `ios/IntelliStock/Features/Approvals/Signing/SoftwareApprovalSigner.swift`
- Create: `ios/IntelliStock/Features/Approvals/Data/ApprovalDeviceStore.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalSignerTests.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalDeviceStoreTests.swift`

**Interfaces:**
- Consumes: `SecureStorage` / `InMemorySecureStorage` (`Core/Storage/SecureStorage.swift`); Task 1 `ApprovalMessage`.
- Produces:
  ```swift
  nonisolated enum ApprovalSigning {
      static let algorithm: SecKeyAlgorithm = .ecdsaSignatureMessageX962SHA256
      static let accessFlags: SecAccessControlCreateFlags = [.privateKeyUsage, .biometryCurrentSet]
      static let keyTag = "dev.pkrishna.intellistock.mcp-approval.v1"
      static let selfTestMessage = Data("intellistock-approval-selftest".utf8)
      static func isUncompressedP256(_ key: Data) -> Bool                          // 65 bytes, first 0x04
      static func verify(_ signature: Data, _ message: Data, publicKey: Data) -> Bool // SecKeyCreateWithData + SecKeyVerifySignature
  }
  nonisolated enum ApprovalSignerError: Error, Equatable, Sendable {
      case unavailable      // no Secure Enclave
      case noKey            // nothing created on this iPhone
      case cancelled        // Face ID dismissed
      case keyInvalidated   // Face ID enrolment changed; the key can never sign again
      case failed(String)
  }
  nonisolated protocol ApprovalSigningGrant: Sendable { func sign(_ message: Data) throws -> Data }  // DER
  nonisolated protocol ApprovalSigner: Sendable {
      var isHardwareBacked: Bool { get }
      func publicKey() -> Data?                       // 65-byte X9.62 point; nil when no key
      func createKey() throws -> Data                 // replaces any key; returns the public key
      func deleteKey()
      /// Face ID (Secure Enclave) showing the reason, then a grant that signs without asking again.
      func authorize(reason: String) async throws -> any ApprovalSigningGrant
  }
  nonisolated enum ApprovalSigners { static func platformDefault(storage: any SecureStorage) -> any ApprovalSigner }
  nonisolated final class SecureEnclaveApprovalSigner: ApprovalSigner {
      init(tag: String = ApprovalSigning.keyTag)
      static func keyAttributes(tag: String, access: SecAccessControl) -> [CFString: Any]
      static func makeAccessControl() throws -> SecAccessControl
  }
  nonisolated final class SoftwareApprovalSigner: ApprovalSigner {
      init(storage: any SecureStorage, storageKey: String = "mcp_approval_software_key")
  }
  nonisolated struct ApprovalDeviceRecord: Codable, Hashable, Sendable {
      let baseURL: String; let deviceId: String; let name: String; let activeAt: Date
  }
  nonisolated struct ApprovalDeviceStore: Sendable {
      static let storageKey = "mcp_approval_device"
      let storage: any SecureStorage
      func record(for baseURL: String) -> ApprovalDeviceRecord?   // nil unless stored for exactly this server
      func save(_ record: ApprovalDeviceRecord) throws
      func clear()
  }
  ```

- [ ] **Step 1: Write the failing tests**

  The server vector below was produced by Python's `cryptography` (`ec.ECDSA(hashes.SHA256())`, DER signature, X9.62 uncompressed point), the library build (C) verifies with. It proves that the algorithm constant and both encodings match the server's.

  ```swift
  import CryptoKit

  struct ApprovalSignerTests {
      static let serverPub = Data(base64Encoded: "BPr89QgQ9Ywp13lwYgOI0R9yI5AQggFqAh/0hDhGoqOuyhBMVLcMfHdUbK9OXP+OZk5Z0gntyHv1t6eZnKsrxVU=")!
      static let serverSig = Data(base64Encoded: "MEUCIEL1xTOHf4SMVIEI3flff9m8xDXxawhFsT5f/67F1m/QAiEArrJQXBsq3U7iVHhFQ7oELk0uKlGaPfSjwtwFjhwYDRk=")!
      static let serverMsg = Data("ap_7f3c|15d67143989b33dd3b4423b0f683824b4394e9af80191b336c6d79c15cec3d5d|approve|1763538000".utf8)

      @Test func aPythonSignatureVerifiesWithTheSigningAlgorithm() {
          #expect(ApprovalSigning.verify(Self.serverSig, Self.serverMsg, publicKey: Self.serverPub))
          let tampered = Data(String(decoding: Self.serverMsg, as: UTF8.self).replacingOccurrences(of: "approve", with: "deny").utf8)
          #expect(!ApprovalSigning.verify(Self.serverSig, tampered, publicKey: Self.serverPub))
      }

      @Test func softwareSignaturesVerifyWithCryptoKitAgainstTheExportedKey() async throws {
          let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
          let pub = try signer.createKey()
          #expect(pub.count == 65 && pub.first == 0x04 && ApprovalSigning.isUncompressedP256(pub))
          let msg = ApprovalMessage.bytes(id: "ap_7f3c", bodySha256: kVectorAHash, decision: .approve, ts: 1_795_068_030)
          let der = try await signer.authorize(reason: "test").sign(msg)
          #expect(der.first == 0x30)                                           // DER SEQUENCE
          let key = try P256.Signing.PublicKey(x963Representation: pub)
          let sig = try P256.Signing.ECDSASignature(derRepresentation: der)
          #expect(key.isValidSignature(sig, for: msg))
          #expect(!key.isValidSignature(sig, for: Data("other".utf8)))
          #expect(ApprovalSigning.verify(der, msg, publicKey: pub))
      }

      @Test func secureEnclaveAttributesPinTheContract() throws {
          #expect(ApprovalSigning.accessFlags == [.privateKeyUsage, .biometryCurrentSet])
          #expect(ApprovalSigning.algorithm == .ecdsaSignatureMessageX962SHA256)
          let attrs = SecureEnclaveApprovalSigner.keyAttributes(tag: "t", access: try SecureEnclaveApprovalSigner.makeAccessControl())
          #expect(attrs[kSecAttrTokenID] as? String == kSecAttrTokenIDSecureEnclave as String)
          #expect(attrs[kSecAttrKeyType] as? String == kSecAttrKeyTypeECSECPrimeRandom as String)
          #expect(attrs[kSecAttrKeySizeInBits] as? Int == 256)
          let priv = try #require(attrs[kSecPrivateKeyAttrs] as? [CFString: Any])
          #expect(priv[kSecAttrIsPermanent] as? Bool == true)
          #expect(priv[kSecAttrApplicationTag] as? Data == Data("t".utf8))
          #expect(priv[kSecAttrAccessControl] != nil)
      }

      #if targetEnvironment(simulator)
      @Test func theSecureEnclaveSignerNeverFallsBackToSoftware() {
          #expect(throws: ApprovalSignerError.unavailable) { _ = try SecureEnclaveApprovalSigner(tag: "test.\(UUID().uuidString)").createKey() }
      }

      @Test func theSimulatorDefaultIsTheSoftwareSigner() {
          #expect(!ApprovalSigners.platformDefault(storage: InMemorySecureStorage()).isHardwareBacked)
      }
      #endif
  }
  ```

  | Test | Arrange | Expect |
  |---|---|---|
  | `ApprovalSignerTests.theSoftwareKeySurvivesANewSignerAndCanBeDeleted` | `createKey()` on one `SoftwareApprovalSigner` over an `InMemorySecureStorage` | a second signer over the same storage has the same `publicKey()`; after `deleteKey()` it is nil, and `authorize` throws `.noKey` |
  | `ApprovalDeviceStoreTests.readsTheRecordOnlyForItsServer` | save `ApprovalDeviceRecord(baseURL: "https://api.test", deviceId: "dev_1", name: "iPhone", activeAt: Date(timeIntervalSince1970: 1_795_068_600))` | the equal record for `https://api.test`; nil for `https://other.test` |
  | `ApprovalDeviceStoreTests.clearRemovesItAndJunkReadsAsNone` | save, then `clear()`; then write `"not json"` under `storageKey` | nil both times |

- [ ] **Step 2: Run them and see them fail**

  Run: `xct ApprovalSignerTests ApprovalDeviceStoreTests`
  Expected: build failure, "cannot find 'SoftwareApprovalSigner' in scope".

- [ ] **Step 3: Implement `ApprovalSigner.swift`**

  `verify` builds the key with `SecKeyCreateWithData(publicKey, [kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom, kSecAttrKeyClass: kSecAttrKeyClassPublic] as CFDictionary, nil)` and checks the signature with `SecKeyVerifySignature(key, algorithm, …)`. A bad key reads as false. `platformDefault` is the only selector:
  ```swift
  #if targetEnvironment(simulator)
  return SoftwareApprovalSigner(storage: storage)
  #else
  return SecureEnclaveApprovalSigner()
  #endif
  ```

- [ ] **Step 4: Implement `SecureEnclaveApprovalSigner.swift`**

  - `makeAccessControl`: `SecAccessControlCreateWithFlags(kCFAllocatorDefault, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, ApprovalSigning.accessFlags, &error)`; failure → `.failed`.
  - `keyAttributes`: `kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom`, `kSecAttrKeySizeInBits: 256`, `kSecAttrTokenID: kSecAttrTokenIDSecureEnclave`, `kSecPrivateKeyAttrs: [kSecAttrIsPermanent: true, kSecAttrApplicationTag: Data(tag.utf8), kSecAttrAccessControl: access]`.
  - `createKey`: `guard SecureEnclave.isAvailable` (CryptoKit), else throw `.unavailable`. Then `deleteKey()` and `SecKeyCreateRandomKey`. Export the public half with `SecKeyCopyExternalRepresentation(SecKeyCopyPublicKey(key)!)` and require `isUncompressedP256`. `isHardwareBacked` is true.
  - Key lookup query: `kSecClass: kSecClassKey`, `kSecAttrApplicationTag`, `kSecAttrKeyClass: kSecAttrKeyClassPrivate`, `kSecReturnRef: true`, `kSecReturnAttributes: true`. `publicKey()` passes an `LAContext` with `interactionNotAllowed = true`, so reading the public half never prompts. `deleteKey` is `SecItemDelete` on the same class and tag.
  - `authorize(reason:)`:
    1. Look the key up (not found → `.noKey`) and read its own `kSecAttrAccessControl` from the returned attributes.
    2. On a fresh `LAContext`, `try await context.evaluateAccessControl(access, operation: .useKeySign, localizedReason: reason)`. Map `LAError.userCancel`, `.appCancel`, `.systemCancel` and `.userFallback` to `.cancelled`, anything else to `.failed(error.localizedDescription)`.
    3. Fetch the key again with `kSecUseAuthenticationContext: context`, and return a grant (an `@unchecked Sendable` final class) holding that `SecKey`.
  - The grant's `sign`: `SecKeyCreateSignature(key, ApprovalSigning.algorithm, message as CFData, &error)`. `errSecItemNotFound`, `errSecAuthFailed` and `errSecInvalidKeyRef` → `.keyInvalidated`; other failures → `.failed("OSStatus \(code)")`.

- [ ] **Step 5: Implement `SoftwareApprovalSigner.swift` and `ApprovalDeviceStore.swift`**

  - Software signer: `createKey` makes a non-permanent `SecKeyCreateRandomKey([kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom, kSecAttrKeySizeInBits: 256])` and writes the base64 of its private external representation (97 bytes) under `storageKey`. `publicKey()` and `authorize` rebuild the key with `SecKeyCreateWithData(…, kSecAttrKeyClassPrivate)`. `authorize` never prompts and throws `.noKey` when nothing is stored. The grant signs with the same `ApprovalSigning.algorithm`. `isHardwareBacked` is false.
  - Store: `JSONEncoder`/`JSONDecoder` (dates as `.secondsSince1970`) under `storageKey`. `record(for:)` returns nil when decoding fails or `baseURL` differs.

- [ ] **Step 6: Run them and see them pass**

  Run: `xct ApprovalSignerTests ApprovalDeviceStoreTests`
  Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 7: Commit**

  Run `mcp__gitnexus__detect_changes()`, then:
  ```bash
  git add ios/IntelliStock/Features/Approvals/Signing ios/IntelliStock/Features/Approvals/Data/ApprovalDeviceStore.swift ios/IntelliStockTests/Features/Approvals
  git commit -m "feat(ios): Secure Enclave approval signer with a simulator software key

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 4: The decision state machine

**Files:**
- Create: `ios/IntelliStock/Features/Approvals/Model/ApprovalGate.swift`
- Create: `ios/IntelliStock/Features/Approvals/Model/ApprovalDetailModel.swift`
- Create: `ios/IntelliStockTests/Features/Approvals/ApprovalsTestSupport.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalGateTests.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalDetailModelTests.swift`

**Interfaces:**
- Consumes: Task 1 (`McpApproval`, `ApprovalDecision`, `ApprovalDecisionBody`, `ApprovalFingerprint.matches`, `ApprovalMessage.bytes`, `ApprovalIds.isValid`); Task 2 (`ApprovalsSource`, `ApprovalDevicesSource`, `kInvalidApprovalId`); Task 3 (`ApprovalSigner`, `ApprovalSigningGrant`, `ApprovalSignerError`, `ApprovalDeviceStore`, `ApprovalDeviceRecord`); `Loadable`, `PollingLoop`, `PollingSleep`, `realPollingSleep`, `AppLifecycle`, `ApiError`.
- Produces:
  ```swift
  nonisolated enum ApprovalGate: Hashable, Sendable {
      case open
      case denyOnly(String)     // Approve blocked for the reason; Deny allowed
      case closed(String)
      var canApprove: Bool { get }; var canDeny: Bool { get }; var reason: String? { get }
  }
  nonisolated func approvalGate(_ a: McpApproval, now: Date, device: ApprovalDeviceRecord?, fingerprintMatches: Bool) -> ApprovalGate
  nonisolated func approvalStatusWord(_ status: String) -> String   // for the not-pending copy

  nonisolated enum ApprovalDecisionPhase: Hashable, Sendable { case idle, authorizing(ApprovalDecision), sending(ApprovalDecision), checking }
  nonisolated enum ApprovalDecisionOutcome: Hashable, Sendable {
      case decided      // 200: state is the returned approval
      case cancelled    // Face ID dismissed; nothing sent; message ""
      case blocked      // refused on the phone (gate, signer error, expired during Face ID); nothing sent
      case rejected     // 400/403: still pending; message = server detail (+ clock hint on 400)
      case notPending   // 409/410: re-read; state shows the real status
      case uncertain    // transport error / 5xx / unexpected body: re-read; message kApprovalUncertain
      case ignored      // a second tap while one runs
  }
  nonisolated struct ApprovalDecisionResult: Hashable, Sendable { let outcome: ApprovalDecisionOutcome; let message: String }

  @Observable final class ApprovalDetailModel {
      static let pendingPoll: Duration = .seconds(10)
      static let runningPoll: Duration = .seconds(3)   // status "approved", or phase .checking
      static let settledPoll: Duration = .seconds(60)
      let approvalId: String
      private(set) var state: Loadable<McpApproval>    // starts .loading
      private(set) var phase: ApprovalDecisionPhase    // starts .idle
      private(set) var fingerprintMatches: Bool
      private(set) var refreshError: String?
      init(approvalId: String, source: @escaping () -> any ApprovalsSource, signer: any ApprovalSigner,
           devices: ApprovalDeviceStore, baseURL: @escaping () -> String, clock: @escaping () -> Date = Date.init)
      var device: ApprovalDeviceRecord? { get }       // devices.record(for: baseURL())
      var isBusy: Bool { get }                        // phase != .idle
      var pollInterval: Duration { get }
      func gate(now: Date) -> ApprovalGate            // closed("") when no approval is loaded
      func load() async
      func refresh() async                            // a failure keeps the last approval and sets refreshError
      func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async
      func decide(_ decision: ApprovalDecision) async -> ApprovalDecisionResult
  }
  ```
  Exact copy, defined in `ApprovalGate.swift` as `nonisolated let`:

  | Constant | Text |
  |---|---|
  | `kApprovalRegisterFirst` | Register this iPhone in Settings › Jarvis approvals to decide requests. |
  | `kApprovalExpired` | This request expired. |
  | `kApprovalFingerprintMismatch` | The parameters shown don't match this request's fingerprint, so it can't be approved here. Deny it, or check the server. |
  | `kApprovalNoKey` | This iPhone has no approval key. Register it again in Settings › Jarvis approvals. |
  | `kApprovalKeyInvalidated` | Face ID changed since this iPhone was registered, so its approval key no longer works. Register it again in Settings › Jarvis approvals. |
  | `kApprovalUncertain` | Couldn't confirm the server got the decision. Showing where the request stands now. |
  | `kApprovalClockHint` | (with a leading space) If this keeps happening, check that Date & Time is set automatically. |

  Generated text:
  - Not pending → "This request is \(approvalStatusWord(status)) — there's nothing to decide." `approvalStatusWord` maps approved → "approved", executed → "done", failed → "failed", denied → "denied", expired → "expired", anything else → the raw status.
  - Device not active yet → "This iPhone becomes an approver in \(m) min.", where m = `max(1, Int(ceil(secondsLeft / 60)))`.
  - Decided: approve + executed → "Approved. The request ran."; approve + failed → "Approved, but the request failed (HTTP \(code))." (`code` is `resultStatus`, or "?" when nil); approve + any other status → "Approved. The request is running."; deny → "Denied. The request will not run."
  - 403 with no detail → "This iPhone isn't an active approver."; 400 with no detail → "The server refused the decision."
  - Signer `.unavailable` / `.failed(s)` → "Couldn't sign the decision: \(s)" (for `.unavailable`, s is "no Secure Enclave").

- [ ] **Step 1: Write the test support file**

  `ApprovalsTestSupport.swift` holds the following, each a `nonisolated final class … @unchecked Sendable` with an `NSLock` around its state:
  - `ApprovalTestClock(_ start: Date)` — `var now: Date`, `func advance(by seconds: TimeInterval)`.
  - `ApprovalTestGate` — `func wait() async` suspends until `open()`.
  - `FakeApprovalsSource: ApprovalsSource, ApprovalDevicesSource`, `init(details: [McpApproval] = [], rows: [McpApproval] = [])`.
    - Settable: `rows`; `details` (each `approval(_:)` call takes the next, and the last one repeats); `listError`, `detailError`, `decideResult`, `decideError`; `decideGate: ApprovalTestGate?`; `receipt` (defaults to `ApprovalDeviceReceipt(json: ["device_id": "dev_1"])`); `registerError`, `devicesList`, `devicesError`, `deleteError`.
    - Records: `listCalls: [(ApprovalListFilter, Int)]`, `approvalCalls: [String]`, `decideCalls: [(id: String, body: ApprovalDecisionBody)]`, `registerCalls: [(name: String, publicKey: Data)]`, `deleteCalls: [String]`.
  - `FakeApprovalSigner: ApprovalSigner`:
    - Settable: `authorizeError: ApprovalSignerError?`, `signError: ApprovalSignerError?`, and `onAuthorize: (@Sendable () -> Void)?`, which runs inside `authorize` and lets a test move the clock as if Face ID took time.
    - Fixed: `let signature = Data([0x30, 0x03, 0x02, 0x01, 0x00])`. `isHardwareBacked` is true. `createKey` returns 65 bytes: `0x04` followed by 64 × `0x01`.
    - Records: `reasons: [String]` and `messages: [Data]`.
  - Fixtures:
    - `nonisolated let kT0 = Date(timeIntervalSince1970: 1_795_068_000)` (2026-11-19T06:00:00Z).
    - `func approvalA(status: String = "pending", expiresAt: Date = kT0 + 900, bodySha256: String = kVectorAHash, resultStatus: Int? = nil) -> McpApproval`. It starts from `ApprovalWireTests.full`, removes `result`, `decided_at` and `device_id`, and overrides the named keys; `result_status` is removed when nil. The id stays `ap_7f3c` and the summary "BUY 10 TQQQ on alpaca-main — LIVE".
    - `nonisolated let kActiveDevice = ApprovalDeviceRecord(baseURL: "https://api.test", deviceId: "dev_1", name: "iPhone", activeAt: kT0 - 3600)`.

- [ ] **Step 2: Write the failing tests**

  ```swift
  struct ApprovalGateTests {
      @Test func gateRulesInPrecedenceOrder() {
          let a = approvalA()
          #expect(approvalGate(a, now: kT0, device: kActiveDevice, fingerprintMatches: true) == .open)
          #expect(approvalGate(approvalA(status: "executed"), now: kT0, device: kActiveDevice, fingerprintMatches: true)
                  == .closed("This request is done — there's nothing to decide."))
          #expect(approvalGate(a, now: kT0 + 900, device: kActiveDevice, fingerprintMatches: true) == .closed(kApprovalExpired))
          #expect(approvalGate(a, now: kT0, device: nil, fingerprintMatches: true) == .closed(kApprovalRegisterFirst))
          let soon = ApprovalDeviceRecord(baseURL: "https://api.test", deviceId: "dev_1", name: "iPhone", activeAt: kT0 + 570)
          #expect(approvalGate(a, now: kT0, device: soon, fingerprintMatches: true) == .closed("This iPhone becomes an approver in 10 min."))
          #expect(approvalGate(a, now: kT0 + 550, device: soon, fingerprintMatches: true) == .closed("This iPhone becomes an approver in 1 min."))
          #expect(approvalGate(a, now: kT0, device: kActiveDevice, fingerprintMatches: false) == .denyOnly(kApprovalFingerprintMismatch))
          #expect(ApprovalGate.denyOnly("x").canDeny && !ApprovalGate.denyOnly("x").canApprove)
      }
  }

  @Suite(.serialized)
  struct ApprovalDetailModelTests {
      let clock = ApprovalTestClock(kT0)

      private func model(_ source: FakeApprovalsSource, signer: FakeApprovalSigner = FakeApprovalSigner(),
                         device: ApprovalDeviceRecord? = kActiveDevice, id: String = "ap_7f3c") async -> ApprovalDetailModel {
          let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
          if let device { try? store.save(device) }
          let clock = clock
          let m = ApprovalDetailModel(approvalId: id, source: { source }, signer: signer, devices: store,
                                      baseURL: { "https://api.test" }, clock: { clock.now })
          await m.load()
          return m
      }

      @Test func approveSignsTheContractMessageAfterFaceIDAndPostsIt() async throws {
          let source = FakeApprovalsSource(details: [approvalA()])
          source.decideResult = approvalA(status: "executed", resultStatus: 200)
          let signer = FakeApprovalSigner()
          let clock = clock
          signer.onAuthorize = { clock.advance(by: 5) }                 // Face ID took 5 s
          let m = await model(source, signer: signer)
          let result = await m.decide(.approve)
          #expect(result == ApprovalDecisionResult(outcome: .decided, message: "Approved. The request ran."))
          #expect(signer.reasons == ["Approve: BUY 10 TQQQ on alpaca-main — LIVE"])
          let ts = Int((kT0 + 5).timeIntervalSince1970)                  // taken after Face ID
          #expect(signer.messages == [Data("ap_7f3c|\(kVectorAHash)|approve|\(ts)".utf8)])
          let call = try #require(source.decideCalls.first)
          #expect(call.id == "ap_7f3c")
          #expect(call.body == ApprovalDecisionBody(decision: .approve, bodySha256: kVectorAHash, ts: ts, deviceId: "dev_1", signature: signer.signature))
          #expect(m.state.value?.status == "executed" && m.phase == .idle)
      }

      @Test func aTimeoutReReadsInsteadOfReportingFailure() async {
          let source = FakeApprovalsSource(details: [approvalA(), approvalA(status: "approved")])
          source.decideError = ApiError(message: "Request timed out. Check your connection and try again.", statusCode: nil)
          let m = await model(source)
          #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .uncertain, message: kApprovalUncertain))
          #expect(m.state.value?.status == "approved")
          #expect(m.pollInterval == ApprovalDetailModel.runningPoll)
          #expect(source.decideCalls.count == 1)                          // no automatic retry
      }

      @Test func fingerprintMismatchBlocksApproveButAllowsDeny() async throws {
          let wrong = String(repeating: "0", count: 64)
          let source = FakeApprovalsSource(details: [approvalA(bodySha256: wrong)])
          source.decideResult = approvalA(status: "denied", bodySha256: wrong)
          let signer = FakeApprovalSigner()
          let m = await model(source, signer: signer)
          #expect(await m.decide(.approve) == ApprovalDecisionResult(outcome: .blocked, message: kApprovalFingerprintMismatch))
          #expect(signer.reasons.isEmpty)
          #expect(await m.decide(.deny).outcome == .decided)
          #expect(try #require(source.decideCalls.first).body.bodySha256 == wrong)   // the server's hash is what gets signed
      }
  }
  ```

  The rest of `ApprovalDetailModelTests`, all through the same `model(…)` helper (by default `details: [approvalA()]` and an active device):

  | Test | Arrange | Expect |
  |---|---|---|
  | `loadComputesTheFingerprint` | `approvalA()`; then `approvalA(bodySha256: 64 × "0")` | `fingerprintMatches` true, then false |
  | `denyIsSignedToo` | `decideResult = approvalA(status: "denied")` | `.decided` with "Denied. The request will not run."; reason "Deny: BUY 10 TQQQ on alpaca-main — LIVE"; the signed message ends `\|deny\|1795068000` |
  | `aDoubleTapSendsOneDecision` | `decideGate = ApprovalTestGate()`; first `decide(.approve)` in a `Task`; `eventually { m.phase == .sending(.approve) }` | a second `.approve` and a `.deny` → `.ignored`; after `open()` the first is `.decided`; `decideCalls.count == 1` |
  | `cancellingFaceIDSendsNothing` | `signer.authorizeError = .cancelled` | `(.cancelled, "")`; no decide call; `phase == .idle` |
  | `aChangedFaceIDOrAMissingKeySaysRegisterAgain` | `signError = .keyInvalidated`; then `authorizeError = .noKey` with `.deny` | `(.blocked, kApprovalKeyInvalidated)`, then `(.blocked, kApprovalNoKey)`; no decide call |
  | `alreadyDecided409ReReadsAndShowsTheRealStatus` | `details: [approvalA(), approvalA(status: "executed", resultStatus: 200)]`; `decideError = ApiError(message: "not pending", statusCode: 409)` | `.notPending`; state status `executed` |
  | `expired410ReReads` | second detail `approvalA(status: "expired")`; `decideError` 410 | `.notPending`; state status `expired` |
  | `badSignatureOrInactiveDevice403StaysPendingWithTheServerReason` | `decideError = ApiError(message: "device dev_1 is not active until 2026-11-19T06:10:00Z", statusCode: 403)` | `(.rejected, that detail)`; status still `pending`; `phase == .idle` |
  | `clockSkew400AddsTheDateAndTimeHint` | `decideError = ApiError(message: "ts outside window", statusCode: 400)` | `(.rejected, "ts outside window" + kApprovalClockHint)` |
  | `a5xxIsTreatedAsUncertain` | `details: [approvalA(), approvalA()]`; `decideError` 502 | `.uncertain`; `gate(now: kT0).canApprove`, so the operator may decide again with a fresh signature |
  | `expiryDuringFaceIDSendsNothing` | `onAuthorize = { clock.advance(by: 901) }` | `(.blocked, kApprovalExpired)`; no decide call; `signer.messages` empty |
  | `noRegisteredDeviceBlocksBoth` | `device: nil` | `(.blocked, kApprovalRegisterFirst)` for approve; `.blocked` for deny |
  | `invalidIdNeverHitsTheNetwork` | `id: "../brokerages"` | `state.errorMessage == "Not a valid approval id."`; `approvalCalls` empty |
  | `pollIntervalFollowsStatus` | `approvalA()`, `approvalA(status: "approved")`, `approvalA(status: "executed")` | `.seconds(10)`, `.seconds(3)`, `.seconds(60)` |
  | `pollRefreshesOnItsInterval` | `details: [approvalA(), approvalA(status: "denied")]`; `Task { await m.poll(lifecycle: nil, sleep: manual.sleep) }` with a `ManualClock`; `advance(by: .seconds(10))` | `eventually { m.state.value?.status == "denied" }` |

  `eventually` and `ManualClock` come from `IntelliStockTests/Core/CoreTestSupport.swift`.

- [ ] **Step 3: Run them and see them fail**

  Run: `xct ApprovalGateTests ApprovalDetailModelTests`
  Expected: build failure, "cannot find 'ApprovalDetailModel' in scope".

- [ ] **Step 4: Implement `ApprovalGate.swift`**

  `approvalGate` checks, in this order:
  1. Not pending → closed (not-pending copy).
  2. `a.isExpired(at: now)` → closed(`kApprovalExpired`).
  3. No device → closed(`kApprovalRegisterFirst`).
  4. `now < device.activeAt` → closed (activation copy).
  5. `!fingerprintMatches` → denyOnly(`kApprovalFingerprintMismatch`).
  6. Otherwise open.

- [ ] **Step 5: Implement `ApprovalDetailModel`**

  - `load()`: an invalid id sets `state = .failed(kInvalidApprovalId)` without a request. Otherwise `state = await Loadable.capture { try await source().approval(approvalId) }`, then `fingerprintMatches = ApprovalFingerprint.matches(approval)`. `refresh()` does the same, but keeps the last good approval on failure and sets `refreshError`; cancellation changes nothing.
  - `pollInterval`: `runningPoll` when `phase == .checking` or the status is `"approved"`; `pendingPoll` while pending; `settledPoll` otherwise. `poll` loads first if nothing is loaded, then runs `PollingLoop(interval: { [weak self] in self?.pollInterval ?? .seconds(60) }, sleep: sleep) { await self.refresh() }.run(lifecycle:)`.
  - `decide(_:)`, in this order:
    1. `phase != .idle` → `.ignored`. No loaded approval → `.blocked` with "".
    2. Check `gate(now: clock())`: approve needs `canApprove`, deny needs `canDeny`; otherwise `.blocked(reason)`.
    3. `phase = .authorizing(d)`, then `let grant = try await signer.authorize(reason: (d == .approve ? "Approve: " : "Deny: ") + summary)`. A summary longer than 150 characters is cut to 149 plus "…". Signer errors: `.cancelled` → `.cancelled` with ""; `.noKey` → `kApprovalNoKey`; `.keyInvalidated` → `kApprovalKeyInvalidated`; `.unavailable` / `.failed` → "Couldn't sign the decision: …". Every one of these except cancelled is `.blocked`.
    4. Re-check `isExpired(at: clock())` → `.blocked(kApprovalExpired)`. Then take `ts = Int(clock().timeIntervalSince1970)` and `der = try grant.sign(ApprovalMessage.bytes(id:, bodySha256: approval.bodySha256, decision:, ts:))`, mapping signer errors as in step 3.
    5. `phase = .sending(d)`, then `source().decide(id, ApprovalDecisionBody(decision:, bodySha256: approval.bodySha256, ts:, deviceId: device.deviceId, signature: der))`.
    6. Handle the answer:
       - 200 → `state = .loaded(returned)`; `.decided` with the decided copy.
       - `ApiError` 409/410 → `refresh()`; `.notPending`, with the detail as message (else the not-pending copy for the re-read status).
       - 400/403 → `.rejected`, with the copy above.
       - `statusCode == nil`, ≥ 500, any other error, or a `CancellationError` while sending → `phase = .checking`, `refresh()`, `.uncertain(kApprovalUncertain)`.
    7. `phase = .idle` on every exit (`defer`).

- [ ] **Step 6: Run them and see them pass**

  Run: `xct ApprovalGateTests ApprovalDetailModelTests`
  Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 7: Commit**

  Run `mcp__gitnexus__detect_changes()`, then:
  ```bash
  git add ios/IntelliStock/Features/Approvals/Model ios/IntelliStockTests/Features/Approvals
  git commit -m "feat(ios): sign-and-send state machine for MCP approvals

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 5: Register this iPhone (Settings › Jarvis approvals)

**Files:**
- Create: `ios/IntelliStock/Features/Approvals/Model/ApprovalDevicesModel.swift`
- Create: `ios/IntelliStock/Features/Approvals/Model/ApprovalPresentation.swift` (the countdown helper; Task 6 adds the rest)
- Create: `ios/IntelliStock/Features/Approvals/Views/ApprovalDeviceSettingsView.swift`
- Modify: `ios/IntelliStock/App/AppServices+Repositories.swift` (one property)
- Modify: `ios/IntelliStock/App/AppServices.swift` (two lazy properties beside `widgetDataSyncer`)
- Modify: `ios/IntelliStock/Core/Navigation/Route.swift`, `Route+Path.swift`, `RouteDestination.swift` (case `approvalDevices`)
- Modify: `ios/IntelliStock/Features/Settings/Views/SettingsView.swift` (one row at the end of `DSSection("Security")`)
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalDevicesModelTests.swift`

**Interfaces:**
- Consumes: Task 1 (`McpApprovalDevice`, `ApprovalDeviceReceipt`); Task 2 (`ApprovalDevicesSource`, `ApprovalsRepository`); Task 3 (`ApprovalSigner`, `ApprovalSigning`, `ApprovalSigners.platformDefault`, `ApprovalDeviceStore`, `ApprovalDeviceRecord`, `SoftwareApprovalSigner`); Task 4 test support.
- Produces:
  ```swift
  nonisolated enum ApprovalDeviceStatus: Hashable, Sendable {
      case notRegistered
      case activating(ApprovalDeviceRecord)        // now < activeAt
      case active(ApprovalDeviceRecord)
      case missingOnServer(ApprovalDeviceRecord)   // the loaded server list lacks this record's device id
  }
  nonisolated func approvalDeviceNameError(_ name: String) -> String?
  nonisolated func approvalsErrorText(_ error: any Error) -> String     // ApiError 404 → kApprovalsNotOnServer; else ApiError.message, else localizedDescription
  nonisolated let kApprovalsNotOnServer = "This server doesn't have Jarvis approvals yet. Update the backend, then try again."
  nonisolated let kApprovalActivationDelay: TimeInterval = 600
  nonisolated func approvalCountdown(_ remaining: TimeInterval) -> String  // "m:ss", rounded UP to whole seconds; "0:00" at or below 0

  @Observable final class ApprovalDevicesModel {
      private(set) var devices: Loadable<[McpApprovalDevice]>
      private(set) var registering: Bool
      private(set) var removing: Set<String>
      init(source: @escaping () -> any ApprovalDevicesSource, signer: any ApprovalSigner, store: ApprovalDeviceStore,
           baseURL: @escaping () -> String, clock: @escaping () -> Date = Date.init)
      var isHardwareBacked: Bool { get }
      func status(now: Date) -> ApprovalDeviceStatus
      func load() async
      func register(name: String) async -> String?    // nil = registered; else the message to show
      func remove(_ deviceId: String) async -> String?
  }
  extension AppServices {
      var approvalsRepository: ApprovalsRepository     // AppServices+Repositories.swift
      var approvalSigner: any ApprovalSigner           // AppServices.swift, lazy, platformDefault(storage:)
      var approvalDeviceStore: ApprovalDeviceStore     // AppServices.swift, lazy, over storage
  }
  // Route.approvalDevices ⇄ "/settings/approval-devices" → ApprovalDeviceSettingsView()
  ```

- [ ] **Step 1: Impact analysis for the shared files**

  Run `mcp__gitnexus__impact` (upstream) on `Route`, `RouteDestination`, `SettingsView` and `AppServices`, and report the blast radius. If GitNexus can't resolve the Swift symbols, expect these callers:
  - the only exhaustive switches over `Route` are in `Route+Path.swift` and `RouteDestination.swift` (`/usr/bin/grep -rn "case .tokenUsage" ios` confirms it);
  - `SettingsView` is built only by `RouteDestination`;
  - `AppServices` only gains members, and no existing body changes.

  Adding enum cases is additive: expect LOW risk unless GitNexus says otherwise.

- [ ] **Step 2: Write the failing tests**

  ```swift
  @Suite(.serialized)
  struct ApprovalDevicesModelTests {
      let clock = ApprovalTestClock(kT0)
      let base = "https://api.test"

      private func make(_ source: FakeApprovalsSource, signer: any ApprovalSigner = SoftwareApprovalSigner(storage: InMemorySecureStorage()),
                        store: ApprovalDeviceStore = ApprovalDeviceStore(storage: InMemorySecureStorage())) -> ApprovalDevicesModel {
          let clock = clock
          return ApprovalDevicesModel(source: { source }, signer: signer, store: store, baseURL: { "https://api.test" }, clock: { clock.now })
      }

      @Test func registerCreatesAKeySelfTestsAndPostsThePublicKey() async throws {
          let source = FakeApprovalsSource()
          source.receipt = ApprovalDeviceReceipt(json: ["device_id": "dev_1", "active_at": "2026-11-19T06:10:00Z"])
          source.devicesList = [McpApprovalDevice(json: ["device_id": "dev_1", "name": "iPhone", "active_at": "2026-11-19T06:10:00Z"])]
          let signer = SoftwareApprovalSigner(storage: InMemorySecureStorage())
          let store = ApprovalDeviceStore(storage: InMemorySecureStorage())
          let m = make(source, signer: signer, store: store)
          #expect(await m.register(name: "  iPhone ") == nil)
          let call = try #require(source.registerCalls.first)
          #expect(call.name == "iPhone" && call.publicKey == signer.publicKey() && call.publicKey.count == 65)
          let record = try #require(store.record(for: base))
          #expect(record == ApprovalDeviceRecord(baseURL: base, deviceId: "dev_1", name: "iPhone", activeAt: kT0 + 600))
          #expect(m.status(now: kT0) == .activating(record))
          #expect(m.status(now: kT0 + 600) == .active(record))
      }

      @Test func aServerWithoutApprovalsSaysSo() async {
          let source = FakeApprovalsSource()
          source.registerError = ApiError(message: "Not Found", statusCode: 404)
          source.devicesError = ApiError(message: "Not Found", statusCode: 404)
          let m = make(source)
          #expect(await m.register(name: "iPhone") == kApprovalsNotOnServer)
          await m.load()
          #expect(m.devices.error.map(approvalsErrorText) == kApprovalsNotOnServer)
      }

      @Test(arguments: [(572.2, "9:33"), (5.0, "0:05"), (0.4, "0:01"), (0.0, "0:00"), (-3.0, "0:00"), (3600.0, "60:00")])
      func countdownRoundsUpToWholeSeconds(remaining: Double, text: String) {
          #expect(approvalCountdown(remaining) == text)
      }
  }
  ```

  The rest of `ApprovalDevicesModelTests` (`make(…)` uses a real `SoftwareApprovalSigner` unless noted):

  | Test | Arrange | Expect |
  |---|---|---|
  | `aReceiptWithoutActiveAtAssumesTenMinutes` | `receipt = ApprovalDeviceReceipt(json: ["device_id": "dev_1"])` | register → nil; `record.activeAt == kT0 + 600` |
  | `registeringAgainRetiresTheOldDevice` | stored record `dev_old`; receipt `dev_2` | `deleteCalls == ["dev_old"]`; record id `dev_2` |
  | `aFailedRegistrationLeavesNoKeyAndNoRecord` | `registerError = ApiError(message: "boom", statusCode: 500)` | returns "boom"; `signer.publicKey() == nil`; no record |
  | `cancelledFaceIDRegistersNothing` | `FakeApprovalSigner` with `authorizeError = .cancelled` | returns "Registration cancelled."; no register call; no record |
  | `removingThisIPhoneDeletesItsKeyAndRecord` | register (receipt `dev_1`), then `remove("dev_1")` | nil; `deleteCalls == ["dev_1"]`; no key; no record |
  | `removingAnotherDeviceKeepsThisOne` | stored record `dev_1`; `remove("dev_9")` | nil; `deleteCalls == ["dev_9"]`; record `dev_1` kept |
  | `aRecordTheServerNoLongerHasIsFlagged` | stored record `dev_1`; `devicesList` holds only `dev_other`; `load()` | `status(now: kT0) == .missingOnServer(record)` |
  | `nameValidation` | — | `"   "` → "Enter a name for this iPhone."; 65 × "a" → "Use 64 characters or fewer."; `" iPhone "` → nil |
  | `servicesBuildTheApprovalPieces` | `AppServices(apiClient: DataStub().client)` | no device record for its base URL; `approvalsRepository.client.baseURL == apiClient.baseURL`; on the simulator `!approvalSigner.isHardwareBacked` |
  | `theDevicesRouteParsesAndPrints` | — | `Route(path: "/settings/approval-devices") == .approvalDevices`, and its `path` is that string |

- [ ] **Step 3: Run them and see them fail**

  Run: `xct ApprovalDevicesModelTests`
  Expected: build failure, "cannot find 'ApprovalDevicesModel' in scope".

- [ ] **Step 4: Implement `ApprovalDevicesModel.swift` and `approvalCountdown`**

  - `approvalDeviceNameError`: trimmed empty → "Enter a name for this iPhone."; more than 64 characters → "Use 64 characters or fewer."; else nil.
  - `status(now:)`: no record for `baseURL()` → `.notRegistered`; `devices` loaded with no row for the record's id → `.missingOnServer`; `now < activeAt` → `.activating`; else `.active`.
  - `load()`: `devices = await devices.refreshing { try await source().devices() }`.
  - `register(name:)` returns nil and does nothing while `registering`. Otherwise:
    1. Validate the trimmed name; return the message if it fails.
    2. `old = store.record(for: baseURL())`; `pub = try signer.createKey()` (`.unavailable` → "This iPhone has no Secure Enclave, so it can't approve requests.").
    3. Self-test: `try await signer.authorize(reason: "Register this iPhone to approve Jarvis's requests").sign(ApprovalSigning.selfTestMessage)`, then require `ApprovalSigning.verify(der, selfTestMessage, publicKey: pub)`. On cancel: `deleteKey()`, `store.clear()`, return "Registration cancelled.". On any other failure (including a signature that fails `verify`): the same cleanup, return "Couldn't use the new key: " followed by the signer error's text (`"signature check failed"` for a failed `verify`).
    4. `receipt = try await source().registerDevice(name: trimmed, publicKey: pub)`. On error: `deleteKey()`, `store.clear()`, return `approvalsErrorText(error)`.
    5. `store.save(ApprovalDeviceRecord(baseURL:, deviceId: receipt.deviceId, name: trimmed, activeAt: receipt.activeAt ?? clock() + kApprovalActivationDelay))`.
    6. If `old` had a different id: `try? await source().deleteDevice(old.deviceId)`.
    7. `await load()`; return nil.
  - `remove(_:)`: insert the id into `removing`, then `try await source().deleteDevice(id)`. If it is this iPhone's record → `signer.deleteKey()` and `store.clear()`. Reload; return nil, or `approvalsErrorText(error)`.
  - `approvalCountdown` in `ApprovalPresentation.swift`: `s = max(0, Int(ceil(remaining)))` → `"\(s / 60):"` followed by `s % 60` as two digits.

- [ ] **Step 5: Wire services and the route**

  - `AppServices+Repositories.swift`: `var approvalsRepository: ApprovalsRepository { ApprovalsRepository(client: apiClient) }`.
  - `AppServices.swift`, after `widgetDataSyncer`:
    ```swift
    /// Signs approval decisions: the Secure Enclave on a device, a software key on the simulator.
    @ObservationIgnored private(set) lazy var approvalSigner: any ApprovalSigner = ApprovalSigners.platformDefault(storage: storage)
    /// This iPhone's approver registration, per server.
    @ObservationIgnored private(set) lazy var approvalDeviceStore = ApprovalDeviceStore(storage: storage)
    ```
  - `Route.swift`: add `case approvalDevices` (doc comment "Settings › Jarvis approvals"). In the two-part parse, beside the `notifications` line, add `case "settings" where id == "approval-devices": self = .approvalDevices`.
  - `Route+Path.swift`: `case .approvalDevices: return "/settings/approval-devices"`.
  - `RouteDestination.swift`: `case .approvalDevices: ApprovalDeviceSettingsView()`.

- [ ] **Step 6: Build `ApprovalDeviceSettingsView` and the Settings row**

  `ApprovalDeviceSettingsView` builds its model in `onAppear` from `services` (as `TokenUsageView` does) and loads it in `.task`. It is a `List` with `.dsList()`, titled "Jarvis approvals" in inline mode:
  - First clear row: "Jarvis asks before it moves live money or does something it can't undo. Approve or deny those requests here, with Face ID."
  - `DSSection("This iPhone")` renders `status(now:)` inside `TimelineView(.periodic(from: .now, by: 1))`:
    - notRegistered: `DSField("Name") { DSTextField("iPhone", text: $name) }`, prefilled "iPhone"; a primary full-width "Register this iPhone" button, showing a spinner and disabled while `registering`; help "The server sends a Discord alert, and this iPhone can approve 10 minutes after it registers."
    - activating: `StatusDot("Becomes an approver in \(approvalCountdown(activeAt - now))", tone: .warn)`.
    - active: `StatusDot("Approves Jarvis's requests", tone: .live)` and the registered name.
    - missingOnServer: `DSStatusDot(.warn)` + "The server no longer lists this iPhone. Register it again."
    - Registered states also offer:
      - "Register again" (secondary), behind a `ConfirmRequest` titled "Register again": "This iPhone gets a new key. Its current registration stops working, and the new one can approve after 10 minutes."
      - "Remove this iPhone" (destructive), behind a `ConfirmRequest` titled "Remove this iPhone": "This iPhone stops approving Jarvis's requests until you register it again."
  - `DSSection("Registered approvers")` shows each server device as `EntityRow(name, subtitle: …)` ("This iPhone", "Active", or "Active in m:ss"). The other devices get a "Remove" swipe action (loss tint) behind a confirm. On failure, show `ErrorRow(message: approvalsErrorText(error))` with Retry.
  - When `!model.isHardwareBacked`, add a caption row: "Simulator: this uses a software key, not the Secure Enclave."
  - Register and remove results show as a `Toast`: error style for a message; success "Registered. This iPhone can approve in 10 minutes."

  `SettingsView.swift`, at the end of `DSSection("Security")` after "Require unlock on launch":
  ```swift
  NavigationLink(value: Route.approvalDevices) {
      SettingsRow(icon: "verified_user", tile: "checkmark.shield", title: "Jarvis approvals",
                  subtitle: "Approve agent actions with Face ID on this iPhone") { EmptyView() }
  }
  ```

- [ ] **Step 7: Run the tests and see them pass**

  Run: `xct ApprovalDevicesModelTests RouteTests SessionAndServicesTests`
  Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 8: Commit**

  Run `mcp__gitnexus__detect_changes()`; expect the new files plus exactly the shared-file lines above. Then:
  ```bash
  git add ios/IntelliStock/Features/Approvals ios/IntelliStock/App/AppServices.swift ios/IntelliStock/App/AppServices+Repositories.swift \
    ios/IntelliStock/Core/Navigation ios/IntelliStock/Features/Settings/Views/SettingsView.swift ios/IntelliStockTests/Features/Approvals
  git commit -m "feat(ios): register this iPhone as a Jarvis approver

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 6: Approvals list and detail screens

**Files:**
- Create: `ios/IntelliStock/Features/Approvals/Model/ApprovalsListModel.swift`
- Modify: `ios/IntelliStock/Features/Approvals/Model/ApprovalPresentation.swift` (add the helpers below)
- Create: `ios/IntelliStock/Features/Approvals/Views/ApprovalsView.swift`
- Create: `ios/IntelliStock/Features/Approvals/Views/ApprovalDetailView.swift`
- Create: `ios/IntelliStock/Features/Approvals/Views/ApprovalComponents.swift`
- Modify: `ios/IntelliStock/Core/Navigation/Route.swift`, `Route+Path.swift`, `RouteDestination.swift` (cases `approvals`, `approval(String)`)
- Modify: `ios/IntelliStock/App/MoreTabView.swift` (one entry, first in `items`)
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalsListModelTests.swift`
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalPresentationTests.swift`

**Interfaces:**
- Consumes: Tasks 1–5 (`ApprovalsSource`, `ApprovalsRepository.listLimit`, `ApprovalDetailModel`, `ApprovalGate`, `kApprovalRegisterFirst`, `approvalsErrorText`, `kApprovalsNotOnServer`, `approvalCountdown`, `Route.approvalDevices`, and `services.approvalsRepository` / `approvalSigner` / `approvalDeviceStore`); DS components (`DSSection`, `EntityRow`, `StatusDot`, `DSStatusTone`, `EmptyState`, `ErrorRow`, `ConfirmRequest`/`.confirmAlert`, `Toast`/`.toast`, `DSKeyValueRow`).
- Produces:
  ```swift
  nonisolated struct ApprovalsListState: Hashable, Sendable { var waiting: [McpApproval] = []; var recent: [McpApproval] = []; var refreshError: String? }
  nonisolated func splitApprovals(_ rows: [McpApproval], now: Date) -> (waiting: [McpApproval], recent: [McpApproval])
  @Observable final class ApprovalsListModel {
      static let pollEvery: Duration = .seconds(15)
      private(set) var state: Loadable<ApprovalsListState>
      init(source: @escaping () -> any ApprovalsSource, clock: @escaping () -> Date = Date.init)
      func load() async          // GET status=all, limit=ApprovalsRepository.listLimit
      func refresh() async       // a failure keeps the last list and sets refreshError
      func poll(lifecycle: AppLifecycle?, sleep: @escaping PollingSleep = realPollingSleep) async
  }
  // ApprovalPresentation.swift additions
  nonisolated func approvalDisplayStatus(_ a: McpApproval, now: Date) -> String   // "expired" for a pending one past expires_at
  nonisolated func approvalStatusLabel(_ status: String) -> String
  nonisolated func approvalStatusTone(_ status: String) -> DSStatusTone
  nonisolated func moneyStateLabel(_ state: String) -> String
  nonisolated func moneyStateTone(_ state: String) -> DSStatusTone
  nonisolated func expiryLabel(remaining: TimeInterval) -> String   // "Expires in m:ss" | "Expired"
  nonisolated func approvalParametersText(_ json: JSON) -> String   // 2-space pretty JSON; "None" for .null or {}
  // Route.approvals ⇄ "/approvals" → ApprovalsView(); Route.approval(id) ⇄ "/approvals/{id}" → ApprovalDetailView(approvalId:)
  ```
  Labels and tones:

  | status | label | tone |
  |---|---|---|
  | pending | Waiting for you | warn |
  | approved | Approved · running | live |
  | executed | Done | positive |
  | failed | Failed | error |
  | denied | Denied | off |
  | expired | Expired | off |
  | anything else | the raw status | off |

  | money_state | label | tone |
  |---|---|---|
  | live | Live money | error |
  | unknown | Unknown account · treated as live | warn |
  | protected | Protected paper target | warn |
  | irreversible | Can't be undone | error |
  | anything else | the raw value | off |

- [ ] **Step 1: Impact analysis**

  Run `mcp__gitnexus__impact` (upstream) on `Route`, `RouteDestination` and `MoreTabView`, and report the blast radius. If GitNexus can't resolve them, grep as in Task 5; `MoreTabView.items` is private and read only by `MoreTabView.body`.

- [ ] **Step 2: Write the failing tests**

  ```swift
  struct ApprovalPresentationTests {
      @Test(arguments: [(872.0, "Expires in 14:32"), (5.0, "Expires in 0:05"), (0.4, "Expires in 0:01"), (0.0, "Expired"), (-3.0, "Expired")])
      func expiryCountsDownAndNeverShowsZeroWhileOpen(remaining: Double, text: String) {
          #expect(expiryLabel(remaining: remaining) == text)
      }
  }

  struct ApprovalsListModelTests {
      @Test func splitsWaitingFromRecentAndOrdersThem() {
          let soon = approvalA(expiresAt: kT0 + 60), later = approvalA(expiresAt: kT0 + 600)
          let lapsed = approvalA(expiresAt: kT0 - 1)                                    // created 06:00
          let done = McpApproval(json: ["id": "ap_d", "status": "executed", "created_at": "2026-11-19T05:59:00Z"])
          let old = McpApproval(json: ["id": "ap_o", "status": "denied", "created_at": "2026-11-19T05:00:00Z"])
          let (waiting, recent) = splitApprovals([later, old, lapsed, done, soon], now: kT0)
          #expect(waiting.map(\.expiresAt) == [kT0 + 60, kT0 + 600])                    // soonest expiry first
          #expect(recent.map(\.id) == ["ap_7f3c", "ap_d", "ap_o"])                     // newest first
      }
  }
  ```

  | Test | Arrange | Expect |
  |---|---|---|
  | `ApprovalPresentationTests.statusAndMoneyLabels` | the two tables above | every row's label and tone, including the raw fallback (`"weird"` → `"weird"`, `.off`) |
  | `ApprovalPresentationTests.aPendingRequestPastItsExpiryReadsExpired` | `approvalA()` at `kT0 + 899` and `kT0 + 900`; `approvalA(status: "executed")` at `kT0 + 9999` | `"pending"`, `"expired"`, `"executed"` |
  | `ApprovalPresentationTests.parametersArePrettyAndEmptyReadsNone` | `["qty": 10, "symbol": "TQQQ"]`; `.null`; an empty object | `"{\n  \"qty\": 10,\n  \"symbol\": \"TQQQ\"\n}"`; `"None"`; `"None"` |
  | `loadAsksForAllWithLimit50` | `FakeApprovalsSource(rows: [approvalA()])`, clock `kT0` | one list call `(.all, 50)`; `waiting` ids `["ap_7f3c"]` |
  | `aFailedRefreshKeepsTheLastListAndSaysSo` | load; then `listError = ApiError(message: "Cannot reach the server. Check your connection.")` and `refresh()` | the one waiting row is kept; `refreshError` is that message |
  | `aServerWithoutApprovalsSaysSoOnTheList` | `listError = ApiError(message: "Not Found", statusCode: 404)`; `load()` | `state.error.map(approvalsErrorText) == kApprovalsNotOnServer` |
  | `pollsEvery15Seconds` | `Task { await m.poll(lifecycle: nil, sleep: manual.sleep) }` with a `ManualClock` | 1 list call, then 2 after `advance(by: .seconds(15))` (both via `eventually`) |
  | `approvalRoutesParseAndPrint` | — | `"/approvals"` ⇄ `.approvals`; `"/approvals/ap_7f3c"` ⇄ `.approval("ap_7f3c")`; `Route(path: "/approvals/a/b") == nil` |

- [ ] **Step 3: Run them and see them fail**

  Run: `xct ApprovalPresentationTests ApprovalsListModelTests`
  Expected: build failure, "cannot find 'ApprovalsListModel' in scope".

- [ ] **Step 4: Implement the list model and the presentation helpers**

  - `splitApprovals`: waiting = `isPending && !isExpired(at: now)`, sorted by `expiresAt` ascending. Recent = everything else, sorted by `createdAt` descending, nil last.
  - `load` / `refresh` follow `TokenUsageModel` and `PendingSignalsModel`. `load` sets `.failed` when there is nothing to show. `refresh` keeps the last state and records `refreshError = approvalsErrorText(error)`. Cancellation changes nothing. `poll` loads first if nothing is loaded, then runs `PollingLoop(interval: { Self.pollEvery }, sleep:)`.
  - `expiryLabel`: `remaining <= 0` → "Expired"; else "Expires in " + `approvalCountdown(remaining)`.
  - `approvalParametersText`: `(try? json.dartEncoded(indent: "  ")) ?? json.dartDescription`; `.null` or an empty object → "None".

- [ ] **Step 5: Add the routes and the More entry**

  - `Route.swift`: `case approvals` and `case approval(String)`, with doc comments. In the one-part parse: `case "approvals": self = .approvals`. In the two-part parse: `case "approvals": self = .approval(id)`.
  - `Route+Path.swift`: `case .approvals: return "/approvals"` and `case .approval(let id): return "/approvals/\(enc(id))"`.
  - `RouteDestination.swift`: `case .approvals: ApprovalsView()` and `case .approval(let id): ApprovalDetailView(approvalId: id)`.
  - `MoreTabView.swift`: make `("verified_user", "Approvals", .approvals, "checkmark.shield")` the first element of `items`.

- [ ] **Step 6: Build the screens**

  `ApprovalComponents.swift`:
  - `ApprovalRow(approval:now:)`: `EntityRow(summary, subtitle: "\(method) \(path)")` with a trailing `StatusDot(approvalStatusLabel(display), tone: approvalStatusTone(display))`. A waiting row shows `expiryLabel` instead, in `DS.Font.caption` with `.monospacedDigit()`. Agent and server text is always `Text(verbatim:)`.
  - `MoneyStateLine(state:)`: `StatusDot(moneyStateLabel(state), tone: moneyStateTone(state))`.
  - `ApprovalJSONBlock(text:)`: the text split into lines, shown as `Text(verbatim:)` in `DS.Font.mono` inside a `LazyVStack(alignment: .leading)`, with `.textSelection(.enabled)`, horizontal scrolling and `.dsCardSurface()`. Even a 256 KB result is shown in full and scrolls smoothly.

  `ApprovalsView` (title "Approvals", large) builds its model in `onAppear`, polls it in `.task(id: model == nil)` with `services.lifecycle`, and supports `.refreshable`:
  - A subtitle row: "Requests from Jarvis that wait for you".
  - When `services.approvalDeviceStore.record(for: services.apiClient.baseURL) == nil`: a row linking to `Route.approvalDevices`, "Register this iPhone to decide requests".
  - `DSSection("Waiting for you")`: `NavigationLink(value: Route.approval(a.id)) { ApprovalRow }`, inside `TimelineView(.periodic(from: .now, by: 1))`. When empty: `EmptyState(title: "Nothing waiting", subtitle: "Jarvis's live-money and can't-undo requests show up here.")`.
  - `DSSection("Recent")`: the recent rows; hidden when there are none.
  - `refreshError` as an `ErrorRow` above the sections. A first-load failure is an `ErrorRow(message: approvalsErrorText(error))` with Retry.

  `ApprovalDetailView(approvalId:)` (inline title "Request") builds its model in `onAppear` from `services.approvalsRepository`, `services.approvalSigner`, `services.approvalDeviceStore` and `{ services.apiClient.baseURL }`, and polls it in `.task`:
  - Header clear row: the summary in `DS.Font.large` (verbatim), the `MoneyStateLine`, and the status `StatusDot` for `approvalDisplayStatus`.
  - `DSSection("Request", rowMinHeight: 44)` with `DSKeyValueRow`s: "Action" (`method path`, mono), "From" (client), "Asked" (created time), "Fingerprint" (the first 12 hex characters, then "· matches" or "· doesn't match").
  - `DSSection("Query")`, only when the query is non-empty, and `DSSection("Body")`, each an `ApprovalJSONBlock(text: approvalParametersText(…))`.
  - `DSSection("Result")` when `resultStatus != nil` or `result != .null`: "HTTP \(code)" and the result block, plus "The server cut this result to 256 KB." when `resultTruncated`. "Decided" time and device id rows when present.
  - A bottom action bar (`safeAreaInset(edge: .bottom)`) inside `TimelineView(.periodic(from: .now, by: 1))`, with `let gate = model.gate(now: context.date)`:
    - `expiryLabel` while pending.
    - "Deny" (`Button(role: .destructive)`, `.dsSecondary(size: .large)`) and "Approve" (`.dsPrimary(size: .large)`), disabled per `gate.canDeny` / `gate.canApprove` and while `model.isBusy`.
    - `gate.reason` as a caption under the buttons. When the reason is `kApprovalRegisterFirst`, add `NavigationLink(value: Route.approvalDevices)` "Open Jarvis approvals".
    - While busy, the button titles read "Waiting for Face ID…" (authorizing), "Sending…" (sending) or "Checking…" (checking).
  - Approve raises `ConfirmRequest(title: "Approve this request?", body: "\(summary)\n\(moneyStateLabel(moneyState)). It runs as soon as you confirm with Face ID.", confirmLabel: "Approve", role: nil)`, whose `onConfirm` awaits `model.decide(.approve)`. Deny calls `model.decide(.deny)` directly.
  - Results become a `Toast`: `.decided` success; `.rejected` and `.blocked` error; `.notPending` and `.uncertain` info; nothing for `.cancelled` or `.ignored`.
  - A load failure shows `ErrorRow(message: approvalsErrorText(error))` with Retry.

- [ ] **Step 7: Run the tests and see them pass**

  Run: `xct ApprovalPresentationTests ApprovalsListModelTests ApprovalDetailModelTests RouteTests`
  Expected: `** TEST SUCCEEDED **`.

  Then launch the app on the simulator (Xcode, or `xcodebuild … build` plus `xcrun simctl install`/`launch`) and check:
  - More › Approvals and Settings › Jarvis approvals both open;
  - registering on the simulator shows the software-key note;
  - against a backend without (C), both screens say "This server doesn't have Jarvis approvals yet…".

- [ ] **Step 8: Commit**

  Run `mcp__gitnexus__detect_changes()`, then:
  ```bash
  git add ios/IntelliStock/Features/Approvals ios/IntelliStock/Core/Navigation ios/IntelliStock/App/MoreTabView.swift ios/IntelliStockTests/Features/Approvals
  git commit -m "feat(ios): approvals list and detail with signed approve and deny

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 7: Open the approval from a tapped push

**Files:**
- Create: `ios/IntelliStock/Features/Approvals/Model/ApprovalPush.swift`
- Modify: `ios/IntelliStock/App/AppDelegate.swift` (approval routing beside the update push)
- Modify: `ios/IntelliStock/App/AppServices.swift` (add `openApproval(_:)` after `openDeepLink(_:)`)
- Modify: `ios/IntelliStock/App/IntelliStockApp.swift` (one `attach` call in `onAppear`)
- Test: `ios/IntelliStockTests/Features/Approvals/ApprovalPushTests.swift`

**Interfaces:**
- Consumes: `ApprovalIds.isValid` (Task 1); `Route.approval` (Task 6); `AppServices.openDeepLink(_:)` (unchanged); `AppUpdateRules.isUpdatePush`.
- Produces:
  ```swift
  nonisolated enum ApprovalPush {
      static let category = "MCP_APPROVAL"
      /// The approval a tapped push names: category "MCP_APPROVAL" (top level, or inside aps) and a valid string approval_id. Else nil.
      static func approvalId(from userInfo: [AnyHashable: Any]) -> String?
  }
  // AppDelegate
  func attach(openApproval: @escaping (String) -> Void)        // forwards a tap that came before it
  func routeTap(isUpdate: Bool, approvalId: String?)           // main actor; didReceive calls it with values read off-actor
  // AppServices
  func openApproval(_ id: String)                              // valid id → openDeepLink("intellistock://approvals/<id>")
  ```

- [ ] **Step 1: Impact analysis**

  Run `mcp__gitnexus__impact` (upstream) on `AppDelegate` and its `userNotificationCenter(_:didReceive:)`, on `IntelliStockApp` and on `AppServices.openDeepLink`, and report the blast radius. If you fall back to grep, expect: `didReceive` is called only by the system; `openDeepLink` by `RootView` and `SessionAndServicesTests` (neither edited here); `attach(` only by `IntelliStockApp`.

- [ ] **Step 2: Write the failing tests**

  ```swift
  struct ApprovalPushTests {
      @Test func findsTheApprovalIdOnlyOnApprovalPushes() {
          let aps: [String: Any] = ["alert": ["title": "Jarvis wants to act", "body": "BUY 10 TQQQ on alpaca-main — LIVE"]]
          #expect(ApprovalPush.approvalId(from: ["aps": aps, "category": "MCP_APPROVAL", "approval_id": "ap_7f3c"]) == "ap_7f3c")
          #expect(ApprovalPush.approvalId(from: ["aps": ["category": "MCP_APPROVAL"], "approval_id": "ap_7f3c"]) == "ap_7f3c")
          #expect(ApprovalPush.approvalId(from: ["category": "app_update", "approval_id": "ap_7f3c"]) == nil)
          #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL"]) == nil)
          #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL", "approval_id": "../brokerages"]) == nil)
          #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL", "approval_id": 42]) == nil)
          #expect(ApprovalPush.approvalId(from: ["category": "MCP_APPROVAL", "approval_id": String(repeating: "a", count: 500)]) == nil)
      }

      @Test func aTapBeforeAttachIsHeldThenOpened() {
          let delegate = AppDelegate()
          var opened: [String] = []
          delegate.routeTap(isUpdate: false, approvalId: "ap_1")
          delegate.attach(openApproval: { opened.append($0) })
          #expect(opened == ["ap_1"])
          delegate.routeTap(isUpdate: false, approvalId: "ap_2")
          delegate.routeTap(isUpdate: true, approvalId: nil)            // an update push never opens an approval
          delegate.routeTap(isUpdate: false, approvalId: nil)
          #expect(opened == ["ap_1", "ap_2"])
      }

      @Test func openApprovalPushesTheDetailWhenSignedInAndWaitsWhenSignedOut() {
          let signedIn = [ApiBaseUrlStore.storageKey: "https://api.example.test", SessionStore.tokenKey: "jwt",
                          SessionStore.userKey: #"{"username": "pk", "has_completed_onboarding": true}"#]
          let services = AppServices(storage: InMemorySecureStorage(signedIn), biometrics: FakeBiometrics(available: true, authResult: true),
                                     widgetSync: WidgetSync(defaults: nil), pushRegistrar: NoopPushRegistrar())
          services.openApproval("ap_7f3c")
          #expect(services.router.stack(for: services.router.tab).last == .approval("ap_7f3c"))
          services.openApproval("../x")
          #expect(services.router.stack(for: services.router.tab).count == 1)

          let signedOut = AppServices(storage: InMemorySecureStorage([ApiBaseUrlStore.storageKey: "https://api.example.test"]),
                                      widgetSync: WidgetSync(defaults: nil), pushRegistrar: NoopPushRegistrar())
          signedOut.openApproval("ap_7f3c")
          #expect(signedOut.loginRedirect == "/approvals/ap_7f3c")
      }
  }
  ```

- [ ] **Step 3: Run them and see them fail**

  Run: `xct ApprovalPushTests`
  Expected: build failure, "cannot find 'ApprovalPush' in scope".

- [ ] **Step 4: Implement**

  - `ApprovalPush.approvalId`: the category is `userInfo["category"] as? String`, else `(userInfo["aps"] as? [String: Any])?["category"] as? String`. It must equal `category`. Then take `userInfo["approval_id"] as? String`, kept only when `ApprovalIds.isValid`.
  - `AppDelegate`:
    - Add `private var openApproval: ((String) -> Void)?` and `private var pendingApprovalId: String?`.
    - `attach(openApproval:)` stores the closure and flushes the pending id.
    - `routeTap(isUpdate:approvalId:)`: when `isUpdate`, call the existing `updatePushTapped()`. Otherwise pass a non-nil id to the closure, or keep it in `pendingApprovalId` if nothing is attached yet.
    - Change `didReceive` to read both values off the main actor — `let info = response.notification.request.content.userInfo`, `let isUpdate = AppUpdateRules.isUpdatePush(info)`, `let id = ApprovalPush.approvalId(from: info)` — then `await routeTap(isUpdate: isUpdate, approvalId: id)`. Only Sendable values cross to the main actor.
    - `willPresent` is unchanged: approval pushes show their banner in the foreground.
  - `AppServices.openApproval`: `guard ApprovalIds.isValid(id), let url = URL(string: "intellistock:/" + Route.approval(id).path) else { return }`, then `openDeepLink(url)`. This reuses the existing gate logic: the route opens at once when signed in and becomes `loginRedirect` when signed out. While locked, the route is pushed behind the lock and shows on unlock.
  - `IntelliStockApp.swift`, in `onAppear`: `appDelegate.attach(openApproval: { [services] id in services.openApproval(id) })`.

- [ ] **Step 5: Run the tests and see them pass**

  Run: `xct ApprovalPushTests SessionAndServicesTests AppUpdateCenterTests`
  Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 6: Commit**

  Run `mcp__gitnexus__detect_changes()`; expect `ApprovalPush.swift`, the test, and the three shared files. Then:
  ```bash
  git add ios/IntelliStock/Features/Approvals/Model/ApprovalPush.swift ios/IntelliStock/App/AppDelegate.swift ios/IntelliStock/App/AppServices.swift \
    ios/IntelliStock/App/IntelliStockApp.swift ios/IntelliStockTests/Features/Approvals/ApprovalPushTests.swift
  git commit -m "feat(ios): open the approval from a tapped MCP_APPROVAL push

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

### Task 8: Whole-suite run, device build, open the PR

**Files:** none new. Any fix goes in the file that owns it, with a test.

- [ ] **Step 1: Full suite**

  Run the test command without `-only-testing`.
  Expected: `** TEST SUCCEEDED **`, with every pre-existing suite still green.

- [ ] **Step 2: Device build (compiles the Secure Enclave path for the device SDK)**

  ```bash
  cd ios && xcodebuild -project IntelliStock.xcodeproj -scheme IntelliStock -destination 'generic/platform=iOS' \
    -derivedDataPath build/dd-approvals CODE_SIGNING_ALLOWED=NO build 2>&1 | tail -5
  ```
  Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Guard checks**

  ```bash
  /usr/bin/grep -rn "SoftwareApprovalSigner(" ios/IntelliStock        # only the simulator branch of ApprovalSigners.platformDefault
  /usr/bin/grep -rnE "print\(|NSLog|os_log|Logger\(" ios/IntelliStock/Features/Approvals   # nothing
  git diff --stat origin/main -- ios/IntelliStock/Features/Swing ios/IntelliStock/Features/Instances/Views/InstanceDetailView.swift \
    ios/IntelliStockTests/Features/Instances ios/IntelliStockTests/Snapshots/SnapshotScreens+Shell.swift   # empty
  ```

- [ ] **Step 4: On-device checklist for the operator (once build C is deployed; not blocking the PR)**

  Install with `ios/scripts/deploy.sh` (or ship with `ios/scripts/release.sh`), then:
  1. Settings › Jarvis approvals › Register: exactly one Face ID sheet ("Register this iPhone…"), a Discord alert, and a countdown from 10:00.
  2. Before the 10 minutes are up, a gated request is refused with the server's not-yet-active reason.
  3. After that, Approve a request on a **paper or protected** target (for example a money route on swing-paper): the confirm alert, then exactly **one** Face ID sheet reading "Approve: …", then "Done" with the result. Cancelling Face ID sends nothing, and the request stays pending on the server.
  4. Deny one: the app shows "Denied", and Jarvis's `get_approval` returns `denied`.
  5. Kill the app, trigger a request, and tap the push from the lock screen: after unlock the app opens on that request.
  6. If step 3 shows two Face ID sheets, the context reuse in `SecureEnclaveApprovalSigner.authorize` isn't working. Fix it before relying on the 60 s window.

- [ ] **Step 5: Push and open the PR (the operator merges, outside market hours)**

  Run `mcp__gitnexus__detect_changes()` over the branch, then `git push -u origin feat/ios-mcp-approvals` and `gh pr create --base main --title "feat(ios): Jarvis approvals — Face ID-signed MCP decisions"`. The PR body lists:
  - what ships;
  - the shared files touched;
  - the "Decisions made without the operator" list from this plan;
  - any contract mismatch found;
  - that end-to-end use needs build (C) deployed, and that merging redeploys the stack.

  It ends with:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  ```
