import Foundation

/// A user-facing API error, ported from `api_error.dart`.
///
/// FastAPI's `{detail}` may be a string, a list of `{msg|message}` validation
/// objects, or any other value; all three flatten into one message. Without a
/// detail, transport failures get the same friendly copy the Flutter app showed.
nonisolated struct ApiError: Error, LocalizedError, Equatable, Sendable {
    let message: String
    let statusCode: Int?

    init(message: String, statusCode: Int? = nil) {
        self.message = message
        self.statusCode = statusCode
    }

    var errorDescription: String? { message }

    static func from(status: Int?, body: Data?, transport: URLError?) -> ApiError {
        if let body, !body.isEmpty, let json = try? JSON(data: body) {
            let detail = json["detail"]
            switch detail {
            case .null:
                break
            case .string(let s):
                return ApiError(message: s, statusCode: status)
            case .array(let items):
                let joined = items.map { item -> String in
                    if item.object != nil {
                        return item["msg"].string ?? item["message"].string ?? item.dartDescription
                    }
                    return item.dartDescription
                }.joined(separator: "; ")
                return ApiError(message: joined, statusCode: status)
            default:
                return ApiError(message: detail.dartDescription, statusCode: status)
            }
        }
        return ApiError(message: fallbackMessage(status: status, transport: transport), statusCode: status)
    }

    private static func fallbackMessage(status: Int?, transport: URLError?) -> String {
        if let transport {
            switch transport.code {
            case .timedOut:
                return "Request timed out. Check your connection and try again."
            case .notConnectedToInternet, .cannotFindHost, .cannotConnectToHost, .networkConnectionLost,
                 .dnsLookupFailed, .internationalRoamingOff, .dataNotAllowed, .callIsActive,
                 .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate,
                 .serverCertificateNotYetValid, .serverCertificateHasUnknownRoot, .clientCertificateRejected,
                 .appTransportSecurityRequiresSecureConnection:
                return "Cannot reach the server. Check your connection."
            default:
                return "Something went wrong."
            }
        }
        // Dio's bad-response message is a paragraph about validateStatus; the
        // status code is the only part of it a person can act on.
        if let status { return "The server responded with status \(status)." }
        return "Something went wrong."
    }
}
