import SwiftUI
import UIKit

/// Claude Code CLI (subscription) setup — `ClaudeCliSetupPanel` in
/// `claude_setup_panel.dart`: status, the paste-back re-authentication flow
/// and sign-out. Rendered inside the model form's CLI section.
struct ClaudeCliSetupPanel: View {
    let cliPath: String

    @Environment(AppServices.self) private var services
    @Environment(\.openURL) private var openURL
    @State private var model: ClaudeSetupModel?
    @State private var signOutRequest: ConfirmRequest?
    @State private var toast: Toast?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let model {
                content(model)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Palette.info.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
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
        CliPanelHeader(title: "Claude Code CLI (subscription) setup", loading: model.statusLoading) {
            Task { await model.fetchStatus() }
        }

        if model.statusLoading {
            Text("Probing claude CLI status…").font(.caption2).foregroundStyle(.secondary)
        } else if !model.statusError.isEmpty {
            Text("Status probe failed: \(model.statusError)").font(.caption2).foregroundStyle(DS.Palette.danger)
        } else {
            ChatFlowLayout(spacing: 16) {
                CliStatusChip(label: "installed", value: model.installed ? "✓ yes" : "✗ no",
                              color: model.installed ? DS.Palette.success : DS.Palette.warning)
                if model.installed, let version = model.version {
                    CliStatusChip(label: "version", value: version, color: .primary)
                }
                if model.installed {
                    CliStatusChip(label: "authenticated", value: model.authenticated ? "✓ yes" : "✗ no",
                                  color: model.authenticated ? DS.Palette.success : DS.Palette.warning)
                }
                if model.installed, model.authenticated, let account = model.account, !account.isEmpty {
                    CliStatusChip(label: "account", value: account, color: .primary)
                }
            }
        }

        if !model.statusLoading, model.statusError.isEmpty {
            if !model.authMessage.isEmpty {
                Text(model.authMessage).font(.caption2).foregroundStyle(.secondary)
            }
            if !model.installed {
                Text("The claude binary is not installed on the server. Install it on the server before re-authenticating.")
                    .font(.caption2)
                    .foregroundStyle(DS.Palette.warning)
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
            .font(.caption2)
            .foregroundStyle(.secondary)
            .padding(.top, 4)

        HStack(spacing: 8) {
            Button {
                Task { await model.startLogin() }
            } label: {
                HStack(spacing: 6) {
                    if model.loginStarting { ProgressView().tint(DS.Palette.onAccent) }
                    Text(model.loginStarting ? "Starting…" : "Re-authenticate Claude")
                }
            }
            .dsProminentButton()
            .controlSize(.small)
            .disabled(model.loginStarting || model.submitting)
            if live {
                Button("Cancel") { Task { await model.cancelLogin() } }
                    .buttonStyle(.borderless)
                    .font(.footnote)
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
                .font(.caption2)
                .foregroundStyle(.secondary)
            CliUrlRow(url: model.loginUrl, copied: "Copied") { message in
                toast = Toast(message, style: .success)
            }
            TextField("Paste authorization code", text: $model.code)
                .font(.system(.footnote, design: .monospaced))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .padding(10)
                .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                .disabled(model.submitting)
            Button {
                Task { await model.submitCode() }
            } label: {
                HStack(spacing: 6) {
                    if model.submitting { ProgressView().tint(DS.Palette.onAccent) }
                    Text(model.submitting ? "Submitting…" : "Submit Code")
                }
            }
            .dsProminentButton()
            .controlSize(.small)
            .disabled(model.submitting)
        }

        if !model.submitMessage.isEmpty {
            ModelInfoBox(text: model.submitMessage, color: model.submitOk ? DS.Palette.success : DS.Palette.danger)
        }

        if model.authenticated {
            Button("Sign Out of Claude") {
                signOutRequest = ConfirmRequest(
                    title: "Sign out of Claude?",
                    body: "All strategies using claude-cli will need to re-authenticate.",
                    confirmLabel: "Sign Out",
                    role: .destructive,
                    onConfirm: { await model.logout() }
                )
            }
            .font(.caption)
            .buttonStyle(.borderless)
            .tint(.secondary)
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
        VStack(alignment: .leading, spacing: 8) {
            if let model {
                content(model)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Palette.info.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
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
        // No onDisappear stop: this is a Form row, which disappears whenever
        // it scrolls away. The polls stop with the model, when the sheet
        // closes (or the provider changes and the panel goes).
        .onChange(of: cliPath) { _, new in model?.cliPath = new }
    }

    @ViewBuilder
    private func content(_ model: CodexSetupModel) -> some View {
        CliPanelHeader(title: "OpenAI Codex CLI setup", loading: model.statusLoading) {
            Task { await model.fetchStatus() }
        }

        if model.statusLoading {
            Text("Probing codex CLI status...").font(.caption2).foregroundStyle(.secondary)
        } else if !model.statusError.isEmpty {
            Text("Status probe failed: \(model.statusError)").font(.caption2).foregroundStyle(DS.Palette.danger)
        } else {
            ChatFlowLayout(spacing: 16) {
                CliStatusChip(label: "installed", value: model.installed ? "✓ yes" : "✗ no",
                              color: model.installed ? DS.Palette.success : DS.Palette.warning)
                if model.installed, let version = model.version {
                    CliStatusChip(label: "version", value: version, color: .primary)
                }
                if model.installed {
                    CliStatusChip(label: "authenticated", value: model.authenticated ? "✓ yes" : "✗ no",
                                  color: model.authenticated ? DS.Palette.success : DS.Palette.warning)
                }
            }
        }

        if !model.statusLoading, model.statusError.isEmpty {
            if !model.installed { installSection(model) }
            if model.installed, !model.authenticated { loginSection(model) }
            if model.installed, model.authenticated {
                Text("✓ Codex CLI is installed and authenticated. Strategies can now select codex-cli.")
                    .font(.caption2)
                    .foregroundStyle(DS.Palette.success)
                if !model.authMessage.isEmpty {
                    Text(model.authMessage).font(.caption2).foregroundStyle(.secondary)
                }
                Button("Sign Out of OpenAI") {
                    signOutRequest = ConfirmRequest(
                        title: "Sign out of OpenAI?",
                        body: "All strategies using codex-cli will need to re-authenticate.",
                        confirmLabel: "Sign Out",
                        role: .destructive,
                        onConfirm: { await model.logout() }
                    )
                }
                .font(.caption)
                .buttonStyle(.borderless)
                .tint(.secondary)
            }
        }
    }

    @ViewBuilder
    private func installSection(_ model: CodexSetupModel) -> some View {
        if model.installMethod == "unknown" {
            Text("The backend has neither npm nor brew available. Rebuild the backend image with INSTALL_CODEX_CLI=1.")
                .font(.caption2)
                .foregroundStyle(DS.Palette.warning)
        } else {
            Text("Codex CLI is not installed. Click to install via \(model.installMethod == "brew" ? "brew install codex" : "npm install -g @openai/codex").")
                .font(.caption2)
                .foregroundStyle(.secondary)
            let running = model.installState == "running"
            Button {
                Task { await model.startInstall() }
            } label: {
                HStack(spacing: 6) {
                    if running { ProgressView().tint(DS.Palette.onAccent) }
                    Text(running ? "Installing..." : "Install Codex CLI")
                }
            }
            .dsProminentButton()
            .controlSize(.small)
            .disabled(running)
        }
        if !model.installState.isEmpty {
            HStack(spacing: 0) {
                Text("Install state: ").foregroundStyle(.secondary)
                Text(model.installState).foregroundStyle(CliLoginStateLine.color(model.installState))
                if let exit = model.installExitCode {
                    Text(" (exit \(exit))").foregroundStyle(.tertiary)
                }
            }
            .font(.caption2)
            if !model.installLog.isEmpty {
                ScrollView {
                    Text(verbatim: model.installLog.joined(separator: "\n"))
                        .font(.system(.caption2, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                }
                .defaultScrollAnchor(.bottom)
                .frame(maxHeight: 100)
                .background(DS.Surface.panel, in: .rect(cornerRadius: 4))
            }
            if !model.installError.isEmpty {
                Text(model.installError).font(.caption2).foregroundStyle(DS.Palette.danger)
            }
        }
    }

    @ViewBuilder
    private func loginSection(_ model: CodexSetupModel) -> some View {
        Text("Codex is installed but not authenticated. Start the OpenAI device-code login.")
            .font(.caption2)
            .foregroundStyle(.secondary)
        HStack(spacing: 8) {
            Button {
                Task { await model.startLogin() }
            } label: {
                HStack(spacing: 6) {
                    if model.loginWaiting { ProgressView().tint(DS.Palette.onAccent) }
                    Text(model.loginWaiting ? "Waiting for sign-in..." : "Sign In with OpenAI")
                }
            }
            .dsProminentButton()
            .controlSize(.small)
            .disabled(model.loginWaiting)
            if model.loginWaiting {
                Button("Cancel") { Task { await model.cancelLogin() } }
                    .buttonStyle(.borderless)
                    .font(.footnote)
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
                    Text("Code: ").font(.caption2).foregroundStyle(.secondary)
                    Text(verbatim: model.loginPairingCode)
                        .font(.system(.title2, design: .monospaced).weight(.bold))
                        .tracking(8)
                        .foregroundStyle(DS.Palette.success)
                        .textSelection(.enabled)
                    Spacer()
                    CliCopyButton(text: model.loginPairingCode) {
                        toast = Toast("Copied to clipboard", style: .success)
                    }
                }
            }
            if !model.loginError.isEmpty {
                Text(model.loginError).font(.caption2).foregroundStyle(DS.Palette.danger)
            }
        }
    }
}

// MARK: - Shared pieces

private struct CliPanelHeader: View {
    let title: String
    let loading: Bool
    let onRefresh: () -> Void

    var body: some View {
        HStack {
            Text(title).font(.footnote.weight(.semibold))
            Spacer()
            Button(action: onRefresh) {
                Group {
                    if loading { ProgressView() } else { Image(systemName: Symbol.named("refresh")) }
                }
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .disabled(loading)
            .accessibilityLabel("Refresh status")
        }
    }
}

private struct CliStatusChip: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        HStack(spacing: 2) {
            Text("\(label):").foregroundStyle(.secondary)
            Text(verbatim: value).foregroundStyle(color)
        }
        .font(.caption2)
    }
}

/// `Login state: …` — success green, failed / expired / cancelled red, else orange.
private struct CliLoginStateLine: View {
    let state: String

    static func color(_ s: String) -> Color {
        if s == "success" { return DS.Palette.success }
        if s == "failed" || s == "expired" || s == "cancelled" { return DS.Palette.danger }
        return DS.Palette.warning
    }

    var body: some View {
        HStack(spacing: 0) {
            Text("Login state: ").foregroundStyle(.secondary)
            Text(state).foregroundStyle(Self.color(state))
        }
        .font(.caption2)
    }
}

/// `Open URL: …` with copy; the URL also opens with a tap.
private struct CliUrlRow: View {
    let url: String
    let copied: String
    let onCopied: (String) -> Void

    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Open URL: ").font(.caption2).foregroundStyle(.secondary)
            Button {
                if let link = URL(string: url) { openURL(link) }
            } label: {
                Text(verbatim: url)
                    .font(.system(.caption2, design: .monospaced))
                    .multilineTextAlignment(.leading)
            }
            .buttonStyle(.borderless)
            .tint(DS.Palette.info)
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
                .font(.caption)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .tint(.secondary)
        .accessibilityLabel("Copy")
    }
}
