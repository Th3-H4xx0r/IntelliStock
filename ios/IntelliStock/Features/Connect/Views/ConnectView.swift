import SwiftUI

/// First-run gate and Settings' "change server" screen — `ConnectScreen` in
/// `connect_screen.dart`. Captures the backend URL, probes `/health`, and
/// saves it. At the gate there is no back affordance; pushed from Settings
/// the system back works.
struct ConnectView: View {
    @Environment(AppServices.self) private var services

    var body: some View {
        ConnectForm(services: services)
    }
}

private struct ConnectForm: View {
    @State private var model: ConnectModel
    @Environment(\.dismiss) private var dismiss
    @FocusState private var fieldFocused: Bool

    init(services: AppServices) {
        _model = State(initialValue: ConnectModel(services: services))
    }

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    AppLogoView(size: 56)
                    Text("Connect to your instance")
                        .font(.title2.bold())
                        .accessibilityAddTraits(.isHeader)
                    Text("Enter the URL of your IntelliStock backend. You can change this later in Settings.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 8)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 0, leading: 4, bottom: 0, trailing: 4))
            }

            Section {
                // Verbatim: a string-literal title is Markdown, which would
                // render the example URL as a link.
                TextField(text: $model.url, prompt: Text(verbatim: "https://your-instance.example.com")) {
                    Text("Server URL")
                }
                    .keyboardType(.URL)
                    .textContentType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.go)
                    .focused($fieldFocused)
                    .onChange(of: model.url) { model.textChanged() }
                    .onSubmit(submit)
            } footer: {
                if let error = model.error {
                    Label(error, systemImage: Symbol.named("error"))
                        .foregroundStyle(DS.Palette.danger)
                }
            }

            Section {
                Button(action: submit) {
                    ZStack {
                        Text(model.buttonLabel)
                            .opacity(model.probing ? 0 : 1)
                        if model.probing {
                            ProgressView()
                                .tint(DS.Palette.onAccent)
                        }
                    }
                    .frame(maxWidth: .infinity)
                }
                .dsProminentButton()
                .controlSize(.large)
                .disabled(model.probing)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(model.isEditing ? .automatic : .hidden, for: .navigationBar)
    }

    private func submit() {
        fieldFocused = false
        Task {
            guard let outcome = await model.submit() else { return }
            switch outcome {
            case .unchanged:
                dismiss()
            case .configured, .changed:
                // The gates move on: first run → Login; a new server cleared the
                // session → Login on a fresh shell.
                break
            }
        }
    }
}
