import Foundation
import Observation

/// Tolerant string coercion — `_asStr` in `claude_setup_panel.dart`:
/// maps and lists are JSON-encoded instead of crashing a cast.
nonisolated func cliSetupString(_ v: JSON) -> String? {
    switch v {
    case .null: nil
    case .string(let s): s
    case .object, .array: (try? v.dartEncoded()) ?? v.dartDescription
    default: v.dartDescription
    }
}

/// A login URL a setup panel may show: http(s), no user info, and a host on
/// the panel's allow-list.
nonisolated func isSafeCliLoginURL(_ url: String, allowedHosts: Set<String>) -> Bool {
    guard !url.isEmpty, let components = URLComponents(string: url) else { return false }
    guard let scheme = components.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return false }
    if components.user?.isEmpty == false || components.password?.isEmpty == false { return false }
    return allowedHosts.contains((components.host ?? "").lowercased())
}

/// Claude Code CLI setup — `_ClaudeCliSetupPanelState`: status, the
/// paste-back OAuth flow (start → open URL → paste code → submit), cancel and
/// sign-out.
@Observable
final class ClaudeSetupModel {
    static let allowedLoginHosts: Set<String> = [
        "claude.ai", "www.claude.ai", "claude.com", "www.claude.com", "console.anthropic.com", "anthropic.com",
    ]

    // Status
    private(set) var statusLoading = true
    private(set) var statusError = ""
    private(set) var installed = false
    private(set) var version: String?
    private(set) var authenticated = false
    private(set) var account: String?
    private(set) var authMessage = ""

    // Login job
    private(set) var loginJobId: String?
    private(set) var loginState = ""
    private(set) var loginUrl = ""
    private(set) var loginError = ""
    private(set) var loginStarting = false

    // Code submission
    var code = ""
    private(set) var submitting = false
    private(set) var submitMessage = ""
    private(set) var submitOk = false

    var cliPath: String

    @ObservationIgnored private let repository: () -> ModelRepository

    init(cliPath: String, repository: @escaping () -> ModelRepository) {
        self.cliPath = cliPath
        self.repository = repository
    }

    /// A login is live: a URL and a job, not yet successful.
    var loginLive: Bool { !loginUrl.isEmpty && loginJobId != nil && loginState != "success" }

    func fetchStatus() async {
        statusLoading = true
        statusError = ""
        do {
            let s = JSON.object(try await repository().claudeAuthStatus())
            installed = s["installed"].boolValue ?? false
            version = cliSetupString(s["version"])
            authenticated = s["authenticated"].boolValue ?? false
            account = cliSetupString(s["account"])
            authMessage = cliSetupString(s["auth_message"]) ?? ""
            statusLoading = false
        } catch {
            if error is CancellationError { statusLoading = false; return }
            statusLoading = false
            statusError = llmErrorText(error)
        }
    }

    func startLogin() async {
        loginStarting = true
        loginJobId = nil
        loginState = ""
        loginUrl = ""
        loginError = ""
        code = ""
        submitMessage = ""
        submitOk = false
        do {
            let data = JSON.object(try await repository().claudeLoginStart([
                "cli_path": .string(cliPath.isEmpty ? "claude" : cliPath),
            ]))
            let incoming = cliSetupString(data["login_url"]) ?? ""
            let safe = isSafeCliLoginURL(incoming, allowedHosts: Self.allowedLoginHosts) ? incoming : ""
            loginStarting = false
            loginJobId = cliSetupString(data["job_id"])
            loginState = cliSetupString(data["state"]) ?? ""
            loginUrl = safe
            if !incoming.isEmpty && safe.isEmpty {
                loginError = "claude returned a non-Anthropic login URL; ignoring for safety"
            } else {
                loginError = cliSetupString(data["error"]) ?? ""
            }
        } catch {
            loginStarting = false
            loginState = "failed"
            loginError = llmErrorText(error)
        }
    }

    func submitCode() async {
        guard let jobId = loginJobId else { return }
        let trimmed = code.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            submitOk = false
            submitMessage = "Paste the authorization code first."
            return
        }
        submitting = true
        submitMessage = "Exchanging code…"
        submitOk = false
        do {
            let data = JSON.object(try await repository().claudeLoginSubmit(jobId, trimmed))
            let state = cliSetupString(data["state"]) ?? ""
            submitting = false
            loginState = state
            loginError = cliSetupString(data["error"]) ?? ""
            if state == "success" {
                submitOk = true
                submitMessage = "✓ Claude re-authenticated"
                loginUrl = ""
                loginJobId = nil
                code = ""
                await fetchStatus()
            } else {
                let tail = cliSetupString(data["output_tail"])
                submitOk = false
                submitMessage = (loginError.isEmpty ? "Login failed" : loginError)
                    + ((tail?.isEmpty == false) ? "\n\(tail!)" : "")
            }
        } catch {
            submitting = false
            submitOk = false
            submitMessage = llmErrorText(error)
        }
    }

    func cancelLogin() async {
        if let jobId = loginJobId {
            try? await repository().claudeLoginCancel(jobId)
        }
        loginState = "cancelled"
        loginUrl = ""
        loginJobId = nil
        submitMessage = ""
    }

    /// After the confirmation: sign out (errors ignored), then refresh.
    func logout() async {
        try? await repository().claudeLogout()
        await fetchStatus()
    }
}

/// OpenAI Codex CLI setup — `_CodexCliSetupPanelState`: status, one-click
/// install with a polled log tail, device-code login with a polled pairing
/// code, cancel and sign-out.
@Observable
final class CodexSetupModel {
    static let allowedPairingHosts: Set<String> = ["chatgpt.com", "platform.openai.com", "auth.openai.com"]
    static let installPollInterval: Duration = .milliseconds(1500)
    static let loginPollInterval: Duration = .milliseconds(2000)

    // Status
    private(set) var statusLoading = true
    private(set) var statusError = ""
    private(set) var installed = false
    private(set) var version: String?
    private(set) var authenticated = false
    private(set) var authMessage = ""
    private(set) var installMethod = "unknown"

    // Install job
    private(set) var installJobId: String?
    private(set) var installState = ""
    private(set) var installExitCode: Int?
    private(set) var installLog: [String] = []
    private(set) var installError = ""

    // Login job
    private(set) var loginJobId: String?
    private(set) var loginState = ""
    private(set) var loginPairingUrl = ""
    private(set) var loginPairingCode = ""
    private(set) var loginError = ""

    var cliPath: String

    @ObservationIgnored private let repository: () -> ModelRepository
    @ObservationIgnored private let sleep: PollingSleep
    @ObservationIgnored private var installTimer: Task<Void, Never>?
    @ObservationIgnored private var loginTimer: Task<Void, Never>?

    init(cliPath: String, repository: @escaping () -> ModelRepository, sleep: @escaping PollingSleep = realPollingSleep) {
        self.cliPath = cliPath
        self.repository = repository
        self.sleep = sleep
    }

    /// `dispose`: stop both polls.
    func stop() {
        installTimer?.cancel()
        loginTimer?.cancel()
    }

    var loginWaiting: Bool { loginState == "pending" || loginState == "parsed" }

    func fetchStatus() async {
        statusLoading = true
        statusError = ""
        do {
            let s = try await repository().codexStatus()
            installed = s.installed
            version = s.version
            authenticated = s.authenticated
            authMessage = s.authMessage ?? ""
            installMethod = s.installMethod
            statusLoading = false
        } catch {
            if error is CancellationError { statusLoading = false; return }
            statusLoading = false
            statusError = llmErrorText(error)
        }
    }

    // MARK: Install

    func startInstall() async {
        installJobId = nil
        installState = "running"
        installExitCode = nil
        installLog = []
        installError = ""
        do {
            let data = JSON.object(try await repository().codexInstall())
            installJobId = data["job_id"].string
            installState = data["state"].string ?? "running"
            scheduleInstallPoll()
        } catch {
            installState = "failed"
            installError = llmErrorText(error)
        }
    }

    private func scheduleInstallPoll() {
        installTimer?.cancel()
        let sleep = sleep
        installTimer = Task { [weak self] in
            do { try await sleep(Self.installPollInterval) } catch { return }
            await self?.pollInstall()
        }
    }

    func pollInstall() async {
        guard let jobId = installJobId else { return }
        do {
            let data = JSON.object(try await repository().codexInstallJob(jobId))
            installState = data["state"].string ?? installState
            installExitCode = data["exit_code"].int
            if let tail = data["log_tail"].array { installLog = tail.map(\.dartDescription) }
            installError = data["error"].string ?? ""
            if installState == "running" {
                scheduleInstallPoll()
            } else {
                await fetchStatus()
            }
        } catch {
            if error is CancellationError { return }
            installState = "failed"
            installError = llmErrorText(error)
        }
    }

    // MARK: Login

    func startLogin() async {
        loginJobId = nil
        loginState = "pending"
        loginPairingUrl = ""
        loginPairingCode = ""
        loginError = ""
        do {
            let data = JSON.object(try await repository().codexLoginStart([
                "cli_path": .string(cliPath.isEmpty ? "codex" : cliPath),
            ]))
            let incoming = data["pairing_url"].string ?? ""
            let safe = isSafeCliLoginURL(incoming, allowedHosts: Self.allowedPairingHosts) ? incoming : ""
            loginJobId = data["job_id"].string
            loginState = data["state"].string ?? "pending"
            loginPairingUrl = safe
            loginPairingCode = data["pairing_code"].string ?? ""
            if !incoming.isEmpty && safe.isEmpty {
                loginError = "codex returned a non-OpenAI pairing URL; ignoring for safety"
            } else {
                loginError = data["error"].string ?? ""
            }
            scheduleLoginPoll()
        } catch {
            loginState = "failed"
            loginError = llmErrorText(error)
        }
    }

    private func scheduleLoginPoll() {
        loginTimer?.cancel()
        let sleep = sleep
        loginTimer = Task { [weak self] in
            do { try await sleep(Self.loginPollInterval) } catch { return }
            await self?.pollLogin()
        }
    }

    func pollLogin() async {
        guard let jobId = loginJobId else { return }
        do {
            let data = JSON.object(try await repository().codexLoginStatus(jobId))
            let state = data["state"].string ?? loginState
            let incoming = data["pairing_url"].string ?? ""
            loginState = state
            if !incoming.isEmpty, isSafeCliLoginURL(incoming, allowedHosts: Self.allowedPairingHosts) {
                loginPairingUrl = incoming
            }
            loginPairingCode = data["pairing_code"].string ?? loginPairingCode
            loginError = data["error"].string ?? ""
            if state == "pending" || state == "parsed" {
                scheduleLoginPoll()
            } else {
                await fetchStatus()
            }
        } catch {
            if error is CancellationError { return }
            loginState = "failed"
            loginError = llmErrorText(error)
        }
    }

    func cancelLogin() async {
        loginTimer?.cancel()
        if let jobId = loginJobId {
            try? await repository().codexLoginCancel(jobId)
        }
        loginState = "cancelled"
    }

    func logout() async {
        try? await repository().codexLogout()
        await fetchStatus()
    }
}
