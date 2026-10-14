import Foundation

/// Turns the harness's raw failure reason (often a Python repr of a server error, escaped twice and cut at ~500
/// characters) into a short headline and one line of detail. Known failures get plain words; anything else falls
/// back to the server's own message, unwrapped and unescaped.
enum ReasonText {
    struct Explained: Equatable {
        var headline: String
        var detail: String?
    }

    /// `depth` > 0: already inside "server error in stream:" (unwrapped once, never again).
    static func explain(_ raw: String, depth: Int = 0) -> Explained {
        // Reasons come from files: cap the length first, so no input makes the regexes below slow (review M1).
        let raw = String(raw.prefix(2048))
        var r = raw.split(separator: "\n").first.map(String.init) ?? raw
        for prefix in ["did not fit: ", "did not fit:", "error: ", "skipped: "] where r.lowercased().hasPrefix(prefix) {
            r = String(r.dropFirst(prefix.count)); break
        }
        r = r.trimmingCharacters(in: .whitespaces)
        // RuntimeError('...') -> ...
        if let m = match(#"^[A-Za-z]+Error\(['"](.*?)['"]?\)?$"#, r) { r = m[0] }
        let http = match(#"HTTP (\d{3})"#, r)?.first
        var msg = r
        if let m = match(#""message"\s*:\s*"(.*?)(?:"\s*[,}]|$)"#, r) { msg = m[0] }
        msg = unescape(msg)

        if let m = match(#"\(([\d.]+)\s*GB\) does not fit under the dynamic memory ceiling \(([\d.]+)\s*GB\)"#, msg) {
            var d = "oMLX refused to load it" + (http.map { " (HTTP \($0))" } ?? "")
            if let s = match(#"static cap is ([\d.]+)\s*GB but only ([\d.]+)\s*GB is reclaimable"#, msg) {
                d += ". Its limit follows free memory: \(s[1]) GB reclaimable now, \(s[0]) GB static cap"
            }
            return .init(headline: "Doesn't fit: needs \(m[0]) GB, oMLX allows \(m[1]) GB", detail: d)
        }
        if let m = match(#"projected memory ([\d.]+)\s*GB would exceed the metal_cap memory ceiling ([\d.]+)\s*GB"#, msg) {
            return .init(headline: "Doesn't fit: needs \(m[0]) GB, GPU limit \(m[1]) GB",
                         detail: "Over the Metal cap set by iogpu.wired_limit_mb" + (http.map { " (HTTP \($0))" } ?? ""))
        }
        if msg.contains("Impacting Interactivity") {
            return .init(headline: "macOS stopped a long GPU job",
                         detail: "One GPU command ran too long while the display was in use (Metal: Impacting Interactivity)")
        }
        if msg.contains("[METAL]") && msg.contains("Insufficient Memory") {
            return .init(headline: "GPU ran out of memory", detail: "Metal: Insufficient Memory")
        }
        if let m = match(#"memory pressure critical with swap \+([\d.]+) GB"#, msg) {
            return .init(headline: "Stopped: memory pressure critical", detail: "Swap grew by \(m[0]) GB; the watchdog stopped the runner")
        }
        if msg.contains("memory pressure critical") {
            return .init(headline: "Stopped: memory pressure critical", detail: "The watchdog stopped the runner")
        }
        if let m = match(#"MLX worker exited \(code (-?\d+)\)"#, msg) {
            return .init(headline: "MLX worker crashed (exit \(m[0]))", detail: "The cause is in runner.log in the run folder")
        }
        if let m = match(#"empty response at turn (\d+)"#, msg) {
            return .init(headline: "Server returned nothing at turn \(m[0])",
                         detail: "Usually a server crash or out of memory; see server.log in the run folder")
        }
        if depth == 0, let m = match(#"server error in stream:\s*(.*)$"#, msg) {
            let inner = explain(m[0], depth: 1)
            return .init(headline: inner.headline, detail: inner.detail ?? "Error reported by the server mid-stream")
        }
        // Fallback: the server's own message, first sentence as the headline, the rest as detail.
        let clean = msg.trimmingCharacters(in: .whitespaces)
        let sentence = firstSentence(clean)
        let rest = clean.count > sentence.count ? String(clean.dropFirst(sentence.count)).trimmingCharacters(in: .whitespaces) : ""
        let head = (http != nil && !sentence.hasPrefix("HTTP") ? "Server refused (HTTP \(http!)): " : "") + sentence
        return .init(headline: capped(cap(head), 90), detail: rest.isEmpty ? nil : capped(rest, 140))
    }

    // MARK: helpers

    /// Python repr escaping, possibly nested: \' \\' \\\' -> ', \xNN byte runs -> UTF-8, \" -> ".
    static func unescape(_ s: String) -> String {
        var out = s.replacingOccurrences(of: #"\\+'"#, with: "'", options: .regularExpression)
        out = out.replacingOccurrences(of: #"\\+""#, with: "\"", options: .regularExpression)
        // \xe2\x86\x92 (any number of backslashes) -> decoded UTF-8
        if let rx = try? NSRegularExpression(pattern: #"(?:\\+x[0-9a-fA-F]{2})+"#) {
            let ns = out as NSString
            var result = ""
            var last = 0
            for m in rx.matches(in: out, range: NSRange(location: 0, length: ns.length)) {
                result += ns.substring(with: NSRange(location: last, length: m.range.location - last))
                let hex = ns.substring(with: m.range).components(separatedBy: "x").dropFirst()
                let bytes = hex.compactMap { UInt8(String($0.prefix(2)), radix: 16) }
                result += String(bytes: bytes, encoding: .utf8) ?? ""
                last = m.range.location + m.range.length
            }
            result += ns.substring(from: last)
            out = result
        }
        return out.replacingOccurrences(of: #"\\+n"#, with: " ", options: .regularExpression)
    }

    static func match(_ pattern: String, _ s: String) -> [String]? {
        guard let rx = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]),
              let m = rx.firstMatch(in: s, range: NSRange(location: 0, length: (s as NSString).length)) else { return nil }
        return (1..<max(1, m.numberOfRanges)).map { i in
            m.range(at: i).location == NSNotFound ? "" : (s as NSString).substring(with: m.range(at: i))
        }
    }

    private static func firstSentence(_ s: String) -> String {
        if let r = s.range(of: #"^.*?[.!?](?=\s|$)"#, options: .regularExpression) { return String(s[r]) }
        return s
    }

    private static func cap(_ s: String) -> String { s.prefix(1).uppercased() + s.dropFirst() }

    private static func capped(_ s: String, _ n: Int) -> String { s.count > n ? String(s.prefix(n - 1)) + "…" : s }
}
