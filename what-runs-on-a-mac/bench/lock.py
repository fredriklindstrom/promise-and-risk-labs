"""Write manifest.lock.json: everything the February M5 Ultra rerun needs to reproduce these runs."""
import datetime, hashlib, json, subprocess
from pathlib import Path
import yaml
from . import ROOT, MANIFEST, load_manifest
from .runners.adapters import RUNNERS, MLXRunner
from .system import sysctl


def sh(cmd):
    """argv only — never a shell string (security review 2026-10-13)."""
    assert isinstance(cmd, list)
    p = subprocess.run(cmd, capture_output=True, text=True)
    return (p.stdout + p.stderr).strip()


def pkgs(python):
    code = ("import importlib.metadata as m, sys, json\nout={'python': sys.version.split()[0]}\n"
            "for p in ['mlx','mlx-metal','mlx-lm','mlx-audio','mlx-whisper','torch','transformers','huggingface_hub','safetensors','omlx','mtplx','numpy']:\n"
            "    try: out[p]=m.version(p)\n    except Exception: pass\nprint(json.dumps(out))")
    try:
        return json.loads(subprocess.run([python, "-I", "-c", code], capture_output=True, text=True).stdout)
    except Exception:
        return None


def main():
    m = load_manifest()
    sums = [json.loads(l) for l in (ROOT / "results/checksums.jsonl").read_text().splitlines()] if (ROOT / "results/checksums.jsonl").exists() else []
    files = {}
    for r in sums:                                   # last record per file wins (reruns re-verify)
        files[(r["repo"], r["file"])] = {k: r.get(k) for k in ("repo", "revision", "file", "size", "sha256", "verified")}
    corpus = (ROOT / "inputs/context_corpus.txt").read_bytes()
    lock = {
        "written": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "machine": {"model": sysctl("hw.model"), "chip": sysctl("machdep.cpu.brand_string"), "memsize": int(sysctl("hw.memsize")),
                    "macos": sh(["sw_vers", "-productVersion"]), "build": sh(["sw_vers", "-buildVersion"]),
                    "iogpu.wired_limit_mb": sysctl("iogpu.wired_limit_mb")},
        "runners": {
            "mlx": MLXRunner.version(None),
            "llama.cpp": RUNNERS["llamacpp"].version(None),
            "ollama": sh(["/Applications/Ollama.app/Contents/Resources/ollama", "--version"]).split()[-1],
            "omlx": sh([str(ROOT / ".venv-omlx/bin/omlx"), "--version"]),
            "mtplx": (pkgs(str(ROOT / ".venv-mtplx/bin/python")) or {}).get("mtplx"),
            "mlx-serve": " / ".join(l for l in sh([str(ROOT / "tools/mlx-serve-26.10.1/mlx-serve"), "--version"]).splitlines() if l.startswith(("mlx-serve", "mlx "))),
        },
        # Every Python environment the runs used: the project's own venvs, plus any listed in the manifest's python_envs.
        "python_envs": {name: pkgs(str(py if str(py).startswith("/") else ROOT / py)) for name, py in {
            **{n: d + "/bin/python" for n, d in (("harness", ".venv"), ("omlx", ".venv-omlx"), ("mtplx", ".venv-mtplx"), ("voice", ".venv-voice"))
               if (ROOT / d).exists()},
            **(m.get("python_envs") or {})}.items()},
        "ollama_models": sh(["/usr/bin/curl", "-s", "--max-time", "2", "http://127.0.0.1:11434/api/tags"]) or "server not running at lock time; see manifest.yaml qwen27b-ollama size_gb note",
        "settings": m["settings"], "variants": m["variants"], "models": m["models"], "media": m["media"], "disk_guard": m["disk_guard"],
        "workload": {"corpus_sha256": hashlib.sha256(corpus).hexdigest(), "corpus_source": (ROOT / "inputs/context_corpus.SOURCE.txt").read_text(),
                     "prompts_module_sha256": hashlib.sha256((ROOT / "bench/workloads/prompts.py").read_bytes()).hexdigest()},
        "files": sorted(files.values(), key=lambda r: (r["repo"], r["file"])),
    }
    (MANIFEST.with_suffix(".lock.json")).write_text(json.dumps(lock, indent=1, default=str))
    print(f"manifest.lock.json: {len(lock['files'])} files, {sum(1 for f in lock['files'] if f['verified'])} verified")


if __name__ == "__main__":
    main()
