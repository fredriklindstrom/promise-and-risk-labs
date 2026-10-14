"""One adapter per runner. Each returns the same result dict so rows are comparable:
ttft_s, prefill_tps, decode_tps, prompt_tokens, output_tokens, peak_mem_gb, peak_mem_source,
timing_source, text, finish_reason, raw (runner-native timing fields)."""
import http.client, json, os, re, signal, socket, subprocess, sys, time, urllib.request
from pathlib import Path
from .. import ROOT
from ..workloads.prompts import HFTok


HUB = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub")


def snapshot(repo, revision=None):
    """Local snapshot dir in the HF cache (offline). Pinned revision may be a short hash prefix."""
    base = HUB / ("models--" + repo.replace("/", "--"))
    snaps = base / "snapshots"
    if revision:
        hits = [d for d in snaps.glob(revision + "*") if d.is_dir()]
    else:
        ref = (base / "refs/main").read_text().strip() if (base / "refs/main").exists() else None
        hits = [snaps / ref] if ref and (snaps / ref).is_dir() else sorted(snaps.iterdir(), key=lambda d: d.stat().st_mtime)[-1:]
    if not hits or not (hits[0] / "config.json").exists() and not any(hits[0].glob("*.gguf")):
        raise FileNotFoundError(f"{repo}@{revision or 'main'} not in the HF cache (download first)")
    return hits[0]


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_http(url, timeout, proc=None, ok=lambda r: r.status == 200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"server exited with code {proc.returncode} during load")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if ok(r):
                    return time.time() - t0
        except Exception:
            pass
        time.sleep(0.25)
    raise TimeoutError(f"{url} not ready after {timeout}s")


def post_json(port, path, payload, timeout=60):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
    r = c.getresponse()
    body = r.read()
    return json.loads(body) if body else {}


def stream(port, path, payload, timeout=7200):
    """Yields (t_perf, obj) for SSE ('data: ...') or NDJSON streams."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
    r = c.getresponse()
    if r.status != 200:
        raise RuntimeError(f"HTTP {r.status}: {r.read()[:500]!r}")
    for raw in r:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
            if line == "[DONE]":
                break
        try:
            yield time.perf_counter(), json.loads(line)
        except json.JSONDecodeError:
            continue


class Runner:
    name = "?"
    def __init__(self, variant, model_cfg, run_dir_fn):
        self.v, self.m, self.run_dir_fn = variant, model_cfg, run_dir_fn
        self.proc = None
        self.template_kwargs = model_cfg.get("template_kwargs") or {}
    def kill(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except Exception:
                self.proc.kill()
    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(20)
            except Exception:
                self.kill()
    def _spawn(self, cmd, env=None, log_name="runner.log"):
        self.log_path = self.run_dir_fn() / log_name
        self.cmd = cmd
        # HF_HUB_OFFLINE: a runner must never fetch anything mid-benchmark (security review 2026-10-13).
        self.proc = subprocess.Popen(cmd, stdout=open(self.log_path, "ab"), stderr=subprocess.STDOUT, cwd=ROOT,
                                     env={**os.environ, "HF_HUB_OFFLINE": "1", **(env or {})}, start_new_session=True)
        return self.proc


# ------------------------------------------------------------------ MLX (mlx-lm Python API)
class MLXRunner(Runner):
    name = "MLX"
    def version(self):
        import importlib.metadata as m
        return f"mlx-lm {m.version('mlx-lm')} / mlx {m.version('mlx')}"
    def start(self, ctx):
        self.path = snapshot(self.v["repo"], self.v.get("revision"))
        self.tok = HFTok(self.path)
        self.cmd = [str(ROOT / ".venv/bin/python"), "-m", "bench.runners.mlx_worker", "--model", str(self.path)]
        self.log_path = self.run_dir_fn() / "runner.log"
        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(self.log_path, "ab"),
                                     cwd=ROOT, text=True, bufsize=1, start_new_session=True,
                                     env={**os.environ, "HF_HUB_OFFLINE": "1"})
        msg = self._read()
        return {"load_s": msg["load_s"], "cmd": " ".join(self.cmd), "pid": self.proc.pid}
    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MLX worker exited (code {self.proc.poll()}); see runner.log")
        return json.loads(line)
    def generate(self, messages, max_tokens, on_progress):
        self.proc.stdin.write(json.dumps({"messages": messages, "max_tokens": max_tokens, "template_kwargs": self.template_kwargs}) + "\n")
        self.proc.stdin.flush()
        first = None
        while True:
            m = self._read()
            if m["event"] == "first":
                first = m
                on_progress(phase="decode", tokens=1, ttft_s=m["ttft_s"], prefill_tps=round(m["prompt_tokens"] / m["ttft_s"], 2))
            elif m["event"] == "tick":
                dt = m["t"] - first["ttft_s"]
                on_progress(tokens=m["tokens"], decode_tps=round((m["tokens"] - 1) / dt, 2) if dt > 0 else None)
            elif m["event"] == "done":
                text = m.pop("text")
                return {**{k: m[k] for k in ("ttft_s", "prefill_tps", "decode_tps", "prompt_tokens", "output_tokens", "peak_mem_gb", "finish_reason")},
                        "peak_mem_source": "mx.get_peak_memory()", "timing_source": "client-side perf_counter around mlx_lm.stream_generate",
                        "text": text, "raw": m}


# ------------------------------------------------------------------ llama.cpp (llama-server)
def llama_server():
    """llama-server binary from the manifest (Homebrew on the Studio, the pinned release tarball on the Air)."""
    from .. import load_manifest
    import shutil
    b = load_manifest()["runners"]["llamacpp"].get("binary", "llama-server")
    if "/" not in b:                                   # a bare name: look it up on PATH
        found = shutil.which(b)
        if not found:
            raise RuntimeError(f"{b} not found on PATH (install llama.cpp, or set runners.llamacpp.binary)")
        return found
    return str(b if b.startswith("/") else ROOT / b)


class LlamaCppRunner(Runner):
    name = "llama.cpp"
    def version(self):
        out = subprocess.run([llama_server(), "--version"], capture_output=True, text=True)
        m = re.search(r"version: (\S+) \(build (\d+), commit (\w+)\)", out.stdout + out.stderr)
        return f"llama.cpp {m.group(1)} b{m.group(2)} ({m.group(3)})" if m else "llama.cpp ?"
    def start(self, ctx):
        snap = snapshot(self.v["repo"], self.v.get("revision"))
        self.path = snap / self.v["file"]
        self.tok_path = None
        self.port = free_port()
        cmd = [llama_server(), "-m", str(self.path), "--ctx-size", str(ctx), "--seed", "17",
               "-ngl", "999", "--parallel", "1", "--host", "127.0.0.1", "--port", str(self.port), "--jinja",
               "--cache-reuse", "0", "--no-webui"]
        self._spawn(cmd)
        load_s = wait_http(f"http://127.0.0.1:{self.port}/health", 1800, self.proc)
        self.tok = _LlamaTok(self.port)
        return {"load_s": round(load_s, 3), "cmd": " ".join(cmd), "pid": self.proc.pid}
    def _buffers_gb(self):
        txt = Path(self.log_path).read_text(errors="replace")
        tot = 0.0
        for kind in ("model buffer size", "KV buffer size", "compute buffer size", "RS buffer size"):
            for m in re.finditer(rf"(?:MTL\d*|Metal\w*)\s*{kind}\s*=\s*([\d.]+) MiB", txt):
                tot += float(m.group(1))
        return round(tot * 1.048576 / 1000, 3) if tot else None
    def generate(self, messages, max_tokens, on_progress):
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": 17, "stream": True,
                "cache_prompt": False, "chat_template_kwargs": self.template_kwargs, "stream_options": {"include_usage": True}}
        t0 = time.perf_counter(); t_first = None; n = 0; text = []; timings = None; finish = None
        for t, obj in stream(self.port, "/v1/chat/completions", body):
            if obj.get("timings"):
                timings = obj["timings"]
            for ch in obj.get("choices", []):
                d = ch.get("delta", {})
                piece = (d.get("content") or "") + (d.get("reasoning_content") or "")
                if piece:
                    if t_first is None:
                        t_first = t
                        on_progress(phase="decode", tokens=1, ttft_s=round(t - t0, 3))
                    n += 1; text.append(piece)
                    if n % 16 == 0:
                        on_progress(tokens=n, decode_tps=round((n - 1) / (t - t_first), 2))
                finish = ch.get("finish_reason") or finish
        tm = timings or {}
        return {"ttft_s": round(tm["prompt_ms"] / 1000, 4) if tm.get("prompt_ms") else round(t_first - t0, 4),
                "prefill_tps": round(tm.get("prompt_per_second", 0), 2) or None,
                "decode_tps": round(tm.get("predicted_per_second", 0), 3) or None,
                "prompt_tokens": tm.get("prompt_n"), "output_tokens": tm.get("predicted_n", n),
                "peak_mem_gb": self._buffers_gb(), "peak_mem_source": "llama-server Metal buffers (model+KV+compute) from log",
                "timing_source": "llama-server timings block (prompt_ms, prompt_per_second, predicted_per_second)",
                "client_ttft_s": round(t_first - t0, 4) if t_first else None,
                "text": "".join(text), "finish_reason": finish, "raw": tm}


class _LlamaTok:
    def __init__(self, port):
        self.port, self.source = port, "llama-server /tokenize + /apply-template (GGUF-embedded template)"
    def encode(self, s):
        return post_json(self.port, "/tokenize", {"content": s, "add_special": False})["tokens"]
    def decode(self, ids):
        return post_json(self.port, "/detokenize", {"tokens": ids})["content"]
    def prompt_len(self, messages, template_kwargs=None):
        p = post_json(self.port, "/apply-template", {"messages": messages, "chat_template_kwargs": template_kwargs or {}})["prompt"]
        return len(post_json(self.port, "/tokenize", {"content": p, "add_special": True, "parse_special": True})["tokens"])


# ------------------------------------------------------------------ Ollama
OLLAMA = "/Applications/Ollama.app/Contents/Resources/ollama"

class OllamaRunner(Runner):
    name = "Ollama"
    port = 11434
    def version(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version", timeout=3) as r:
                return "ollama " + json.load(r)["version"]
        except Exception:
            return subprocess.run([OLLAMA, "--version"], capture_output=True, text=True).stdout.strip().split()[-1]
    def _ensure_server(self):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version", timeout=2); self.own = False
        except Exception:
            self._spawn([OLLAMA, "serve"], env={"OLLAMA_HOST": f"127.0.0.1:{self.port}", "OLLAMA_KEEP_ALIVE": "30m"}, log_name="ollama-serve.log")
            wait_http(f"http://127.0.0.1:{self.port}/api/version", 60, self.proc); self.own = True
    def start(self, ctx):
        self._ensure_server()
        self.ctx, self.tag = ctx, self.v["ollama_tag"]
        want = self.v.get("ollama_digest")         # tags are mutable: the pulled digest must be the one in the manifest
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/tags", timeout=10) as r:
            have = {m["name"]: m["digest"] for m in json.load(r).get("models", [])}
        got = have.get(self.tag) or have.get(self.tag + ":latest")
        if got is None:
            raise RuntimeError(f"{self.tag} is not pulled")
        if want and not got.startswith(want) and os.environ.get("BENCH_ALLOW_UNVERIFIED") != "1":
            raise RuntimeError(f"{self.tag}: pulled digest {got[:12]} != manifest ollama_digest {want}")
        self.digest = got
        tok_src = self.v.get("tokenizer_repo")
        if not tok_src:
            raise RuntimeError("Ollama variants need tokenizer_repo (+ tokenizer_revision): Ollama exposes no tokenizer")
        self.tok = HFTok(snapshot(tok_src, self.v.get("tokenizer_revision")))
        self.tok.source = f"HF tokenizer from {tok_src} (same tokenizer family; Ollama exposes none)"
        t0 = time.time()
        r = post_json(self.port, "/api/generate", {"model": self.tag, "prompt": "", "keep_alive": "30m", "options": {"num_ctx": ctx}}, timeout=1800)
        wall = time.time() - t0
        return {"load_s": round(wall, 3), "cmd": f"POST /api/generate model={self.tag} num_ctx={ctx}", "pid": self.proc.pid if self.proc else None,
                "load_note": f"Ollama load = wall time of the empty load request (its load_duration field reported {r.get('load_duration', 0) / 1e9:.3f}s)"}
    def generate(self, messages, max_tokens, on_progress):
        think = self.template_kwargs.get("reasoning_effort") or (False if self.template_kwargs.get("enable_thinking") is False else None)
        body = {"model": self.tag, "messages": messages, "stream": True, "keep_alive": "30m",
                "options": {"num_ctx": self.ctx, "seed": 17, "temperature": 0, "num_predict": max_tokens}}
        if think is not None:
            body["think"] = think
        t0 = time.perf_counter(); t_first = None; n = 0; text = []; final = {}
        for t, obj in stream(self.port, "/api/chat", body):
            msg = obj.get("message", {})
            piece = (msg.get("content") or "") + (msg.get("thinking") or "")
            if piece:
                if t_first is None:
                    t_first = t; on_progress(phase="decode", tokens=1, ttft_s=round(t - t0, 3))
                n += 1; text.append(piece)
                if n % 16 == 0:
                    on_progress(tokens=n, decode_tps=round((n - 1) / (t - t_first), 2))
            if obj.get("done"):
                final = obj
        ps = {}
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/ps", timeout=5) as r:
                ps = next((m for m in json.load(r).get("models", []) if m.get("name", "").startswith(self.tag.split(":")[0])), {})
        except Exception:
            pass
        pe, pd, ec, ed = final.get("prompt_eval_count"), final.get("prompt_eval_duration"), final.get("eval_count"), final.get("eval_duration")
        return {"ttft_s": round(pd / 1e9, 4) if pd else None, "prefill_tps": round(pe / (pd / 1e9), 2) if pe and pd else None,
                "decode_tps": round(ec / (ed / 1e9), 3) if ec and ed else None, "prompt_tokens": pe, "output_tokens": ec,
                "peak_mem_gb": round(ps.get("size_vram", 0) / 1e9, 3) or None, "peak_mem_source": "ollama /api/ps size_vram",
                "timing_source": "ollama /api/chat prompt_eval_count/duration, eval_count/duration",
                "client_ttft_s": round(t_first - t0, 4) if t_first else None,
                "text": "".join(text), "finish_reason": final.get("done_reason"),
                "raw": {k: final.get(k) for k in ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")} | {"ps": ps}}
    def kill(self):
        """Watchdog path: unload the model even when Ollama.app's own server is running (then self.proc is None)."""
        try:
            post_json(self.port, "/api/generate", {"model": self.v["ollama_tag"], "keep_alive": 0}, timeout=10)
        except Exception:
            pass
        super().kill()

    def stop(self):
        try:
            post_json(self.port, "/api/generate", {"model": self.v["ollama_tag"], "keep_alive": 0}, timeout=60)
        except Exception:
            pass
        if getattr(self, "own", False):
            super().stop()


# ------------------------------------------------------------------ OpenAI-compatible MLX servers (oMLX, MTPLX)
class _OpenAIServer(Runner):
    def _generate_openai(self, model_id, messages, max_tokens, on_progress, extra):
        body = {"model": model_id, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": 17, "stream": True,
                "stream_options": {"include_usage": True}, "chat_template_kwargs": self.template_kwargs, **extra}
        if "reasoning_effort" in self.template_kwargs:
            body["reasoning_effort"] = self.template_kwargs["reasoning_effort"]
        t0 = time.perf_counter(); t_first = None; t_last = None; n = 0; text = []; usage = {}; finish = None; extra_t = {}
        for t, obj in stream(self.port, "/v1/chat/completions", body):
            if obj.get("error"):   # 2026-10-22: MTPLX reports refusals here; without this they became empty fit=yes rows
                err = obj["error"]
                raise RuntimeError(f"server error in stream: {(err.get('message') if isinstance(err, dict) else err)!s:.400}")
            if obj.get("usage"):
                usage = obj["usage"]
            for k in ("timings", "metrics", "x_timings"):
                if obj.get(k):
                    extra_t[k] = obj[k]
            for ch in obj.get("choices", []):
                d = ch.get("delta", {})
                piece = (d.get("content") or "") + (d.get("reasoning_content") or d.get("reasoning") or "")
                if piece:
                    if t_first is None:
                        t_first = t; on_progress(phase="decode", tokens=1, ttft_s=round(t - t0, 3))
                    n += 1; t_last = t; text.append(piece)
                    if n % 16 == 0:
                        on_progress(tokens=n, decode_tps=round((n - 1) / (t - t_first), 2))
                finish = ch.get("finish_reason") or finish
        out_tok = usage.get("completion_tokens") or n
        pt = usage.get("prompt_tokens")
        ttft = t_first - t0 if t_first else None
        c_decode = round((out_tok - 1) / (t_last - t_first), 3) if t_first and t_last and t_last > t_first else None
        c_prefill = round(pt / ttft, 2) if pt and ttft else None
        tm = extra_t.get("timings") or {}
        if tm.get("prompt_per_second") and tm.get("predicted_per_second"):
            res_t = {"ttft_s": round(tm["prompt_ms"] / 1000, 4), "prefill_tps": round(tm["prompt_per_second"], 2),
                     "decode_tps": round(tm["predicted_per_second"], 3),
                     "timing_source": "server timings block (prompt_ms, prompt_per_second, predicted_per_second)"}
        else:
            res_t = {"ttft_s": round(ttft, 4) if ttft else None, "prefill_tps": c_prefill, "decode_tps": c_decode,
                     "timing_source": "client-side streaming timestamps; token counts from usage block (stream chunks may hold >1 token)"}
        return {**res_t, "client_ttft_s": round(ttft, 4) if ttft else None, "client_prefill_tps": c_prefill, "client_decode_tps": c_decode,
                "draft_accept": f"{tm['draft_n_accepted']}/{tm['draft_n']}" if tm.get("draft_n") else None,
                "prompt_tokens": pt, "output_tokens": out_tok, "peak_mem_gb": None, "peak_mem_source": "system (sampler delta)",
                "text": "".join(text), "finish_reason": finish, "raw": {"usage": usage, **extra_t, "stream_chunks": n}}


class OMLXRunner(_OpenAIServer):
    name = "oMLX"
    def version(self):
        import importlib.metadata as m
        out = subprocess.run([str(ROOT / ".venv-omlx/bin/omlx"), "--version"], capture_output=True, text=True).stdout.strip()
        return ("omlx " + out.split()[-1]) if out else "omlx ?"
    def start(self, ctx):
        snap = snapshot(self.v["repo"], self.v.get("revision"))
        mdir = ROOT / "runs/_omlx_models"; mdir.mkdir(parents=True, exist_ok=True)
        link = mdir / self.v["id"]
        if not link.exists():
            link.symlink_to(snap)
        self.model_id = self.v["id"]
        self.tok = HFTok(snap)
        self.port = free_port()
        cmd = [str(ROOT / ".venv-omlx/bin/omlx"), "serve", "--model-dir", str(mdir), "--host", "127.0.0.1", "--port", str(self.port),
               "--max-concurrent-requests", "1", "--no-cache", "--no-hf-cache", "--log-level", "info"] + list(self.v.get("serve_args", []))
        self._spawn(cmd, env=self.v.get("env"))
        wait_http(f"http://127.0.0.1:{self.port}/v1/models", 300, self.proc)
        t0 = time.time()     # oMLX loads lazily: time a 1-token request as the load
        self._generate_openai(self.model_id, [{"role": "user", "content": "Hi"}], 1, lambda **k: None, {})
        return {"load_s": round(time.time() - t0, 3), "cmd": " ".join(cmd) + f"  env={self.v.get('env')}", "pid": self.proc.pid,
                "load_note": "oMLX loads on first request; load_s = wall time of a 1-token request"}
    def generate(self, messages, max_tokens, on_progress):
        return self._generate_openai(self.model_id, messages, max_tokens, on_progress, {})


class MTPLXRunner(_OpenAIServer):
    name = "MTPLX"
    def version(self):
        import importlib.metadata as m
        return "mtplx " + subprocess.run([str(ROOT / ".venv-mtplx/bin/python"), "-I", "-c", "import importlib.metadata as m;print(m.version('mtplx'))"],
                                         capture_output=True, text=True).stdout.strip()
    def start(self, ctx):
        snap = snapshot(self.v["repo"], self.v.get("revision"))
        self.tok = HFTok(snap)
        self.port = free_port()
        cmd = [str(ROOT / ".venv-mtplx/bin/mtplx"), "serve", "--model", str(snap), "--host", "127.0.0.1", "--port", str(self.port),
               # +1024 (2026-10-21): at exactly ctx, MTPLX logged "max_tokens clamped to remaining context" and returned
               # nothing at 32K (runs/s1-clean-qwen27b-mtplx-32k-r1-warmup/runner.log). Room for the 512 output tokens.
               "--no-auth", "--yes", "--context-window", str(ctx + 1024), "--ssd-session-cache", "off",
               # "stable" refuses prompts past ~32K ("Blocked unsafe long-context MTP prefill path ... Start MTPLX with
               # --profile sustained", runs/mtplx-32k-debug/stream_head.txt, 2026-10-22), so 32K and up run "sustained".
               "--scheduler-mode", "serial", "--batching-preset", "solo", "--profile", "sustained" if ctx >= 32768 else "stable"]
        self._spawn(cmd)
        load_s = wait_http(f"http://127.0.0.1:{self.port}/v1/models", 1800, self.proc)
        self.model_id = None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1/models", timeout=5) as r:
                self.model_id = json.load(r)["data"][0]["id"]
        except Exception:
            pass
        return {"load_s": round(load_s, 3), "cmd": " ".join(cmd), "pid": self.proc.pid}
    def generate(self, messages, max_tokens, on_progress):
        return self._generate_openai(self.model_id or "default", messages, max_tokens, on_progress, {})


class MLXServeRunner(_OpenAIServer):
    """mlx-serve 26.10.1. Safetensors run on its MLX engine, ds4 GGUFs on its embedded ds4 engine."""
    name = "mlx-serve"
    bin = ROOT / "tools/mlx-serve-26.10.1/mlx-serve"
    def version(self):
        return "mlx-serve 26.10.1"
    def start(self, ctx):
        snap = snapshot(self.v["repo"], self.v.get("revision"))
        # A GGUF snapshot has no tokenizer files: tok_from names a safetensors pack of the same model.
        self.tok = HFTok(snapshot(*self.v["tok_from"]) if self.v.get("tok_from") else snap)
        self.port = free_port()
        model = snap / self.v["file"] if self.v.get("file") else snap
        cmd = [str(self.bin), "--model", str(model), "--serve", "--host", "127.0.0.1", "--port", str(self.port),
               "--ctx-size", str(ctx + 1024), "--no-vision", "--timeout", "0"] + list(self.v.get("serve_args", []))
        self._spawn(cmd)
        load_s = wait_http(f"http://127.0.0.1:{self.port}/v1/models", 1800, self.proc)
        self.model_id = "default"
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/v1/models", timeout=5) as r:
                self.model_id = json.load(r)["data"][0]["id"]
        except Exception:
            pass
        t0 = time.time()     # time a 1-token request too: the server may load lazily
        self._generate_openai(self.model_id, [{"role": "user", "content": "Hi"}], 1, lambda **k: None, {})
        return {"load_s": round(load_s + time.time() - t0, 3), "cmd": " ".join(cmd), "pid": self.proc.pid,
                "load_note": "server ready + 1-token request"}
    def generate(self, messages, max_tokens, on_progress):
        return self._generate_openai(self.model_id, messages, max_tokens, on_progress, {})


RUNNERS = {"mlx": MLXRunner, "llamacpp": LlamaCppRunner, "ollama": OllamaRunner, "omlx": OMLXRunner, "mtplx": MTPLXRunner, "mlxserve": MLXServeRunner}
