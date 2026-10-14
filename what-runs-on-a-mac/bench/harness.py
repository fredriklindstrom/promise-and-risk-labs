"""Test set 1 harness: language models, unattended and resumable.

    python -m bench.harness --set 1 --load-condition clean [--variants id,id] [--contexts 1024,32768] [--no-needle]

A queue item ("cell") is one variant at one context: load once, one discarded warm-up, three timed
reps (or the needle depths x tries). Every rep is its own run_id with its own runs/<run_id>/ folder.
A run_id already in results/summary.csv is skipped on restart.
"""
import argparse, csv, datetime, re, json, os, shutil, subprocess, sys, time, traceback
from pathlib import Path
import yaml
from . import ROOT, load_manifest, machine_label, validate_manifest, refuse_root, exit_on_signals, run_hook
from .verify import check_variant
from .diskguard import status as disk_status, wait_until_ok
from .events import EventWriter, RUNS, acquire_bench_lock, now
from .runners.adapters import RUNNERS
from .system import FallbackSampler, Watchdog, foreground_apps, gpu_budget_gb, gpu_mem_in_use_gb, window_summary
from .workloads.prompts import build, score_needle

SUMMARY = ROOT / "results/summary.csv"
COLUMNS = ["run_id", "started_at", "set", "load_condition", "model", "params", "arch", "active_params", "quant", "file_format",
           "runner", "runner_version", "context_tokens", "rep", "load_s", "ttft_s", "prefill_tps", "decode_tps", "output_tokens",
           "peak_mem_gb", "gpu_budget_gb", "mem_pressure_max", "swap_used_gb_max", "gpu_active_pct_avg", "gpu_power_w_avg",
           "package_power_w_avg", "energy_wh", "wh_per_1k_tokens", "needle_depth", "needle_correct", "fit", "failure_reason", "notes",
           # extras, after the spec columns
           "kind", "prompt_tokens", "finish_reason", "wall_s", "mem_used_gb_max", "gpu_energy_wh", "power_samples",
           "peak_mem_source", "gpu_mem_peak_gb", "gpu_mem_baseline_gb", "timing_source", "tokenizer_source", "variant_id", "repo", "revision",
           "package_power_w_max", "gpu_power_w_max", "machine",
           "thermal_state_max", "thermal_pressure_max", "gpu_freq_mhz_avg", "gpu_freq_mhz_min", "cpu_p_freq_mhz_avg",
           "soc_temp_max_c", "soc_temp_avg_c"]   # peaks + thermal added 2026-10; write_csv_row migrates older headers
SAMPLER_BIN = ROOT / "sampler/.build/release/bench-sampler"


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with open(ROOT / "results/harness.log", "a") as f:
        f.write(line + "\n")


def done_ids():
    if not SUMMARY.exists():
        return set()
    with open(SUMMARY) as f:
        return {r["run_id"] for r in csv.DictReader(f)}


def write_csv_row(path, columns, row):
    """Append a row; if the file's header predates a column change, rewrite it once with the new header
    (old rows keep their values, new columns blank) so the CSV never goes out of alignment."""
    if path.exists():
        with open(path, newline="") as f:
            header = next(csv.reader(f), [])
        if header != columns:
            with open(path, newline="") as f:
                old = list(csv.DictReader(f))
            tmp = path.with_suffix(".migrating")
            with open(tmp, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
                w.writeheader()
                w.writerows(old)
            os.replace(tmp, path)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def write_row(row):
    write_csv_row(SUMMARY, COLUMNS, row)


def ctx_label(c):
    return f"{c // 1024}k"


def build_queue(m, args):
    want_v = set(args.variants.split(",")) if args.variants else None
    want_c = {int(x) for x in args.contexts.split(",")} if args.contexts else None
    cells = []
    for v in m["variants"]:
        if want_v and v["id"] not in want_v:
            continue
        if not want_v and (v.get("dry_run") or v.get("optional")):     # optional probes only when named in --variants
            continue
        for c in v["contexts"]:
            if want_c and c not in want_c:
                continue
            cells.append({"v": v, "ctx": c, "kind": "speed",
                          "runs": [f"s{args.set}-{args.load_condition}-{v['id']}-{ctx_label(c)}-r{r}" for r in range(1, m["settings"]["timed_reps"] + 1)]})
        if args.no_needle:
            continue
        for c in v.get("needle", []):
            if want_c and c not in want_c:
                continue
            s = m["settings"]
            cells.append({"v": v, "ctx": c, "kind": "needle",
                          "runs": [f"s{args.set}-{args.load_condition}-{v['id']}-{ctx_label(c)}-needle-d{d}-t{t}"
                                   for d in s["needle_depths"] for t in range(1, s["needle_tries"] + 1)]})
    # Every speed cell first, needle cells last (32K before 128K): a slow night still covers every model.
    return sorted(cells, key=lambda c: (c["kind"] == "needle", c["ctx"] if c["kind"] == "needle" else 0))


class Harness:
    def __init__(self, args):
        self.args = args
        self.m = load_manifest()
        validate_manifest(self.m)
        self.s = self.m["settings"]
        self.sampler = None
        self.budget = gpu_budget_gb()
        self.machine = machine_label()
        from .diskguard import _cfg
        dg = _cfg()           # Bench Monitor colours disk free against this manifest's own floor/warn
        self.disk_levels = {"disk_floor_gb": dg["internal_floor_gb"], "disk_warn_gb": dg["internal_warn_gb"]}
        self.failed_ctx = {}          # variant id -> smallest context that did not fit

    # ---------------------------------------------------------------- sampler
    def start_sampler(self):
        if SAMPLER_BIN.exists():
            self.sampler = subprocess.Popen([str(SAMPLER_BIN)], stdout=open(ROOT / "results/sampler.log", "ab"),
                                            stderr=subprocess.STDOUT, start_new_session=True)
            log(f"sampler: {SAMPLER_BIN} pid {self.sampler.pid}")
        else:
            self.sampler = FallbackSampler(lambda: (RUNS / "current").resolve() / "samples.jsonl")
            self.sampler.start()
            log("sampler: Swift bench-sampler not built yet, using the Python fallback (no power, no runner RSS)")

    def stop_sampler(self):
        if isinstance(self.sampler, subprocess.Popen):
            self.sampler.terminate()
        elif self.sampler:
            self.sampler.stop()

    # ---------------------------------------------------------------- main loop
    def run(self):
        (ROOT / "results").mkdir(exist_ok=True)
        cells = build_queue(self.m, self.args)
        done = done_ids()
        total = sum(len(c["runs"]) for c in cells)
        todo = sum(1 for c in cells for r in c["runs"] if r not in done)
        apps = foreground_apps() if self.m.get("record_foreground_apps") else ["(not recorded: set record_foreground_apps: true)"]
        cond = {"t": now(), "set": self.args.set, "load_condition": self.args.load_condition, "foreground_apps": apps,
                "disk": disk_status(), "gpu_budget_gb": self.budget, "queue_total": total, "todo": todo}
        with open(ROOT / "results/conditions.jsonl", "a") as f:
            f.write(json.dumps(cond) + "\n")
        log(f"queue: {len(cells)} cells, {total} runs, {todo} to do; load condition {self.args.load_condition}; apps: {', '.join(apps)}")
        self.start_sampler()
        pos = 0
        try:
            for cell in cells:
                pending = [r for r in cell["runs"] if r not in done]
                if not pending:
                    pos += len(cell["runs"])
                    continue
                wait_until_ok(log)
                self.run_cell(cell, pos, total, done)
                pos += len(cell["runs"])
            last = EventWriter(f"_queue_end_{datetime.datetime.now():%Y%m%d-%H%M%S}")
            last.emit("queue_end")
        finally:
            self.stop_sampler()
        log("queue finished")

    # ---------------------------------------------------------------- one cell
    def run_cell(self, cell, pos, total, done):
        v, ctx, kind = cell["v"], cell["ctx"], cell["kind"]
        mcfg = self.m["models"][v["model"]]
        # A larger context than one that already failed for this variant is recorded, not attempted.
        if v["id"] in self.failed_ctx and ctx > self.failed_ctx[v["id"]]:
            for i, rid in enumerate(cell["runs"]):
                if rid not in done:
                    self.write_fail_row(rid, cell, mcfg, None, f"skipped: {ctx_label(self.failed_ctx[v['id']])} context already did not fit", fit="skipped")
            return
        ok_w, why = check_variant(v)
        if not ok_w:      # not a result: logged as an error and retried once the weights are verified
            for rid in cell["runs"]:
                if rid not in done:
                    self.write_fail_row(rid, cell, mcfg, None, f"error: unverified weights: {why}")
            return
        runner = RUNNERS[v["runner"]](v, mcfg, lambda: (RUNS / "current").resolve())
        version = runner.version()
        base = {"set": str(self.args.set), "model": mcfg["name"], "arch": mcfg["arch"], "active_params": mcfg.get("active_params"),
                "quant": v["quant"], "runner": runner.name, "runner_version": version, "context_tokens": ctx,
                "load_condition": self.args.load_condition, "kind": "llm", "gpu_budget_gb": self.budget, "machine": self.machine,
                **self.disk_levels}
        first_id = next(r for r in cell["runs"] if r not in done)
        warm = EventWriter(first_id + "-warmup")
        warm.emit("run_start", run_id=warm.run_id, rep=0, of=len(cell["runs"]), queue_pos=pos + 1, queue_total=total, warmup=True, **base)
        warm.emit("phase", phase="loading")
        tripped = {}
        wd = Watchdog(lambda reason: (tripped.setdefault("reason", reason), runner.kill()))
        wd.start()
        self.gpu_baseline = gpu_mem_in_use_gb()
        try:
            try:
                info = runner.start(ctx)
            except Exception as e:
                reason = self.failure_reason(e, runner, tripped)
                warm.emit("run_fail", run_id=warm.run_id, reason=reason, **base)
                for rid in cell["runs"]:
                    if rid not in done:
                        self.write_fail_row(rid, cell, mcfg, version, reason)
                if reason.startswith("did not fit"):
                    self.failed_ctx.setdefault(v["id"], ctx)
                return
            warm.runner_pid(info.get("pid"))
            (warm.dir / "cmd.txt").write_text(info["cmd"] + "\n")
            log(f"{v['id']} @ {ctx_label(ctx)} [{kind}] loaded in {info['load_s']}s ({version})")
            # discarded warm-up: same shape as the timed run
            ok = self.one_request(warm, runner, cell, mcfg, base, info, rep=0, depth=None, warmup=True, tripped=tripped)
            if not ok:
                for rid in cell["runs"]:
                    if rid not in done:
                        self.write_fail_row(rid, cell, mcfg, version, ok_reason(warm))
                if ok_reason(warm).startswith("did not fit"):
                    self.failed_ctx.setdefault(v["id"], ctx)
                return
            for i, rid in enumerate(cell["runs"]):
                if rid in done:
                    continue
                ev = EventWriter(rid)
                ev.runner_pid(info.get("pid"))
                (ev.dir / "cmd.txt").write_text(info["cmd"] + "\n")
                depth = int(rid.split("-d")[-1].split("-")[0]) if kind == "needle" else None
                rep = i + 1
                ev.emit("run_start", run_id=rid, rep=rep, of=len(cell["runs"]), queue_pos=pos + i + 1, queue_total=total, **base)
                ok = self.one_request(ev, runner, cell, mcfg, base, info, rep=rep, depth=depth, warmup=False, tripped=tripped)
                if not ok:
                    for rid2 in cell["runs"][i + 1:]:
                        if rid2 not in done:
                            self.write_fail_row(rid2, cell, mcfg, version, ok_reason(ev))
                    if ok_reason(ev).startswith("did not fit"):
                        self.failed_ctx.setdefault(v["id"], ctx)
                    return
        finally:
            wd.stop()
            runner.stop()

    def one_request(self, ev, runner, cell, mcfg, base, info, rep, depth, warmup, tripped):
        v, ctx, kind = cell["v"], cell["ctx"], cell["kind"]
        max_out = self.s["max_output_tokens"] if kind == "speed" else 64
        needle = self.s["needle"] if kind == "needle" else None
        msgs, meta = build(runner.tok, ctx, max_out, kind=kind, depth=depth if depth is not None else 50, needle=needle,
                           template_kwargs=runner.template_kwargs)
        (ev.dir / "prompt.json").write_text(json.dumps({"messages": msgs, "meta": meta, "max_tokens": max_out,
                                                        "template_kwargs": runner.template_kwargs}, indent=1))
        ev.emit("phase", phase="prefill")
        t0 = time.time()
        started = now()
        last = {"t": 0}

        def progress(phase=None, **k):
            if phase:
                ev.emit("phase", phase=phase)
            if time.time() - last["t"] >= 0.5 or phase:
                last["t"] = time.time()
                ev.emit("progress", **k)
        try:
            res = runner.generate(msgs, max_out, progress)
        except Exception as e:
            reason = self.failure_reason(e, runner, tripped)
            t1 = time.time()
            ev.emit("run_fail", run_id=ev.run_id, reason=reason, **base)
            if not warmup:
                self.write_fail_row(ev.run_id, cell, mcfg, base["runner_version"], reason, started=started,
                                    extra=window_summary(ev.dir, t0, t1, RUNS / "power_all.jsonl"))
            ev.dir.joinpath("failure.txt").write_text(reason + "\n\n" + traceback.format_exc())
            ev.reason = reason
            return False
        t1 = time.time()
        ev.emit("phase", phase="done")
        (ev.dir / "output.txt").write_text(res.get("text", ""))
        (ev.dir / "result.json").write_text(json.dumps({k: res[k] for k in res if k != "text"}, indent=1, default=str))
        w = window_summary(ev.dir, t0, t1, RUNS / "power_all.jsonl")
        correct = score_needle(res.get("text", ""), self.s["needle_answer"]) if needle else None
        out_tok = res.get("output_tokens") or 0
        notes = []
        if os.environ.get("BENCH_ALLOW_UNVERIFIED") == "1":
            notes.append("UNVERIFIED weights (BENCH_ALLOW_UNVERIFIED=1)")
        if kind == "speed" and out_tok < max_out:
            notes.append(f"stopped at {out_tok} tokens ({res.get('finish_reason')})")
        if res.get("draft_accept"):
            notes.append(f"MTP drafts accepted {res['draft_accept']}")
        if res.get("client_decode_tps") and res.get("timing_source", "").startswith("server"):
            notes.append(f"client-side decode {res['client_decode_tps']} tok/s")
        if info.get("load_note"):
            notes.append(info["load_note"])
        if w["energy_wh"] is not None:
            notes.append("wh_per_1k_tokens = package energy over the whole request / output tokens")
        else:
            notes.append("energy missing: power_sampler.sh not running")
        row = {**base, "run_id": ev.run_id, "started_at": started, "params": mcfg.get("params"), "file_format": v["file_format"],
               "rep": rep, "load_s": info["load_s"], "ttft_s": res.get("ttft_s"), "prefill_tps": res.get("prefill_tps"),
               "decode_tps": res.get("decode_tps"), "output_tokens": out_tok, "peak_mem_gb": res.get("peak_mem_gb"),
               "needle_depth": depth, "needle_correct": correct, "fit": "yes", "failure_reason": "", "notes": "; ".join(notes),
               "prompt_tokens": res.get("prompt_tokens"), "finish_reason": res.get("finish_reason"), "wall_s": round(t1 - t0, 3),
               "peak_mem_source": res.get("peak_mem_source"), "timing_source": res.get("timing_source"),
               "tokenizer_source": getattr(runner.tok, "source", ""), "variant_id": v["id"], "repo": v.get("repo") or v.get("ollama_tag"),
               "revision": v.get("revision"), "kind": kind,
               **{k: w[k] for k in ("mem_pressure_max", "swap_used_gb_max", "gpu_active_pct_avg", "gpu_power_w_avg",
                                    "package_power_w_avg", "energy_wh", "mem_used_gb_max", "gpu_energy_wh", "power_samples",
                                    "package_power_w_max", "gpu_power_w_max", "thermal_state_max", "thermal_pressure_max",
                                    "gpu_freq_mhz_avg", "gpu_freq_mhz_min", "cpu_p_freq_mhz_avg", "soc_temp_max_c", "soc_temp_avg_c")}}
        row["gpu_mem_peak_gb"], row["gpu_mem_baseline_gb"] = w["gpu_mem_in_use_gb_max"], self.gpu_baseline
        if row["peak_mem_gb"] is None and w["gpu_mem_in_use_gb_max"] is not None and self.gpu_baseline is not None:
            row["peak_mem_source"] = "GPU allocated memory (IOAccelerator In use system memory): max during run minus idle baseline before load"
            row["peak_mem_gb"] = round(w["gpu_mem_in_use_gb_max"] - self.gpu_baseline, 3)
        row["wh_per_1k_tokens"] = round(w["energy_wh"] / out_tok * 1000, 4) if w["energy_wh"] and out_tok else None
        ev.emit("run_end", run_id=ev.run_id, fit="yes", decode_tps=row["decode_tps"], prefill_tps=row["prefill_tps"], ttft_s=row["ttft_s"],
                peak_mem_gb=row["peak_mem_gb"], energy_wh=row["energy_wh"], needle_correct=correct, warmup=warmup, rep=rep,
                mem_used_gb_max=row["mem_used_gb_max"], package_power_w_max=row["package_power_w_max"], gpu_power_w_max=row["gpu_power_w_max"],
                thermal_state_max=row["thermal_state_max"], gpu_freq_mhz_min=row["gpu_freq_mhz_min"], soc_temp_max_c=row["soc_temp_max_c"], **base)
        if not warmup:
            write_row(row)
            log(f"  {ev.run_id}: decode {row['decode_tps']} tok/s, prefill {row['prefill_tps']} tok/s, ttft {row['ttft_s']}s, "
                f"{row['prompt_tokens']} prompt tok, peak {row['peak_mem_gb']} GB" + (f", needle {'OK' if correct else 'MISS'}" if needle else ""))
        return True

    def failure_reason(self, e, runner, tripped):
        if tripped.get("reason"):
            return f"did not fit: {tripped['reason']} (watchdog stopped the runner)"
        tail = ""
        try:
            tail = Path(runner.log_path).read_text(errors="replace")[-4000:]
        except Exception:
            pass
        for key in ("out of memory", "Insufficient Memory", "failed to allocate", "kIOGPUCommandBufferCallbackErrorOutOfMemory",
                    "Resource limit", "std::bad_alloc", "Killed", "exceeds"):
            if key.lower() in tail.lower():
                line = next((l for l in tail.splitlines() if key.lower() in l.lower()), key)
                line = re.sub(r"^[\d.]+\s+[EIWD]\s+", "", line.strip())        # llama.cpp log prefix (timestamp + level)
                return f"did not fit: {line[:200]}"
        code = runner.proc.poll() if runner.proc else None
        if code is not None and code < 0:
            return f"did not fit: runner killed by signal {-code}"
        return f"error: {repr(e)[:300]}"

    def write_fail_row(self, rid, cell, mcfg, version, reason, fit="no", started=None, extra=None):
        v = cell["v"]
        if reason.startswith("error:"):
            # A harness/runner error is not a result: log it, keep it out of summary.csv so a restart retries it.
            with open(ROOT / "results/errors.jsonl", "a") as f:
                f.write(json.dumps({"t": now(), "run_id": rid, "variant": v["id"], "ctx": cell["ctx"], "reason": reason}) + "\n")
            log(f"  {rid}: ERROR (will retry on restart) — {reason}")
            return
        row = {"run_id": rid, "started_at": started or now(), "set": str(self.args.set), "load_condition": self.args.load_condition,
               "model": mcfg["name"], "params": mcfg.get("params"), "arch": mcfg["arch"], "active_params": mcfg.get("active_params"),
               "quant": v["quant"], "file_format": v["file_format"], "runner": RUNNERS[v["runner"]].name, "runner_version": version,
               "context_tokens": cell["ctx"], "gpu_budget_gb": self.budget, "machine": self.machine, "fit": fit, "failure_reason": reason, "kind": cell["kind"],
               "variant_id": v["id"], "repo": v.get("repo") or v.get("ollama_tag"), "revision": v.get("revision"), **(extra or {})}
        write_row(row)
        # Also into runs/history.jsonl, so Bench Monitor counts runs that were recorded without being attempted.
        with open(RUNS / "history.jsonl", "a") as f:
            f.write(json.dumps({"t": row["started_at"], "type": "run_fail", "run_id": rid, "reason": reason, "fit": fit,
                                "model": row["model"], "runner": row["runner"], "quant": row["quant"], "kind": "llm",
                                "context_tokens": row["context_tokens"], "set": row["set"], "load_condition": row["load_condition"]}) + "\n")
        log(f"  {rid}: fit={fit} — {reason}")


def ok_reason(ev):
    return getattr(ev, "reason", "warm-up failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="1")
    ap.add_argument("--load-condition", default="clean", choices=["clean", "everyday", "concurrent"])
    ap.add_argument("--variants")
    ap.add_argument("--contexts")
    ap.add_argument("--no-needle", action="store_true")
    ap.add_argument("--allow-other-servers", action="store_true", help="run even if another model server is up (results will be skewed)")
    a = ap.parse_args()
    refuse_root()
    exit_on_signals()
    run_hook("pre_run")          # e.g. scripts/pre_run.local.sh stops other GPU-heavy services (gitignored)
    try:
        if not a.allow_other_servers:
            ps = subprocess.run(["/usr/bin/pgrep", "-fl", "mlx_lm.server|llama-server|omlx serve|mtplx serve|mlx-serve|ollama runner"],
                                capture_output=True, text=True).stdout.strip()
            if ps:
                sys.exit(f"Another model server is running; stop it first (or --allow-other-servers):\n{ps}")
        acquire_bench_lock(f"harness set {a.set} {a.load_condition}")
        Harness(a).run()
    finally:
        run_hook("post_run")


if __name__ == "__main__":
    main()
