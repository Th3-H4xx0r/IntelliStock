import Foundation

/// Dart `Uri.encodeComponent`: letters, digits and `-_.!~*'()` pass through;
/// everything else (a space too) is percent-encoded UTF-8, uppercase hex.
/// The single implementation: path builders (`Route.path`, repositories)
/// all call this.
nonisolated func dartEncodeComponent(_ s: String) -> String {
    var out = ""
    for byte in s.utf8 {
        switch byte {
        case UInt8(ascii: "a")...UInt8(ascii: "z"), UInt8(ascii: "A")...UInt8(ascii: "Z"),
             UInt8(ascii: "0")...UInt8(ascii: "9"),
             UInt8(ascii: "-"), UInt8(ascii: "_"), UInt8(ascii: "."), UInt8(ascii: "!"),
             UInt8(ascii: "~"), UInt8(ascii: "*"), UInt8(ascii: "'"), UInt8(ascii: "("), UInt8(ascii: ")"):
            out.unicodeScalars.append(Unicode.Scalar(byte))
        default:
            out += String(format: "%%%02X", byte)
        }
    }
    return out
}
