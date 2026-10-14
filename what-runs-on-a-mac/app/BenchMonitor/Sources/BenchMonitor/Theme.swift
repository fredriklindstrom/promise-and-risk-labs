import AppKit
import SwiftUI

/// Promise & Risk channel palette + type. Nothing on screen is smaller than 14 pt.
enum Theme {
    static let navy = Color(hex: 0x0B1628)
    static let panel = Color(hex: 0x111F36)
    static let panelEdge = Color(hex: 0x1E3354)
    static let cyan = Color(hex: 0x06B6D4)        // speed
    static let amber = Color(hex: 0xF59E0B)       // memory and warnings
    static let red = Color(hex: 0xEF4444)
    static let green = Color(hex: 0x10B981)
    static let text = Color(hex: 0xF8FAFC)
    static let dim = Color(hex: 0x9FB0C8)         // ~7:1 on navy
    static let faint = Color(hex: 0x5F7393)

    static let minSize: CGFloat = 14

    /// Outfit if installed, else DM Sans, else the system font.
    static let family: String? = {
        let fm = NSFontManager.shared
        for fam in ["Outfit", "DM Sans"] where !(fm.availableMembers(ofFontFamily: fam) ?? []).isEmpty {
            return fam
        }
        return nil
    }()

    static func font(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        let s = max(size, minSize)
        guard let fam = family else { return .system(size: s, weight: weight) }
        return .custom(fam, size: s).weight(weight)
    }

    /// Numerals: tabular (monospaced) digits so live values don't jitter. Outfit supports tnum.
    static func numerals(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        font(size, weight).monospacedDigit()
    }

    /// nominal = normal colour, fair = amber, serious/critical = red (the Mac is slowing itself down to cool).
    static func thermalColor(_ t: String?) -> Color {
        switch t ?? "" {
        case "fair", "moderate": return amber
        case "serious", "heavy", "critical", "trapping": return red
        default: return text
        }
    }

    static func pressureColor(_ p: String) -> Color {
        switch p {
        case "critical": return red
        case "warn": return amber
        default: return green
        }
    }
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: opacity)
    }
}

/// Rounded panel used by every section.
struct Panel<Content: View>: View {
    var padding: CGFloat = 20
    @ViewBuilder var content: Content
    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(RoundedRectangle(cornerRadius: 14).fill(Theme.panel))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Theme.panelEdge, lineWidth: 1))
    }
}

struct SectionLabel: View {
    var text: String
    var color: Color = Theme.dim
    init(_ text: String, color: Color = Theme.dim) { self.text = text; self.color = color }
    var body: some View {
        Text(text.uppercased())
            .font(Theme.font(15, .semibold))
            .tracking(1.6)
            .foregroundStyle(color)
    }
}

struct Chip: View {
    var text: String
    var color: Color = Theme.text
    var body: some View {
        Text(text)
            .font(Theme.font(20, .medium))
            .foregroundStyle(color)
            .padding(.horizontal, 12).padding(.vertical, 5)
            .background(Capsule().fill(Color.white.opacity(0.06)))
            .overlay(Capsule().strokeBorder(Color.white.opacity(0.12), lineWidth: 1))
    }
}
