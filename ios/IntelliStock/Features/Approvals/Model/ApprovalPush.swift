import Foundation

/// The push the API sends when Jarvis asks to act (contract §5): category
/// "MCP_APPROVAL", title "Jarvis wants to act", body = the summary, data
/// `{"approval_id": id}`. `apns_sender` puts `category` and every data key at
/// the top level of the payload, beside `aps`.
nonisolated enum ApprovalPush {
    static let category = "MCP_APPROVAL"

    /// The approval a tapped push names: category "MCP_APPROVAL" (top level, or inside aps) and a valid string approval_id. Else nil.
    static func approvalId(from userInfo: [AnyHashable: Any]) -> String? {
        let pushCategory = (userInfo["category"] as? String)
            ?? ((userInfo["aps"] as? [String: Any])?["category"] as? String)
        guard pushCategory == category,
              let id = userInfo["approval_id"] as? String,
              ApprovalIds.isValid(id)
        else { return nil }
        return id
    }
}
