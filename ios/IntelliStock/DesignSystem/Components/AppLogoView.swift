import SwiftUI

/// The IntelliStock app mark (the line-chart icon) from the `AppLogo` asset —
/// `AppLogo` in `app_logo.dart`. Corner radius defaults to 23 % of the size.
struct AppLogoView: View {
    var size: CGFloat = 56
    var radius: CGFloat?

    var body: some View {
        Image("AppLogo")
            .resizable()
            .interpolation(.high)
            .aspectRatio(contentMode: .fill)
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: radius ?? size * 0.23, style: .continuous))
            .accessibilityHidden(true)
    }
}

/// The app mark plus the "IntelliStock" wordmark — `AppWordmark`.
struct AppWordmark: View {
    var iconSize: CGFloat = 34

    var body: some View {
        HStack(spacing: 10) {
            AppLogoView(size: iconSize)
            Text("IntelliStock")
                .font(.title3.bold())
                .foregroundStyle(.primary)
        }
        .accessibilityElement(children: .combine)
    }
}
