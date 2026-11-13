import SwiftUI

/// First-run gate and Settings' "change server" screen — `ConnectScreen` in
/// `connect_screen.dart`. Captures the backend URL, probes `/health`, and
/// saves it. At the gate there is no back affordance; pushed from Settings
/// the system back works. Styled as the web's sign-in page: the app tile,
/// a 24/500 title and its line, then a card with the labelled field and
/// the full-width primary button.
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
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                AppLogoView(size: 48)
                    .overlay {
                        RoundedRectangle(cornerRadius: 48 * 0.23, style: .continuous)
                            .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                    }
                VStack(alignment: .leading, spacing: 8) {
                    Text("Connect to your instance")
                        .font(DS.Font.pageTitle)
                        .foregroundStyle(DS.Text.primary)
                        .accessibilityAddTraits(.isHeader)
                    Text("Enter the URL of your IntelliStock backend. You can change this later in Settings.")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 24)

                // The form card, as the web's sign-in card.
                VStack(alignment: .leading, spacing: 20) {
                    DSField("Server URL") {
                        // Verbatim: a string-literal title is Markdown, which would
                        // render the example URL as a link.
                        TextField(text: $model.url, prompt: Text(verbatim: "https://your-instance.example.com").foregroundStyle(DS.Text.faint)) {
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
                        .frame(minHeight: 40)
                        .dsInput(focused: fieldFocused)
                    }

                    if let error = model.error {
                        ErrorRow(message: error)
                    }

                    Button(action: submit) {
                        ZStack {
                            Text(model.buttonLabel)
                                .opacity(model.probing ? 0 : 1)
                            if model.probing {
                                ProgressView()
                                    .tint(DS.Palette.onAccent)
                            }
                        }
                    }
                    .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
                    .disabled(model.probing)
                }
                .padding(24)
                .dsCardSurface()
                .padding(.top, 32)
            }
            .frame(maxWidth: 384, alignment: .leading)
            .padding(.horizontal, DS.pageGutter)
            .padding(.top, 48)
            .padding(.bottom, 24)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(DS.Surface.page.ignoresSafeArea())
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
