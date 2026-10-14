"""Agent-loop cache test (Nov 17 addendum): turn-by-turn time to first token with prefix caching ON.

    python -m bench.cache_test [--variants id,..] [--conditions A,B] [--reps 2] [--dry-run] [--ollama-default-ctx]

One run = one variant (model + runner) x one condition x one repetition, all turns, on a freshly started
server (restarted between runs, so no cache leaks across conditions). Rows go to results/cache_test.csv only
when a run has finished every turn, so a restart re-runs unfinished runs from turn 1 (resumable by run_id).
Raw server logs, events and samples are kept in runs/<run_id>/.

Differences from the Set 1 adapters, on purpose: caching is ON at each server's defaults, the server is
driven through its HTTP API (agents talk to servers), and an empty response is an error, not a result.
"""
import argparse, csv, json, os, re, shutil, subprocess, sys, time, traceback
from pathlib import Path
from . import ROOT, load_manifest, refuse_root, exit_on_signals, run_hook
from .events import EventWriter, RUNS, acquire_bench_lock, now
from .verify import check_variant
from .runners.adapters import (Runner, snapshot, free_port, wait_http, stream, post_json, llama_server, OLLAMA)
from .system import Watchdog, gpu_mem_in_use_gb, window_summary, gpu_budget_gb
from .workloads.prompts import HFTok
from .workloads.agent_loop import Session, CONDITIONS, OUTPUT_TOKENS, SEED, load_script

CSV_PATH = ROOT / "results/cache_test.csv"
DRY_CSV = ROOT / "results/cache_test.dry.csv"
NOTES = ROOT / "results/cache_test_notes.md"
HISTORY = RUNS / "history.cache.jsonl"
SAMPLER_BIN = ROOT / "sampler/.build/release/bench-sampler"
PAUSE_FLAG = RUNS / ".cache_pause"          # scripts/cache_pause.sh creates it; the batch stops before the next run
STATUS = RUNS / ".cache_status.json"        # written before each run, read by scripts/cache_status.sh
# Minutes per run when no finished run of that variant+condition exists yet (plan estimates, worst case for C/D).
EST_MIN = {("qwen", "A"): 12, ("qwen", "B"): 70, ("qwen", "C"): 70, ("qwen", "D"): 70, ("llama", "A"): 22, ("llama", "B"): 85, ("llama", "C"): 105}
COLUMNS = ["run_id", "model", "quant", "runner", "runner_version", "condition", "turn", "context_tokens", "new_tokens",
           "cached_tokens", "cached_source", "ttft_s", "prefill_tps_uncached", "decode_tps", "peak_mem_gb", "mem_pressure_max",
           "reprocess_lines", "notes",
           # extras after the spec columns
           "rep", "turns_total", "started_at", "output_tokens", "reasoning_chars", "content_chars", "finish_reason",
           "server_ttft_s", "server_prompt_tps", "restored_checkpoint_lines", "swap_used_gb_max", "package_power_w_avg",
           "energy_wh", "variant_id", "repo", "revision", "server_cmd"]

QWEN_TOK = ("lmstudio-community/Qwen3.8-27B-MLX-4bit", "6067b15cf581666a4aecf6af3afaba4bb5efc20c")
LLAMA_TOK = ("mlx-community/Llama-3.3-70B-Instruct-4bit", "de2dfaf56839b7d0e834157d2401dee02726874d")
MODELS = {
    "qwen": {"name": "Qwen 3.8 27B", "tok": QWEN_TOK, "thinking": True, "turns": 10, "conditions": "ABCD", "ctx": 81920},
    # Condition C added 2026-10-21 (batch 1 follow-up, item 1); D does not apply (no reasoning output).
    "llama": {"name": "Llama 3.3 70B Instruct", "tok": LLAMA_TOK, "thinking": False, "turns": 6, "conditions": "ABC", "ctx": 65536},
}
# Run order = the approved plan: Qwen on every runner, then Llama on MLX and llama.cpp.
VARIANTS = [
    {"id": "qwen27b-mlx4", "model": "qwen", "runner": "mlxserver", "quant": "MLX 4-bit",
     "repo": QWEN_TOK[0], "revision": QWEN_TOK[1]},
    {"id": "qwen27b-q4km", "model": "qwen", "runner": "llamaserver", "quant": "GGUF Q4_K_M",
     "repo": "lmstudio-community/Qwen3.8-27B-GGUF", "revision": "5a7da681f60570ab5b439a587e912d2e5eddb582", "file": "Qwen3.8-27B-Q4_K_M.gguf"},
    {"id": "qwen27b-ollama", "model": "qwen", "runner": "ollama", "quant": "NVFP4 (Ollama default)",
     "ollama_tag": "qwen3.8:27b", "ollama_digest": "47951e4a6661"},
    {"id": "qwen27b-omlx", "model": "qwen", "runner": "omlx", "quant": "MLX 4-bit",
     "repo": QWEN_TOK[0], "revision": QWEN_TOK[1]},
    {"id": "llama70b-mlx4", "model": "llama", "runner": "mlxserver", "quant": "MLX 4-bit",
     "repo": LLAMA_TOK[0], "revision": LLAMA_TOK[1]},
    {"id": "llama70b-q4km", "model": "llama", "runner": "llamaserver", "quant": "GGUF Q4_K_M",
     "repo": "lmstudio-community/Llama-3.3-70B-Instruct-GGUF", "revision": "3a489fd247ce24848d8d8a3fadd707088665681f", "file": "Llama-3.3-70B-Instruct-Q4_K_M.gguf"},
    # Added 2026-10-20: llama70b-mlx4 on the default prompt cache ran out of GPU memory at turn 4-5 in
    # all four runs (kept in runs/ct-llama70b-mlx4-*). Same model and server with the prompt cache capped at 16 GB.
    {"id": "llama70b-mlx4-cap16", "model": "llama", "runner": "mlxserver", "quant": "MLX 4-bit",
     "repo": LLAMA_TOK[0], "revision": LLAMA_TOK[1], "prompt_cache_bytes": "16GB"},
    # 2026-10-20 evening: cap16 failed exactly like the default. mlx_lm.server serves any request that sets "seed" on
    # its single-request path (_is_batchable needs seed None), and only the batched path applies --prompt-cache-bytes.
    # Same cap, seed left out of the request (no effect at temperature 0) so the cap takes effect.
    {"id": "llama70b-mlx4-cap16-noseed", "model": "llama", "runner": "mlxserver", "quant": "MLX 4-bit",
     "repo": LLAMA_TOK[0], "revision": LLAMA_TOK[1], "prompt_cache_bytes": "16GB", "omit_seed": True},
]


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(ROOT / "results/cache_test.log", "a") as f:
        f.write(line + "\n")


def prompt_len(tok, messages, kw):
    """Token count of the rendered prompt (transformers 5 returns a dict from apply_chat_template)."""
    r = tok.t.apply_chat_template(messages, add_generation_prompt=True, tokenize=True, **(kw or {}))
    return len(r["input_ids"]) if hasattr(r, "keys") else len(r)


class CacheEvents(EventWriter):
    """Same event writer; run_end/run_fail history lines go to runs/history.cache.jsonl, not the Set 1 history."""
    def emit(self, type_, **fields):
        rec = {**fields, "t": now(), "type": type_}
        line = json.dumps(rec) + "\n"
        with open(self.path, "a") as f:
            f.write(line)
        if type_ in ("run_end", "run_fail"):
            with open(HISTORY, "a") as f:
                f.write(line)
        return rec


# ------------------------------------------------------------------ servers with caching ON
class Server(Runner):
    """One server process per run. turn() streams one chat request and returns the measured fields."""
    reasoning_field = "reasoning_content"
    def __init__(self, v, m, run_dir_fn):
        super().__init__(v, {"template_kwargs": {"enable_thinking": True} if m["thinking"] else {}}, run_dir_fn)
        self.mcfg = m
    def log_size(self):
        try:
            return Path(self.log_path).stat().st_size
        except Exception:
            return 0
    def log_since(self, offset):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(offset)
                return f.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def _openai_turn(self, path, body, cached_from):
        t0 = time.perf_counter(); t_first = None; t_last = None; content, reasoning = [], []
        usage, timings, finish, chunks = {}, {}, None, 0
        for t, obj in stream(self.port, path, body):
            if obj.get("usage"):
                usage = obj["usage"]
            if obj.get("timings"):
                timings = obj["timings"]
            for ch in obj.get("choices", []):
                d = ch.get("delta", {})
                c = d.get("content") or ""
                r = d.get("reasoning_content") or d.get("reasoning") or ""
                if c or r:
                    if t_first is None:
                        t_first = t
                    t_last = t; chunks += 1
                    content.append(c); reasoning.append(r)
                finish = ch.get("finish_reason") or finish
        out = usage.get("completion_tokens") or timings.get("predicted_n") or chunks
        res = {"ttft_s": round(t_first - t0, 4) if t_first else None, "content": "".join(content), "reasoning": "".join(reasoning),
               "finish_reason": finish, "output_tokens": out,
               "decode_tps": round((out - 1) / (t_last - t_first), 3) if t_first and t_last and t_last > t_first and out > 1 else None,
               "raw": {"usage": usage, "timings": timings}}
        cached_from(res, usage, timings)
        return res


class MLXServer(Server):
    name = "mlx_lm.server"
    reasoning_field = "reasoning_content"
    def version(self):
        import importlib.metadata as m
        return f"mlx-lm {m.version('mlx-lm')} / mlx {m.version('mlx')}"
    def start(self):
        snap = snapshot(self.v["repo"], self.v.get("revision"))
        self.port = free_port()
        # Prompt cache at its defaults (--prompt-cache-size 10, no byte limit) unless the variant sets a byte cap;
        # no trust-remote-code.
        cmd = [str(ROOT / ".venv/bin/mlx_lm.server"), "--model", str(snap), "--host", "127.0.0.1", "--port", str(self.port)]
        if self.v.get("prompt_cache_bytes"):
            cmd += ["--prompt-cache-bytes", self.v["prompt_cache_bytes"]]
        self._spawn(cmd, log_name="server.log")
        wait_http(f"http://127.0.0.1:{self.port}/v1/models", 1800, self.proc)
        return " ".join(cmd)
    def turn(self, messages, max_tokens):
        body = {"model": "default_model", "messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": SEED,
                "stream": True, "stream_options": {"include_usage": True}, "chat_template_kwargs": self.template_kwargs}
        if self.v.get("omit_seed"):
            del body["seed"]
        def cached(res, usage, timings):
            d = usage.get("prompt_tokens_details") or {}
            res["context_tokens"] = usage.get("prompt_tokens")
            res["cached_tokens"] = d.get("cached_tokens")
            res["cached_source"] = "usage.prompt_tokens_details.cached_tokens" if "cached_tokens" in d else "not reported"
        return self._openai_turn("/v1/chat/completions", body, cached)


class LlamaServer(Server):
    name = "llama-server"
    def version(self):
        out = subprocess.run([llama_server(), "--version"], capture_output=True, text=True)
        m = re.search(r"version: (\S+) \(build (\d+), commit (\w+)\)", out.stdout + out.stderr)
        return f"llama.cpp {m.group(1)} b{m.group(2)} ({m.group(3)})" if m else "llama.cpp ?"
    def start(self):
        snap = snapshot(self.v["repo"], self.v.get("revision"))
        self.port = free_port()
        # Prompt caching at defaults (--cache-prompt on, --ctx-checkpoints 32, --cache-ram 8192); -lv 4 logs the
        # slot's cache decisions so full reprocessing can be counted per turn.
        cmd = [llama_server(), "-m", str(snap / self.v["file"]), "--ctx-size", str(self.mcfg["ctx"]), "--seed", str(SEED),
               "-ngl", "999", "--parallel", "1", "--host", "127.0.0.1", "--port", str(self.port), "--jinja", "--no-webui", "-lv", "4"]
        self._spawn(cmd, log_name="server.log")
        wait_http(f"http://127.0.0.1:{self.port}/health", 1800, self.proc)
        return " ".join(cmd)
    def turn(self, messages, max_tokens):
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": SEED, "stream": True,
                "stream_options": {"include_usage": True}, "chat_template_kwargs": self.template_kwargs}
        def cached(res, usage, timings):
            n, c = timings.get("prompt_n"), timings.get("cache_n")
            res["context_tokens"] = (n or 0) + (c or 0) if n is not None else usage.get("prompt_tokens")
            res["cached_tokens"] = c
            res["cached_source"] = "timings.cache_n" if c is not None else "not reported"
            res["server_ttft_s"] = round(timings["prompt_ms"] / 1000, 4) if timings.get("prompt_ms") else None
            res["server_prompt_tps"] = round(timings["prompt_per_second"], 2) if timings.get("prompt_per_second") else None
            if timings.get("predicted_per_second"):
                res["decode_tps"] = round(timings["predicted_per_second"], 3)
        return self._openai_turn("/v1/chat/completions", body, cached)


class OMLXServer(Server):
    name = "oMLX"
    def version(self):
        out = subprocess.run([str(ROOT / ".venv-omlx/bin/omlx"), "--version"], capture_output=True, text=True).stdout.strip()
        return ("omlx " + out.split()[-1]) if out else "omlx ?"
    def start(self):
        snap = snapshot(self.v["repo"], self.v.get("revision"))
        mdir = ROOT / "runs/_omlx_models"; mdir.mkdir(parents=True, exist_ok=True)
        link = mdir / self.v["id"]
        if not link.exists():
            link.symlink_to(snap)
        # 2026-10-17: prefix cache on the internal SSD, 30 GB cap, a fresh folder per run.
        cache = ROOT / "runs/_omlx_cache"
        if cache.exists():
            shutil.rmtree(cache)
        cache.mkdir(parents=True)
        self.port = free_port()
        cmd = [str(ROOT / ".venv-omlx/bin/omlx"), "serve", "--model-dir", str(mdir), "--host", "127.0.0.1", "--port", str(self.port),
               "--max-concurrent-requests", "1", "--no-hf-cache", "--log-level", "info",
               "--paged-ssd-cache-dir", str(cache), "--paged-ssd-cache-max-size", "30GB"]
        self._spawn(cmd, log_name="server.log")
        wait_http(f"http://127.0.0.1:{self.port}/v1/models", 300, self.proc)
        return " ".join(cmd)
    def turn(self, messages, max_tokens):
        body = {"model": self.v["id"], "messages": messages, "max_tokens": max_tokens, "temperature": 0, "seed": SEED,
                "stream": True, "stream_options": {"include_usage": True}, "chat_template_kwargs": self.template_kwargs}
        def cached(res, usage, timings):
            d = usage.get("prompt_tokens_details") or {}
            c = d.get("cached_tokens", usage.get("cached_tokens"))
            res["context_tokens"] = usage.get("prompt_tokens")
            res["cached_tokens"] = c
            res["cached_source"] = ("usage.prompt_tokens_details.cached_tokens" if "cached_tokens" in d else
                                    "usage.cached_tokens" if "cached_tokens" in usage else "not reported")
        return self._openai_turn("/v1/chat/completions", body, cached)


class OllamaServer(Server):
    name = "Ollama"
    reasoning_field = "thinking"
    port = 11434
    def version(self):
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version", timeout=3) as r:
                return "ollama " + json.load(r)["version"]
        except Exception:
            return "ollama " + subprocess.run([OLLAMA, "--version"], capture_output=True, text=True).stdout.strip().split()[-1]
    def start(self):
        import urllib.request
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/version", timeout=2)
            raise RuntimeError("an Ollama server is already running (the Ollama app?); quit it so the test can restart its own server between runs")
        except RuntimeError:
            raise
        except Exception:
            pass
        self._spawn([OLLAMA, "serve"], env={"OLLAMA_HOST": f"127.0.0.1:{self.port}", "OLLAMA_KEEP_ALIVE": "60m"}, log_name="server.log")
        wait_http(f"http://127.0.0.1:{self.port}/api/version", 60, self.proc)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/tags", timeout=10) as r:
            have = {m["name"]: m["digest"] for m in json.load(r).get("models", [])}
        got = have.get(self.v["ollama_tag"])
        if not got or not got.startswith(self.v["ollama_digest"]):
            raise RuntimeError(f"{self.v['ollama_tag']}: digest {got and got[:12]} != {self.v['ollama_digest']}")
        return f"{OLLAMA} serve (OLLAMA_HOST=127.0.0.1:{self.port}, keep_alive 60m); /api/chat num_ctx={self.mcfg['ctx']}"
    def turn(self, messages, max_tokens):
        body = {"model": self.v["ollama_tag"], "messages": messages, "stream": True, "keep_alive": "60m",
                "options": {"num_ctx": self.mcfg["ctx"], "seed": SEED, "temperature": 0, "num_predict": max_tokens}}
        if self.mcfg["thinking"]:
            body["think"] = True
        t0 = time.perf_counter(); t_first = None; t_last = None; content, reasoning, final, n = [], [], {}, 0
        for t, obj in stream(self.port, "/api/chat", body):
            msg = obj.get("message", {})
            c, r = msg.get("content") or "", msg.get("thinking") or ""
            if c or r:
                if t_first is None:
                    t_first = t
                t_last = t; n += 1; content.append(c); reasoning.append(r)
            if obj.get("done"):
                final = obj
        pe, pd, ec, ed = (final.get(k) for k in ("prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration"))
        return {"ttft_s": round(t_first - t0, 4) if t_first else None, "content": "".join(content), "reasoning": "".join(reasoning),
                "finish_reason": final.get("done_reason"), "output_tokens": ec or n,
                "decode_tps": round(ec / (ed / 1e9), 3) if ec and ed else None,
                # Ollama reports tokens evaluated, not cached: new_tokens comes from the server, the context size from
                # the client-side render (filled in by the caller), cached = context - evaluated (labelled as derived).
                "new_tokens_server": pe, "server_ttft_s": round(pd / 1e9, 4) if pd else None,
                "server_prompt_tps": round(pe / (pd / 1e9), 2) if pe and pd else None,
                "cached_source": "derived: client-rendered prompt tokens - Ollama prompt_eval_count", "raw": final}
    def stop(self):
        try:
            post_json(self.port, "/api/generate", {"model": self.v["ollama_tag"], "keep_alive": 0}, timeout=60)
        except Exception:
            pass
        super().stop()


SERVERS = {"mlxserver": MLXServer, "llamaserver": LlamaServer, "omlx": OMLXServer, "ollama": OllamaServer}


# ------------------------------------------------------------------ the test
def done_runs():
    if not CSV_PATH.exists():
        return set()
    with open(CSV_PATH) as f:
        return {r["run_id"] for r in csv.DictReader(f)}


def append_rows(path, rows):
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


class CacheTest:
    def __init__(self, a):
        self.a = a
        self.sampler = None

    def start_sampler(self):
        if SAMPLER_BIN.exists():
            self.sampler = subprocess.Popen([str(SAMPLER_BIN)], stdout=open(ROOT / "results/sampler.log", "ab"),
                                            stderr=subprocess.STDOUT, start_new_session=True)

    def queue(self):
        want_v = set(self.a.variants.split(",")) if self.a.variants else None
        want_c = set(self.a.conditions.split(",")) if self.a.conditions else None
        out = []
        for v in VARIANTS:
            if want_v and v["id"] not in want_v:
                continue
            m = MODELS[v["model"]]
            for c in m["conditions"]:
                if want_c and c not in want_c:
                    continue
                for rep in range(1, self.a.reps + 1):
                    tag = "dry" if self.a.dry_run else f"r{rep}"
                    out.append((v, c, rep, f"ct-{v['id']}-{c}-{tag}"))
        return out

    def est_minutes(self, v, c):
        """Wall minutes of a finished run of the same variant + condition, else the plan estimate."""
        if CSV_PATH.exists():
            with open(CSV_PATH) as f:
                rows = [r for r in csv.DictReader(f) if r["variant_id"] == v["id"] and r["condition"] == c]
            by_run = {}
            for r in rows:
                by_run.setdefault(r["run_id"], []).append(r)
            walls = []
            for rs in by_run.values():
                ts = sorted(time.mktime(time.strptime(r["started_at"][:19], "%Y-%m-%dT%H:%M:%S")) for r in rs)
                last = max(rs, key=lambda r: int(r["turn"]))
                walls.append((ts[-1] - ts[0] + float(last["ttft_s"] or 0) + 60) / 60 + 3)     # + server start
            if walls:
                return max(walls)
        return EST_MIN[(v["model"], c)]

    def run(self):
        q = self.queue()
        done = set() if self.a.dry_run else done_runs()
        log(f"cache test: {len(q)} runs, {sum(1 for x in q if x[3] in done)} already done"
            + (f"; stop before {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.a.stop_at))}" if self.a.stop_at else ""))
        self.start_sampler()
        reason = "queue complete"
        try:
            for pos, (v, c, rep, rid) in enumerate(q, 1):
                if rid in done:
                    continue
                if PAUSE_FLAG.exists():
                    reason = "paused (scripts/cache_pause.sh)"; break
                est = self.est_minutes(v, c)
                if self.a.stop_at and time.time() + est * 60 > self.a.stop_at:
                    reason = f"stopped: {rid} (~{est:.0f} min) would not finish before {time.strftime('%H:%M', time.localtime(self.a.stop_at))}"; break
                left = [x for x in q[pos - 1:] if x[3] not in done]
                STATUS.write_text(json.dumps({"t": now(), "current": rid, "done": sum(1 for x in q if x[3] in done), "total": len(q),
                                              "est_current_min": round(est), "est_left_h": round(sum(self.est_minutes(x[0], x[1]) for x in left) / 60, 1),
                                              "stop_at": self.a.stop_at}))
                self.one_run(v, c, rep, rid, pos, len(q))
                done = set() if self.a.dry_run else done_runs()
            log(f"cache test: {reason}")
            STATUS.write_text(json.dumps({"t": now(), "current": None, "state": reason, "done": sum(1 for x in q if x[3] in done), "total": len(q)}))
            last = CacheEvents(f"_queue_end_{time.strftime('%Y%m%d-%H%M%S')}")
            last.emit("queue_end")
        finally:
            if isinstance(self.sampler, subprocess.Popen):
                self.sampler.terminate()

    def one_run(self, v, c, rep, rid, pos, total):
        m = MODELS[v["model"]]
        turns = self.a.turns or m["turns"]
        ok, why = check_variant(v, tok=m["tok"])
        if not ok:
            log(f"{rid}: SKIPPED, unverified weights: {why}")
            return
        tok = HFTok(snapshot(*m["tok"]))
        srv = SERVERS[v["runner"]](v, m, lambda: (RUNS / "current").resolve())
        ev = CacheEvents(rid)
        version = srv.version()
        base = {"set": "cache", "model": m["name"], "arch": "dense", "quant": v["quant"], "runner": srv.name, "runner_version": version,
                "context_tokens": 33_000, "load_condition": "clean", "kind": "llm", "gpu_budget_gb": gpu_budget_gb(),
                "condition": c, "condition_name": CONDITIONS[c], "turns_total": turns}
        ev.emit("run_start", run_id=rid, rep=rep, of=self.a.reps, queue_pos=pos, queue_total=total, **base)
        ev.emit("phase", phase="loading")
        baseline = gpu_mem_in_use_gb()
        tripped = {}
        wd = Watchdog(lambda reason: (tripped.setdefault("reason", reason), srv.kill())); wd.start()
        rows, session = [], Session(tok, c, turns, reasoning_field=srv.reasoning_field)
        kw = {"enable_thinking": True} if m["thinking"] else None
        try:
            cmd = srv.start()
            (ev.dir / "cmd.txt").write_text(cmd + "\n")
            ev.runner_pid(srv.proc.pid if srv.proc else None)
            (ev.dir / "session.json").write_text(json.dumps({"system_tokens": session.system_tokens, "condition": c, "turns": turns,
                                                            "tokenizer": f"{m['tok'][0]}@{m['tok'][1][:12]}"}, indent=1))
            for turn in range(1, turns + 1):
                msgs = session.messages(turn)
                client_ctx = prompt_len(tok, [dict(x, **({"reasoning_content": x["thinking"]} if "thinking" in x else {})) for x in msgs], kw)
                (ev.dir / f"turn{turn:02d}_request.json").write_text(json.dumps({"messages": msgs}, ensure_ascii=False))
                ev.emit("phase", phase="prefill")
                ev.emit("cache_turn", condition=c, turn=turn, turns_total=turns, context_tokens=client_ctx,
                        cached_tokens=None, new_tokens=None, ttft_s=None)
                off = srv.log_size()
                t0 = time.time(); started = now()
                res = srv.turn(msgs, OUTPUT_TOKENS)
                t1 = time.time()
                if not res.get("output_tokens") or res.get("finish_reason") == "error" or (not res["content"] and not res["reasoning"]):
                    raise RuntimeError(f"empty response at turn {turn} (finish_reason={res.get('finish_reason')})")
                # A stream that ends without a finish reason was cut off (2026-10-19: Ollama 0.40.3's MLX runner panicked
                # mid-answer on the M2 Max, Metal threadgroup 960 > 896, and every turn looked like a short normal answer).
                if res.get("finish_reason") not in ("length", "stop"):
                    raise RuntimeError(f"stream ended without a finish reason at turn {turn} (got {res.get('finish_reason')!r}); "
                                       f"see turn{turn:02d}_server.log")
                session.record_reply(res["content"], res["reasoning"])
                logtxt = srv.log_since(off)
                (ev.dir / f"turn{turn:02d}_server.log").write_text(logtxt)
                (ev.dir / f"turn{turn:02d}_response.json").write_text(json.dumps({k: res[k] for k in res if k != "raw"} | {"raw": res.get("raw")},
                                                                               ensure_ascii=False, default=str))
                ctx_tok = res.get("context_tokens") or client_ctx
                if "new_tokens_server" in res:                       # Ollama
                    # Ollama 0.40.6: prompt_eval_count is the whole prompt, cached part included, so it can't show reuse.
                    # The server logs its own prefix-cache decision per request:
                    #   msg="cache hit" total=36337 matched=33294 cached=33294 left=3043
                    # No such line for a request means nothing was reused.
                    ctx_tok = res["new_tokens_server"] or client_ctx
                    hit = re.search(r'msg="cache hit" total=(\d+) matched=\d+ cached=(\d+) left=(\d+)', logtxt)
                    if hit:
                        ctx_tok, cached, new = int(hit.group(1)), int(hit.group(2)), int(hit.group(3))
                    else:
                        cached, new = 0, ctx_tok
                    res["cached_source"] = 'server log: prefix_cache "cache hit" cached= (absent = 0)'
                else:
                    cached = res.get("cached_tokens"); new = (ctx_tok - cached) if cached is not None else None
                ttft = res["ttft_s"]
                w = window_summary(ev.dir, t0, t1, RUNS / "power_all.jsonl")
                peak = round(w["gpu_mem_in_use_gb_max"] - baseline, 3) if w.get("gpu_mem_in_use_gb_max") and baseline is not None else None
                notes = []
                if os.environ.get("BENCH_ALLOW_UNVERIFIED") == "1":
                    notes.append("UNVERIFIED weights (BENCH_ALLOW_UNVERIFIED=1)")
                if res.get("server_ttft_s"):
                    notes.append(f"server prompt time {res['server_ttft_s']} s")
                if c == "D" and turn > 1:
                    notes.append("earlier reasoning stripped")
                if res.get("finish_reason") not in ("length", "stop"):
                    notes.append(f"finish_reason {res.get('finish_reason')}")
                row = {"run_id": rid, "model": m["name"], "quant": v["quant"], "runner": srv.name, "runner_version": version,
                       "condition": c, "turn": turn, "context_tokens": ctx_tok, "new_tokens": new, "cached_tokens": cached,
                       "cached_source": res.get("cached_source", "not reported"), "ttft_s": ttft,
                       "prefill_tps_uncached": round(new / ttft, 2) if new and ttft else None, "decode_tps": res.get("decode_tps"),
                       "peak_mem_gb": peak, "mem_pressure_max": w.get("mem_pressure_max"),
                       "reprocess_lines": logtxt.count("forcing full prompt re-processing") if v["runner"] == "llamaserver" else None,
                       "notes": "; ".join(notes), "rep": rep, "turns_total": turns, "started_at": started,
                       "output_tokens": res.get("output_tokens"), "reasoning_chars": len(res["reasoning"]), "content_chars": len(res["content"]),
                       "finish_reason": res.get("finish_reason"), "server_ttft_s": res.get("server_ttft_s"),
                       "server_prompt_tps": res.get("server_prompt_tps"),
                       "restored_checkpoint_lines": logtxt.count("restored context checkpoint") if v["runner"] == "llamaserver" else None,
                       "swap_used_gb_max": w.get("swap_used_gb_max"), "package_power_w_avg": w.get("package_power_w_avg"),
                       "energy_wh": w.get("energy_wh"), "variant_id": v["id"], "repo": v.get("repo") or v.get("ollama_tag"),
                       "revision": v.get("revision") or v.get("ollama_digest"), "server_cmd": cmd}
                rows.append(row)
                ev.emit("progress", tokens=res.get("output_tokens"), ttft_s=ttft, decode_tps=res.get("decode_tps"),
                        prefill_tps=row["prefill_tps_uncached"])
                ev.emit("cache_turn", condition=c, turn=turn, turns_total=turns, context_tokens=ctx_tok, cached_tokens=cached,
                        new_tokens=new, ttft_s=ttft)
                log(f"  {rid} turn {turn}/{turns}: ctx {ctx_tok}, cached {cached}, new {new}, TTFT {ttft}s, decode {res.get('decode_tps')} tok/s"
                    + (f", reprocess lines {row['reprocess_lines']}" if row["reprocess_lines"] is not None else ""))
            ev.emit("phase", phase="done")
            last = rows[-1]
            ev.emit("run_end", run_id=rid, fit="yes", ttft_s=last["ttft_s"], decode_tps=last["decode_tps"], peak_mem_gb=last["peak_mem_gb"],
                    rep=rep, warmup=False, **base)
            append_rows(DRY_CSV if self.a.dry_run else CSV_PATH, rows)
        except Exception as e:
            reason = f"did not fit: {tripped['reason']}" if tripped.get("reason") else f"error: {repr(e)[:300]}"
            ev.emit("run_fail", run_id=rid, reason=reason, **base)
            (ev.dir / "failure.txt").write_text(reason + "\n\n" + traceback.format_exc())
            log(f"{rid}: FAILED ({reason}); {len(rows)} turns kept in runs/{rid}/ only")
        finally:
            wd.stop()
            srv.stop()


def ollama_default_ctx():
    """One fact: Ollama's default context window on this Mac (no num_ctx), and the memory it takes."""
    import urllib.request
    v = next(x for x in VARIANTS if x["runner"] == "ollama")
    srv = OllamaServer(v, MODELS["qwen"], lambda: (RUNS / "current").resolve())
    ev = CacheEvents("ct-ollama-default-ctx")
    srv.start()
    try:
        baseline = gpu_mem_in_use_gb()
        r = post_json(srv.port, "/api/generate", {"model": v["ollama_tag"], "prompt": "Say hello in five words.", "stream": False,
                                                  "keep_alive": "10m", "options": {"seed": SEED, "temperature": 0, "num_predict": 32}}, timeout=1800)
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/api/ps", timeout=10) as resp:
            ps = json.load(resp)
        cli = subprocess.run([OLLAMA, "ps"], capture_output=True, text=True, env={**os.environ, "OLLAMA_HOST": f"127.0.0.1:{srv.port}"}).stdout
        after = gpu_mem_in_use_gb()
        (ev.dir / "ollama_ps.json").write_text(json.dumps(ps, indent=1)); (ev.dir / "ollama_ps.txt").write_text(cli)
        mdl = next((x for x in ps.get("models", []) if x.get("name", "").startswith("qwen3.8")), {})
        line = (f"| {now()} | {srv.version()} | {v['ollama_tag']} ({v['ollama_digest']}) | context_length {mdl.get('context_length')} "
                f"| size_vram {round(mdl.get('size_vram', 0) / 1e9, 2)} GB | GPU memory in use +{round((after or 0) - (baseline or 0), 2)} GB "
                f"| prompt_eval_count {r.get('prompt_eval_count')} | source: runs/ct-ollama-default-ctx/ollama_ps.json + ollama_ps.txt |\n")
        if not NOTES.exists():
            NOTES.write_text("# Cache test notes\n\n## Ollama default context window (no num_ctx)\n\n"
                             "| time | version | model | context | VRAM (Ollama) | GPU memory delta | prompt tokens | source |\n|---|---|---|---|---|---|---|---|\n")
        with open(NOTES, "a") as f:
            f.write(line)
        log("ollama default ctx: " + line.strip())
        print(cli)
    finally:
        srv.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants")
    ap.add_argument("--conditions")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--turns", type=int, help="override the per-model turn count (dry run)")
    ap.add_argument("--dry-run", action="store_true", help="Qwen MLX, condition A, 3 turns, rows to results/cache_test.dry.csv")
    ap.add_argument("--ollama-default-ctx", action="store_true")
    ap.add_argument("--stop-at", help="HH:MM: start no run that would not finish before this time (next occurrence)")
    a = ap.parse_args()
    if a.stop_at:
        import datetime as dt
        h, mnt = (int(x) for x in a.stop_at.split(":"))
        t = dt.datetime.now().replace(hour=h, minute=mnt, second=0, microsecond=0)
        if t <= dt.datetime.now():
            t += dt.timedelta(days=1)
        a.stop_at = t.timestamp()
    if a.dry_run:
        a.variants, a.conditions, a.turns, a.reps = a.variants or "qwen27b-mlx4", a.conditions or "A", a.turns or 3, 1
    refuse_root()
    exit_on_signals()
    run_hook("pre_run")
    try:
        ps = subprocess.run(["/usr/bin/pgrep", "-fl", "mlx_lm.server|llama-server|omlx serve|mtplx serve|mlx-serve|ollama runner|jais_worker|mlx_worker|run_eval.py|run_speed.py"],
                            capture_output=True, text=True).stdout.strip()
        if ps:
            sys.exit(f"Another model process is running; stop it first:\n{ps}")
        acquire_bench_lock("cache test")
        (ROOT / "results").mkdir(exist_ok=True)
        if a.ollama_default_ctx:
            ollama_default_ctx()
        else:
            CacheTest(a).run()
    finally:
        run_hook("post_run")


if __name__ == "__main__":
    main()
