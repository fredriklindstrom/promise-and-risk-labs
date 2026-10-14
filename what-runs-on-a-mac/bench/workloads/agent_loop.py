"""Agent-loop cache test: a scripted coding-agent session (no live agent, so it is repeatable).

Turn 1: a ~33,000-token system prompt (tool descriptions + a source file) and a short instruction.
Turns 2..N: the previous reply stays in the history as returned; a fixed ~3,000-token "tool result" and a
short instruction are appended. Four conditions change what happens between turns:

  A  cache-friendly      append only
  B  broken at the top   the Request-ID on the first line of the system prompt is rewritten every turn
  C  broken mid-context  one marker line roughly halfway through the history is edited every turn
  D  thinking stripped   earlier assistant turns are resent without their reasoning

Inputs live in inputs/agent_loop/ (script.json is the frozen turn script for the February rerun).
Source text: CPython standard-library files (PSF licence, copied there with LICENSE.txt). The tool
descriptions in tools.md were written for this test.

    python -m bench.workloads.agent_loop --build      # (re)build inputs/agent_loop/ from the stdlib on this Mac
"""
import hashlib, json, random, re, shutil, sys
from pathlib import Path
from .. import ROOT

DIR = ROOT / "inputs/agent_loop"
SYSTEM_TOKENS = 33_000
TOOL_RESULT_TOKENS = 3_000
OUTPUT_TOKENS = 256
SEED = 17
CONDITIONS = {"A": "Cache-friendly", "B": "Broken at the top", "C": "Broken mid-context", "D": "Thinking stripped"}

# Turn-1 code files and the nine tool results (turns 2-10), all CPython standard library. The system prompt is
# cut at 33,000 tokens, so it ends somewhere inside typing.py.
SYSTEM_SOURCES = ["argparse.py", "typing.py"]
TOOL_RESULT_SOURCES = ["textwrap.py", "shlex.py", "json/decoder.py", "csv.py", "heapq.py",
                       "string.py", "fnmatch.py", "glob.py", "queue.py"]
INSTRUCTIONS = [
    "Read the repository context above. Summarise what the main module does and list the three functions you would test first.",
    "Here is the next file. Explain how it handles long input lines and note any edge cases worth a test.",
    "Next file. Which inputs could make this tokenizer behave unexpectedly? Suggest two tests.",
    "Next file. Describe the error handling and propose one small refactor.",
    "Next file. Point out anything that depends on the platform or locale.",
    "Next file. Which functions are pure, and which have side effects?",
    "Next file. Write the docstring you would add to the least documented function.",
    "Next file. How would you speed up the slowest path, if at all?",
    "Next file. List the public names and say which ones you would deprecate.",
    "Last file. Summarise the whole session in five bullet points.",
]
MARKER_EVERY_CHARS = 3_000      # one "# marker NN: rev 00" line per ~3,000 characters of the code section


def stdlib_dir():
    import sysconfig
    return Path(sysconfig.get_paths()["stdlib"])


def build():
    """Copy the source files + licence into inputs/agent_loop/ and write script.json. Deterministic."""
    src = stdlib_dir()
    (DIR / "src").mkdir(parents=True, exist_ok=True)
    files = SYSTEM_SOURCES + TOOL_RESULT_SOURCES
    sources = []
    for f in files:
        dst = DIR / "src" / f.replace("/", "__")
        shutil.copyfile(src / f, dst)
        sources.append({"file": f, "copied_as": dst.name, "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()})
    lic = src / "LICENSE.txt"
    shutil.copyfile(lic, DIR / "LICENSE-PSF.txt")
    tools = (DIR / "tools.md").read_text()
    code = "".join(f"# ---- Lib/{f} ----\n" + (DIR / "src" / f.replace("/", "__")).read_text() for f in SYSTEM_SOURCES)
    # Marker lines for condition C, spread through the code section at fixed character positions.
    marked, n = [], 0
    for i in range(0, len(code), MARKER_EVERY_CHARS):
        marked.append(f"# marker {n:02d}: rev 00\n")
        marked.append(code[i:i + MARKER_EVERY_CHARS])
        n += 1
    system = ("Request-ID: {request_id}\n\n" + tools + "\n\n## Repository context\n\n```python\n"
              + "".join(marked) + "\n```\n")
    # Each tool result starts with its own file and runs on into the next ones (cyclically) so every result has
    # well over 3,000 tokens before trimming; a few of these files are shorter than that on their own.
    results = []
    for k in range(len(TOOL_RESULT_SOURCES)):
        parts, size, j = [], 0, k
        while size < 20_000:
            f = TOOL_RESULT_SOURCES[j % len(TOOL_RESULT_SOURCES)]
            body = (DIR / "src" / f.replace("/", "__")).read_text()
            parts.append(f"$ cat Lib/{f}\n{body}")
            size += len(body); j += 1
        results.append("\n".join(parts))
    script = {
        "description": "Agent-loop cache test, frozen turn script (Nov 17 addendum). Trimmed to exact token counts per model at run time.",
        "python_stdlib_version": sys.version.split()[0], "licence": "PSF License (LICENSE-PSF.txt) for src/*; tools.md written for this test",
        "system_tokens": SYSTEM_TOKENS, "tool_result_tokens": TOOL_RESULT_TOKENS, "output_tokens": OUTPUT_TOKENS, "seed": SEED,
        "system_template": system, "markers": n, "tool_results": results, "instructions": INSTRUCTIONS, "sources": sources,
        "conditions": CONDITIONS,
    }
    (DIR / "script.json").write_text(json.dumps(script, indent=1, ensure_ascii=False))
    return script


def load_script():
    return json.loads((DIR / "script.json").read_text())


class Session:
    """Builds the messages for each turn of one condition run. `tok` = object with encode/decode (HFTok)."""

    def __init__(self, tok, condition, turns, reasoning_field="reasoning_content"):
        s = load_script()
        self.tok, self.condition, self.turns, self.rfield = tok, condition, turns, reasoning_field
        # Trim the system prompt to exactly SYSTEM_TOKENS with this model's tokenizer (Request-ID line at full width).
        template = s["system_template"]
        ids = tok.encode(template.replace("{request_id}", "00000000"))
        body = tok.decode(ids[:s["system_tokens"]])
        # Re-insert the placeholder so B can rewrite it; the trimmed text keeps the first line intact.
        assert body.startswith("Request-ID: 00000000"), "system prompt must start with the Request-ID line"
        self.system_tpl = "Request-ID: {request_id}" + body[len("Request-ID: 00000000"):]
        self.system_tokens = len(tok.encode(body))
        self.tool_results = [tok.decode(tok.encode(t)[:s["tool_result_tokens"]]) for t in s["tool_results"]]
        self.instructions = s["instructions"]
        self.markers = re.findall(r"# marker (\d\d): rev \d\d", self.system_tpl)
        self.marker_rev = {}             # marker number -> rev written into it (condition C)
        self.replies = []                # (content, reasoning) per finished turn
        self.request_id = "00000000"

    def record_reply(self, content, reasoning):
        self.replies.append((content or "", reasoning or ""))

    def _system(self):
        s = self.system_tpl.replace("{request_id}", self.request_id)
        for m, rev in self.marker_rev.items():
            s = s.replace(f"# marker {m}: rev 00", f"# marker {m}: rev {rev:02d}")
        return s

    def messages(self, turn):
        """Messages for `turn` (1-based), after applying this condition's change for that turn."""
        if self.condition == "B" and turn > 1:
            self.request_id = "%08x" % random.Random(f"B-{turn}").getrandbits(32)     # same values in both reps
        msgs = [{"role": "user", "content": self.instructions[0]}]
        for k in range(2, turn + 1):
            content, reasoning = self.replies[k - 2]
            a = {"role": "assistant", "content": content}
            keep = self.condition != "D"
            if reasoning and keep:
                a[self.rfield] = reasoning
            msgs.append(a)
            msgs.append({"role": "user", "content": f"Tool output:\n{self.tool_results[k - 2]}\n\n{self.instructions[k - 1]}"})
        if self.condition == "C" and turn > 1:
            # Edit the marker nearest the middle of the current history (by characters, which tracks tokens closely).
            total = len(self._system()) + sum(len(m["content"]) + len(m.get(self.rfield, "")) for m in msgs)
            mid = total // 2
            sys_text = self._system()
            pos = {m: sys_text.find(f"# marker {m}: rev") for m in self.markers}
            m = min(pos, key=lambda k: abs(pos[k] - mid))
            self.marker_rev[m] = turn
        return [{"role": "system", "content": self._system()}] + msgs


if __name__ == "__main__":
    if "--build" in sys.argv:
        s = build()
        print(f"built {DIR}/script.json: {len(s['tool_results'])} tool results, {s['markers']} markers, sources {[x['file'] for x in s['sources']]}")
