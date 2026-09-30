"""Publication check for every staged file, called by tools/pre-commit.

  publication_check.py <deny-anywhere-file> <deny-whole-word-file>

Decides text or binary itself (a NUL byte, or not valid UTF-8, means binary), so neither git's heuristic
nor a .gitattributes entry can route a file around the check. Renames count as additions.
  text    no deny term anywhere, no deny word as a whole word (case-insensitive)
  binary  must be a PNG made only of the standard chunks below and nothing after IEND; any other
          binary type is refused until a check for it exists here
Exit 0 and print nothing when clean; otherwise print one line per problem and exit 1. Any error exits
non-zero, which the hook treats as blocked.
"""
import re
import struct
import subprocess
import sys

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"pHYs", b"bKGD"}


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def terms(path):
    with open(path, encoding="utf-8") as f:
        out = [t.strip().lower() for t in f.read().replace("\r", "").split("\n") if t.strip()]
    if not out:
        raise SystemExit(f"{path}: no usable terms")
    return out


def png_problems(path, data):
    if not data.startswith(PNG_SIG):
        return [f"{path}: binary type without a publication check"]
    i = len(PNG_SIG)
    while i + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[i:i + 8])
        if kind not in PNG_CHUNKS:
            return [f"{path}: PNG chunk {kind!r} isn't on the allowed list"]
        i += 12 + n
        if kind == b"IEND":
            return [] if i == len(data) else [f"{path}: bytes after the end of the PNG"]
    return [f"{path}: PNG without an IEND chunk"]


def main(deny_file, words_file):
    anywhere, words = terms(deny_file), terms(words_file)
    word_res = [re.compile(r"(?<![0-9a-z_])" + re.escape(w) + r"(?![0-9a-z_])") for w in words]
    names = git("diff", "--cached", "--no-renames", "--name-only", "-z", "--diff-filter=ACMRT").split(b"\0")
    bad = []
    for raw in filter(None, names):
        path = raw.decode("utf-8", "surrogateescape")
        data = git("show", ":0:" + path)  # stage 0 by name: a path like "1:x" is not a stage selector
        try:
            text = None if b"\0" in data else data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is None:
            bad += png_problems(path, data)
            continue
        low = text.lower()
        bad += [f"{path}: contains a denied term" for t in anywhere if t in low][:1]
        bad += [f"{path}: contains a denied word" for r in word_res if r.search(low)][:1]
    if bad:
        print("\n".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
