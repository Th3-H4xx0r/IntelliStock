import SwiftUI
import UIKit

/// Claude Code CLI (subscription) setup — `ClaudeCliSetupPanel` in
/// `claude_setup_panel.dart`: status, the paste-back re-authentication flow
/// and sign-out, on the web panel's bordered surface inside the model form.
struct ClaudeCliSetupPanel: View {
    let cliPath: String

    @Environment(AppServices.self) private var services
    @Environment(\.openURL) private var openURL
    @State private var model: ClaudeSetupModel?
    @State private var signOutRequest: ConfirmRequest?
    @State private var toast: Toast?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let model {
                content(model)
            }
        }
        .cliPanelSurface()
        .confirmAlert($signOutRequest)
        .toast($toast)
        .onAppear {
            if model == nil {
                let services = services
                model = ClaudeSetupModel(cliPath: cliPath, repository: { services.modelRepository })
            }
        }
        .task(id: model == nil) {
            await model?.fetchStatus()
        }
        .onChange(of: cliPath) { _, new in model?.cliPath = new }
    }

    @ViewBuilder
    private func content(_ model: ClaudeSetupModel) -> some View {
        @Bindable var model = model
        CliPanelHeader(title: "Claude Code CLI setup", loading: model.statusLoading) {
            Task { await model.fetchStatus() }
        }

        if model.statusLoading {
            Text("Probing claude CLI status…").font(DS.Font.caption).foregroundStyle(DS.Text.muted)
        } else if !model.statusError.isEmpty {
            Text("Status probe failed: \(model.statusError)").font(DS.Font.caption).foregroundStyle(DS.Palette.loss)
        } else {
            ChatFlowLayout(spacing: 20, runSpacing: 6) {
                CliStatusChip(label: "Installed", ok: model.installed)
                if model.installed, let version = model.version {
                    CliStatusChip(label: "Version", value: version)
                }
                if model.installed {
                    CliStatusChip(label: "Authenticated", ok: model.authenticated)
                }
                if model.installed, model.authenticated, let account = model.account, !account.isEmpty {
                    CliStatusChip(label: "Account", value: account)
                }
            }
        }

        if !model.statusLoading, model.statusError.isEmpty {
            if !model.authMessage.isEmpty {
                Text(model.authMessage).font(DS.Font.caption).foregroundStyle(DS.Text.secondary)
            }
            if !model.installed {
                FormNote("The claude binary is not installed on the server. Install it there before re-authenticating.")
            } else {
                installedFlow(model)
            }
        }
    }

    @ViewBuilder
    private func installedFlow(_ model: ClaudeSetupModel) -> some View {
        @Bindable var model = model
        let live = model.loginLive
        Text(model.authenticated
             ? "Re-authenticate if the saved subscription token has expired (e.g. \"401 Invalid authentication credentials\")."
             : "Claude is installed but not authenticated. Start the sign-in flow.")
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.secondary)
            .fixedSize(horizontal: false, vertical: true)

        HStack(spacing: 8) {
            Button {
                Task { await model.startLogin() }
            } label: {
                HStack(spacing: 6) {
                    if model.loginStarting { ProgressView().controlSize(.mini) }
                    Text(model.loginStarting ? "Starting…" : "Re-authenticate Claude")
                }
            }
            .buttonStyle(.dsSecondary)
            .disabled(model.loginStarting || model.submitting)
            if live {
                Button("Cancel") { Task { await model.cancelLogin() } }
                    .buttonStyle(.dsGhost)
                    .disabled(model.submitting)
            }
        }

        if !model.loginState.isEmpty, model.loginState != "parsed" {
            CliLoginStateLine(state: model.loginState)
        }
        if !model.loginError.isEmpty, !live {
            ModelInfoBox(text: model.loginError, color: DS.Palette.danger)
        }

        if live {
            Text("1. Open the link and sign in.\n2. Copy the authorization code.\n3. Paste it below.")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.secondary)
            CliUrlRow(url: model.loginUrl, copied: "Copied") { message in
                toast = Toast(message, style: .success)
            }
            FormTextField("Paste authorization code", text: $model.code, mono: true)
                .disabled(model.submitting)
            Button {
                Task { await model.submitCode() }
            } label: {
                HStack(spacing: 6) {
                    if model.submitting { ProgressView().controlSize(.mini) }
                    Text(model.submitting ? "Submitting…" : "Submit code")
                }
            }
            .buttonStyle(.dsSecondary)
            .disabled(model.submitting)
        }

        if !model.submitMessage.isEmpty {
            ModelInfoBox(text: model.submitMessage, color: model.submitOk ? DS.Palette.success : DS.Palette.danger)
        }

        if model.authenticated {
            Button("Sign out of Claude", role: .destructive) {
                signOutRequest = ConfirmRequest(
                    title: "Sign out of Claude?",
                    body: "All strategies using claude-cli will need to re-authenticate.",
                    confirmLabel: "Sign out",
                    role: .destructive,
                    onConfirm: { await model.logout() }
                )
            }
            .buttonStyle(.dsSecondary)
        }
    }
}

/// OpenAI Codex CLI setup — `CodexCliSetupPanel` in `codex_setup_panel.dart`:
/// status, one-click install with its log, device-code sign-in and sign-out.
struct CodexCliSetupPanel: View {
    let cliPath: String

    @Environment(AppServices.self) private var services
    @State private var model: CodexSetupModel?
    @State private var signOutRequest: ConfirmRequest?
    @State private var toast: Toast?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let model {
                content(model)
            }
        }
        .cliPanelSurface()
        .confirmAlert($signOutRequest)
        .toast($toast)
        .onAppear {
            if model == nil {
                let services = services
                model = CodexSetupModel(cliPath: cliPath, repository: { services.modelRepository }, lifecycle: services.lifecycle)
            }
        }
        .task(id: model == nil) {
            await model?.fetchStatus()
        }
        // No onDisappear stop: the panel scrolls in and out of view with the
        // form. The polls stop with the model, when the sheet
        // closes (or the provider changes and the panel goes).
        .onChange(of: cliPath) { _, new in model?.cliPath = new }
    }

    @ViewBuilder
    private func content(_ model: CodexSetupModel) -> some View {
        CliPanelHeader(title: "OpenAI Codex CLI setup", loading: model.statusLoading) {
            Task { await model.fetchStatus() }
        }

        if model.statusLoading {
            Text("Probing codex CLI status…").font(DS.Font.caption).foregroundStyle(DS.Text.muted)
        } else if !model.statusError.isEmpty {
            Text("Status probe failed: \(model.statusError)").font(DS.Font.caption).foregroundStyle(DS.Palette.loss)
        } else {
            ChatFlowLayout(spacing: 20, runSpacing: 6) {
                CliStatusChip(label: "Installed", ok: model.installed)
                if model.installed, let version = model.version {
                    CliStatusChip(label: "Version", value: version)
                }
                if model.installed {
                    CliStatusChip(label: "Authenticated", ok: model.authenticated)
                }
            }
        }

        if !model.statusLoading, model.statusError.isEmpty {
            if !model.installed { installSection(model) }
            if model.installed, !model.authenticated { loginSection(model) }
            if model.installed, model.authenticated {
                Text("Codex CLI is installed and authenticated. Strategies can now select codex-cli.")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Palette.gain)
                if !model.authMessage.isEmpty {
                    Text(model.authMessage).font(DS.Font.caption).foregroundStyle(DS.Text.secondary)
                }
                Button("Sign out of OpenAI", role: .destructive) {
                    signOutRequest = ConfirmRequest(
                        title: "Sign out of OpenAI?",
                        body: "All strategies using codex-cli will need to re-authenticate.",
                        confirmLabel: "Sign out",
                        role: .destructive,
                        onConfirm: { await model.logout() }
                    )
                }
                .buttonStyle(.dsSecondary)
            }
        }
    }

    @ViewBuilder
    private func installSection(_ model: CodexSetupModel) -> some View {
        if model.installMethod == "unknown" {
            FormNote("The backend has neither npm nor brew. Rebuild the backend image with INSTALL_CODEX_CLI=1.")
        } else {
            Text("Codex CLI is not installed. Install it with \(model.installMethod == "brew" ? "brew install codex" : "npm install -g @openai/codex").")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.secondary)
                .fixedSize(horizontal: false, vertical: true)
            let running = model.installState == "running"
            Button {
                Task { await model.startInstall() }
            } label: {
                HStack(spacing: 6) {
                    if running { ProgressView().controlSize(.mini) }
                    Text(running ? "Installing…" : "Install Codex CLI")
                }
            }
            .buttonStyle(.dsSecondary)
            .disabled(running)
        }
        if !model.installState.isEmpty {
            HStack(spacing: 6) {
                Text("Install state").foregroundStyle(DS.Text.muted)
                DSStatusDot(CliLoginStateLine.tone(model.installState))
                Text(model.installState).foregroundStyle(DS.Text.soft)
                if let exit = model.installExitCode {
                    Text("(exit \(exit))").foregroundStyle(DS.Text.muted)
                }
            }
            .font(DS.Font.caption)
            .monospacedDigit()
            if !model.installLog.isEmpty {
                ScrollView {
                    Text(verbatim: model.installLog.joined(separator: "\n"))
                        .font(DS.Font.monoSmall)
                        .foregroundStyle(DS.Text.label)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                }
                .defaultScrollAnchor(.bottom)
                .frame(maxHeight: 100)
                .background(DS.Surface.page, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
            }
            if !model.installError.isEmpty {
                Text(model.installError).font(DS.Font.caption).foregroundStyle(DS.Palette.loss)
            }
        }
    }

    @ViewBuilder
    private func loginSection(_ model: CodexSetupModel) -> some View {
        Text("Codex is installed but not authenticated. Start the OpenAI device-code login.")
            .font(DS.Font.caption)
            .foregroundStyle(DS.Text.secondary)
        HStack(spacing: 8) {
            Button {
                Task { await model.startLogin() }
            } label: {
                HStack(spacing: 6) {
                    if model.loginWaiting { ProgressView().controlSize(.mini) }
                    Text(model.loginWaiting ? "Waiting for sign-in…" : "Sign in with OpenAI")
                }
            }
            .buttonStyle(.dsSecondary)
            .disabled(model.loginWaiting)
            if model.loginWaiting {
                Button("Cancel") { Task { await model.cancelLogin() } }
                    .buttonStyle(.dsGhost)
            }
        }
        if !model.loginState.isEmpty, model.loginState != "pending" {
            CliLoginStateLine(state: model.loginState)
            if !model.loginPairingUrl.isEmpty {
                CliUrlRow(url: model.loginPairingUrl, copied: "Copied to clipboard") { message in
                    toast = Toast(message, style: .success)
                }
            }
            if !model.loginPairingCode.isEmpty {
                HStack {
                    Text("Code").font(DS.Font.caption).foregroundStyle(DS.Text.muted)
                    Text(verbatim: model.loginPairingCode)
                        .font(FormFont.monoCode)
                        .foregroundStyle(DS.Text.primary)
                        .textSelection(.enabled)
                    Spacer()
                    CliCopyButton(text: model.loginPairingCode) {
                        toast = Toast("Copied to clipboard", style: .success)
                    }
                }
            }
            if !model.loginError.isEmpty {
                Text(model.loginError).font(DS.Font.caption).foregroundStyle(DS.Palette.loss)
            }
        }
    }
}

// MARK: - Shared pieces

extension View {
    /// A setup panel's surface (the web's `rounded-lg border px-4 py-3`).
    fileprivate func cliPanelSurface() -> some View {
        padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
            }
    }
}

private struct CliPanelHeader: View {
    let title: String
    let loading: Bool
    let onRefresh: () -> Void

    var body: some View {
        HStack {
            Text(title)
                .font(DS.Font.bodyMedium)
                .foregroundStyle(DS.Text.soft)
                .accessibilityAddTraits(.isHeader)
            Spacer()
            Button(action: onRefresh) {
                if loading {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: Symbol.named("refresh"))
                }
            }
            .buttonStyle(.dsIcon)
            .padding(.trailing, -8)
            .disabled(loading)
            .accessibilityLabel("Refresh status")
        }
    }
}

/// A status fact (the web's status row): the label in zinc-500, then a gain
/// (yes) or amber (no) dot and the word, or a plain monospaced value.
private struct CliStatusChip: View {
    let label: String
    var value: String?
    var ok: Bool?

    init(label: String, ok: Bool) {
        self.label = label
        self.ok = ok
    }

    init(label: String, value: String) {
        self.label = label
        self.value = value
    }

    var body: some View {
        HStack(spacing: 6) {
            Text(label)
                .foregroundStyle(DS.Text.muted)
            if let ok {
                DSStatusDot(ok ? .positive : .warn)
                Text(ok ? "Yes" : "No")
                    .foregroundStyle(DS.Text.soft)
            } else if let value {
                Text(verbatim: value)
                    .font(DS.Font.mono)
                    .foregroundStyle(DS.Text.soft)
                    .lineLimit(1)
            }
        }
        .font(DS.Font.caption)
        .accessibilityElement(children: .combine)
    }
}

/// `Login state: …` — success a gain dot, failed / expired / cancelled a
/// loss dot, anything in flight amber.
private struct CliLoginStateLine: View {
    let state: String

    static func tone(_ s: String) -> DSStatusTone {
        if s == "success" { return .positive }
        if s == "failed" || s == "expired" || s == "cancelled" { return .error }
        return .warn
    }

    var body: some View {
        HStack(spacing: 6) {
            Text("Login state").foregroundStyle(DS.Text.muted)
            DSStatusDot(Self.tone(state))
            Text(state).foregroundStyle(DS.Text.soft)
        }
        .font(DS.Font.caption)
    }
}

/// `Open URL: …` with copy; the URL also opens with a tap.
private struct CliUrlRow: View {
    let url: String
    let copied: String
    let onCopied: (String) -> Void

    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("Open URL").font(DS.Font.caption).foregroundStyle(DS.Text.muted)
            Button {
                if let link = URL(string: url) { openURL(link) }
            } label: {
                Text(verbatim: url)
                    .font(DS.Font.monoSmall)
                    .foregroundStyle(DS.Text.soft)
                    .underline(color: DS.Text.faint)
                    .multilineTextAlignment(.leading)
            }
            .buttonStyle(.plain)
            Spacer(minLength: 4)
            CliCopyButton(text: url) { onCopied(copied) }
        }
    }
}

private struct CliCopyButton: View {
    let text: String
    let onCopied: () -> Void

    var body: some View {
        Button {
            UIPasteboard.general.string = text
            onCopied()
        } label: {
            Image(systemName: Symbol.named("copy"))
        }
        .buttonStyle(.dsIcon)
        .accessibilityLabel("Copy")
    }
}
