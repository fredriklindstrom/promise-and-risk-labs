"""Test set 2 harness: the production stack (H3 b-roll, LTX-2.5, voice, whisper).

    python -m bench.media.run_media --jobs h3 --load-condition clean [--builds h3-pipenetwork-4bit] [--settings a,b,c] [--clips 1]
    python -m bench.media.run_media --jobs voice
    python -m bench.media.run_media --jobs whisper

Each clip / narration is one run_id with its own runs/<run_id>/ folder, events and samples, exactly
like set 1. A run_id already in results/media.csv is skipped on restart. Rows go to results/media.csv,
and every generated clip gets a row in results/keep_review.csv to mark keep yes/no.
"""
import argparse, csv, json, os, re, signal, subprocess, sys, time
from pathlib import Path
import yaml
from .. import ROOT, load_manifest, machine_label
from ..diskguard import wait_until_ok
from ..events import EventWriter, RUNS, acquire_bench_lock, now
from ..harness import log, SAMPLER_BIN
from ..system import FallbackSampler, Watchdog, foreground_apps, gpu_budget_gb, gpu_mem_in_use_gb, window_summary

MEDIA = ROOT / "results/media.csv"
KEEP = ROOT / "results/keep_review.csv"
COLUMNS = ["run_id", "started_at", "set", "load_condition", "job", "model", "quant", "runner", "runner_version",
           "width", "height", "seconds", "steps", "lora", "seed", "prompt_id", "load_s", "wall_s", "s_per_step", "audio_s", "rtf",
           "peak_mem_gb", "gpu_budget_gb", "mem_pressure_max", "swap_used_gb_max", "gpu_active_pct_avg", "gpu_power_w_avg",
           "package_power_w_avg", "energy_wh", "gpu_energy_wh", "fit", "failure_reason", "output_path", "notes",
           "peak_mem_source", "gpu_mem_peak_gb", "gpu_mem_baseline_gb", "power_samples", "cmd",
           "mem_used_gb_max", "package_power_w_max", "gpu_power_w_max", "machine",
           "thermal_state_max", "thermal_pressure_max", "gpu_freq_mhz_avg", "gpu_freq_mhz_min", "cpu_p_freq_mhz_avg",
           "soc_temp_max_c", "soc_temp_avg_c"]
STEP_RE = re.compile(r"step (\d+)/(\d+)\s+([\d.]+)s")
SPS_RE = re.compile(r"([\d.]+)s per step, ([\d.]+) min total")
LOAD_RE = re.compile(r"^\s+(transformer|text encoder|video vae|audio vae)[^:]*: ([\d.]+)s", re.M)


def done_ids():
    if not MEDIA.exists():
        return set()
    return {r["run_id"] for r in csv.DictReader(open(MEDIA))}


def append_csv(path, columns, row):
    from ..harness import write_csv_row
    write_csv_row(path, columns, row)


def prompts():
    p = ROOT / "inputs/broll_prompts.txt"
    if not p.exists():
        sys.exit("inputs/broll_prompts.txt is missing (one prompt per line)")
    return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]


class Job:
    """One subprocess job wrapped with events, sampler window, watchdog and a CSV row."""

    def __init__(self, run_id, base, cmd, cwd, env=None, steps=None, clip=None, clips=None):
        self.run_id, self.base, self.cmd, self.cwd, self.env = run_id, base, cmd, cwd, env or {}
        self.steps, self.clip, self.clips = steps, clip, clips

    def run(self, pos, total):
        ev = EventWriter(self.run_id)
        (ev.dir / "cmd.txt").write_text(" ".join(map(str, self.cmd)) + f"\n(cwd {self.cwd})\n")
        from ..diskguard import _cfg
        dg = _cfg()
        ev.emit("run_start", run_id=self.run_id, rep=self.clip, of=self.clips, queue_pos=pos, queue_total=total, kind="media",
                gpu_budget_gb=gpu_budget_gb(), disk_floor_gb=dg["internal_floor_gb"], disk_warn_gb=dg["internal_warn_gb"],
                **{k: v for k, v in self.base.items() if k not in ("run_id",)})
        ev.emit("phase", phase="loading")
        baseline = gpu_mem_in_use_gb()
        t0 = time.time(); started = now()
        log_path = ev.dir / "runner.log"
        proc = subprocess.Popen(self.cmd, cwd=self.cwd, env={**os.environ, **self.env}, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        ev.runner_pid(proc.pid)
        try:
            return self._watch(ev, proc, t0, started, baseline, log_path)
        finally:                    # whatever happens (trip, Ctrl-C, SIGTERM), the job's whole process group goes
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _watch(self, ev, proc, t0, started, baseline, log_path):
        tripped = {}
        wd = Watchdog(lambda r: (tripped.setdefault("reason", r), os.killpg(proc.pid, signal.SIGKILL)))
        wd.start()
        step_times, out_lines, sps = [], [], None
        with open(log_path, "w") as lf:
            for line in proc.stdout:
                lf.write(line); lf.flush()
                out_lines.append(line)
                m = STEP_RE.search(line)
                if m:
                    i, n, dt = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    if i == 1:
                        ev.emit("phase", phase="rendering")
                    step_times.append(dt)
                    mean = sum(step_times) / len(step_times)
                    ev.emit("media_progress", step=i, steps=n, s_per_step=round(mean, 2), eta_s=round(mean * (n - i)),
                            clip=self.clip, clips=self.clips)
                if line.startswith("{") and '"media_progress"' in line:      # voice/whisper wrappers
                    try:
                        ev.emit("media_progress", **json.loads(line)["media_progress"])
                    except Exception:
                        pass
                m = SPS_RE.search(line)
                if m:
                    sps = float(m.group(1))
                if line.startswith('{"voice_result"') or line.startswith('{"whisper_result"'):
                    self.result = next(iter(json.loads(line).values()))
        code = proc.wait()
        wd.stop()
        t1 = time.time()
        w = window_summary(ev.dir, t0, t1, RUNS / "power_all.jsonl")
        text = "".join(out_lines)
        row = {**self.base, "run_id": self.run_id, "started_at": started, "wall_s": round(t1 - t0, 1), "machine": machine_label(),
               "gpu_budget_gb": gpu_budget_gb(), "cmd": " ".join(map(str, self.cmd)),
               "gpu_mem_peak_gb": w["gpu_mem_in_use_gb_max"], "gpu_mem_baseline_gb": baseline,
               "peak_mem_gb": round(w["gpu_mem_in_use_gb_max"] - baseline, 2) if w["gpu_mem_in_use_gb_max"] and baseline is not None else None,
               "peak_mem_source": "GPU allocated memory (IOAccelerator): max during job minus idle baseline",
               **{k: w[k] for k in ("mem_pressure_max", "swap_used_gb_max", "gpu_active_pct_avg", "gpu_power_w_avg",
                                    "package_power_w_avg", "energy_wh", "gpu_energy_wh", "power_samples",
                                    "mem_used_gb_max", "package_power_w_max", "gpu_power_w_max", "thermal_state_max",
                                    "thermal_pressure_max", "gpu_freq_mhz_avg", "gpu_freq_mhz_min", "cpu_p_freq_mhz_avg", "soc_temp_max_c", "soc_temp_avg_c")}}
        r = getattr(self, "result", None)
        if r:
            row.update({k: r[k] for k in ("load_s", "audio_s", "rtf") if k in r})
            if r.get("device_peak_gb"):
                row["peak_mem_gb"] = r["device_peak_gb"]
                row["peak_mem_source"] = "torch.mps.driver_allocated_memory" if "torch" in r.get("engine", "") else "mx.get_peak_memory()"
            row["notes"] = (row.get("notes", "") + f"; gen {r.get('gen_s')}s for {r.get('audio_s')}s audio, {r.get('words')} words, "
                            f"{r.get('chunks')} chunks; device peak {r.get('device_peak_gb')} GB ({'torch MPS driver' if 'torch' in r.get('engine','') else 'mx.get_peak_memory'})").strip("; ")
            (ev.dir / "result.json").write_text(json.dumps(r, indent=1))
        loads = LOAD_RE.findall(text)
        if loads:
            row["load_s"] = round(sum(float(s) for _, s in loads), 1)
        if step_times:
            row["s_per_step"] = round(sps or sum(step_times) / len(step_times), 2)
            row["steps"] = len(step_times)
        if code == 0:
            row["fit"] = "yes"
            ev.emit("phase", phase="done")
            ev.emit("run_end", run_id=self.run_id, fit="yes", s_per_step=row.get("s_per_step"), wall_s=row["wall_s"],
                    peak_mem_gb=row["peak_mem_gb"], energy_wh=row["energy_wh"], mem_used_gb_max=row["mem_used_gb_max"],
                    package_power_w_max=row["package_power_w_max"], gpu_power_w_max=row["gpu_power_w_max"], kind="media", **self.base)
        else:
            reason = (f"did not fit: {tripped['reason']} (watchdog)" if tripped else
                      next((f"did not fit: {l.strip()[:200]}" for l in out_lines[::-1] if re.search(r"out of memory|Insufficient Memory|malloc|bad_alloc", l, re.I)), None)
                      or f"error: exit {code}: {out_lines[-1].strip()[:200] if out_lines else ''}")
            row.update(fit="no" if reason.startswith("did not fit") else "error", failure_reason=reason)
            ev.emit("run_fail", run_id=self.run_id, reason=reason, kind="media", **self.base)
        if row["fit"] != "error":
            append_csv(MEDIA, COLUMNS, row)
        else:
            with open(ROOT / "results/errors.jsonl", "a") as f:
                f.write(json.dumps({"t": now(), "run_id": self.run_id, "reason": row["failure_reason"]}) + "\n")
        log(f"  {self.run_id}: fit={row['fit']} wall {row['wall_s']}s s/step {row.get('s_per_step')} peak {row['peak_mem_gb']} GB {row.get('failure_reason','')}")
        return row


def h3_jobs(m, args, load_condition):
    """Tonight's queue from manifest.yaml media.h3.queue (or --builds/--settings filters over it)."""
    from ..runners.adapters import snapshot
    h3 = m["media"]["h3"]
    ps = prompts()
    builds = {b["id"]: b for b in h3["builds"]}
    jobs = []
    for q in h3["queue"]:
        b, key = builds[q["build"]], q["setting"]
        if args.builds and b["id"] not in args.builds.split(","):
            continue
        if args.settings and key not in args.settings.split(","):
            continue
        from ..verify import check
        ok_w, why = check(b["repo"], b.get("revision"))
        st = h3["settings"][key]
        if not ok_w:
            jobs.append(("pending", f"s2-{load_condition}-{b['id']}-{key}", f"unverified weights: {why}"))
            continue
        if args.smoke:                                   # plumbing check only: tiny canvas, 2 steps, rows tagged -smoke
            st = {**st, "width": 320, "height": 192, "steps": 2}
        fast = str(q.get("fast", "default"))
        for c in range(args.clips or h3["clips_per_setting"]):
            seed = h3["seeds"][c % len(h3["seeds"])]
            pid = c % len(ps)
            kf = ROOT / h3["keyframe"].format(width=st["width"], height=st["height"]) if h3.get("keyframe") else None
            ane = bool(q.get("ane"))
            tag = "" if b["id"] != "h3-mlxserve-4bit" else ("-fast" if fast == "default" else "-full") + ("-ane" if ane else "")
            rid = f"s2-{load_condition}-{b['id']}{tag}-{key}-{st['width']}x{st['height']}-{st['seconds']}s-{st['steps']}st-c{c + 1}"
            rid += "-smoke" if args.smoke else ""
            out = ROOT / "results/clips" / f"{rid}.mp4"
            base = {"set": "2", "load_condition": load_condition, "job": "b-roll", "model": "MiniMax H3", "quant": "MLX 4-bit",
                    "width": st["width"], "height": st["height"], "seconds": st["seconds"], "steps": st["steps"],
                    "lora": "turbo-4step" if st["lora"] else "", "seed": seed, "prompt_id": pid + 1, "output_path": str(out),
                    "notes": f"setting {key}; prompt {pid + 1}; logo keyframe first+last" + (f"; mlx-serve fast={fast}" if tag else "")}
            if b["id"] == "h3-pipenetwork-4bit":
                cmd = [str(_path(b["python"])), "scripts/generate.py", ps[pid], "-o", str(out),
                       "-c", str(_path(b["checkpoint_root"])), "-t", str(snapshot(b["repo"], b.get("revision"))), "-d", str(st["seconds"]),
                       "-s", str(st["steps"] + 1),            # sigma grid points: N points drive N-1 forwards
                       "--seed", str(seed), "--width", str(st["width"]), "--height", str(st["height"])]
                if kf:
                    cmd += ["--image", str(kf), "--anchor", "first", "--image", str(kf), "--anchor", "last"]
                base.update(runner="PipeNetwork minimax_h3_mlx", runner_version="PipeNetwork minimax_h3_mlx port, full compute")
                jobs.append(Job(rid, base, cmd, _path(b["runtime_dir"]), env={"PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1"},
                                steps=st["steps"], clip=c + 1, clips=1))
            else:
                cmd = [str(ROOT / ".venv/bin/python"), "-I", str(ROOT / "bench/media/mlxserve_job.py"), "--pack", str(snapshot(b["repo"], b.get("revision"))),
                       "--prompt", ps[pid], "--width", str(st["width"]), "--height", str(st["height"]), "--seconds", str(st["seconds"]),
                       "--steps", str(st["steps"]), "--seed", str(seed), "--out", str(out), "--fast", fast]
                if kf:
                    cmd += ["--keyframe", str(kf)]
                if st["lora"]:
                    cmd += ["--turbo"]
                if ane:
                    cmd += ["--ane"]
                    base["notes"] += "; --ane-video (Neural Engine beside the GPU, lossy int8/fp16)"
                base.update(runner="mlx-serve + ANE" if ane else "mlx-serve",
                            runner_version=f"mlx-serve 26.10.1 (mlx 0.32.3), fast={fast}" + (", turbo LoRA" if st["lora"] else "") + (", --ane-video" if ane else ""))
                jobs.append(Job(rid, base, cmd, ROOT, steps=st["steps"], clip=c + 1, clips=1))
    return jobs


def _path(x):
    return Path(x) if str(x).startswith("/") else ROOT / x


def voice_jobs(m, args, load_condition):
    """One narration per engine × reference. Everything comes from the manifest:
    engines [{id, name, kind, repo, revision, python, deps}], references [{id, file, text}] (a voice YOU may clone)."""
    from ..runners.adapters import snapshot
    from ..verify import check
    v = m["media"]["voice"]
    scene = (Path(args.scene).resolve() if args.scene else _path(v["script"]))
    if not scene.exists():
        sys.exit(f"{scene} is missing (one scene of ~150-200 words to narrate)")
    refs = [r for r in v.get("references") or [] if isinstance(r, dict)]
    if not refs:
        sys.exit("media.voice.references is empty: add {id, file, text} for a reference voice you have the right to use")
    jobs = []
    for e in v["engines"]:
        if args.builds and e["id"] not in args.builds.split(","):
            continue
        tag = "dry" if args.scene else "scene"
        gate = [check(e["repo"], e.get("revision"))] + [check(d["repo"], d.get("revision")) for d in e.get("deps", [])]
        bad = [why for ok, why in gate if not ok]
        for r in refs:
            rid = f"s2-{load_condition}-voice-{e['id']}-{r['id']}-{tag}"
            if bad:
                jobs.append(("pending", rid, f"unverified weights: {bad[0]}"))
                continue
            out = ROOT / "results/voice" / f"{rid}.wav"
            out.parent.mkdir(parents=True, exist_ok=True)
            cmd = [str(_path(e["python"])), str(ROOT / "bench/media/voice_job.py"), "--kind", e["kind"],
                   "--model-path", str(snapshot(e["repo"], e.get("revision"))), "--ref", str(_path(r["file"])),
                   "--ref-text", r.get("text", ""), "--scene", str(scene), "--out", str(out), "--label", e["name"]]
            base = {"set": "2", "load_condition": load_condition, "job": "narration", "model": e["name"], "quant": "as published",
                    "runner": "PyTorch/MPS" if e["kind"] == "chatterbox-torch" else "MLX (mlx-audio)", "runner_version": e["repo"],
                    "output_path": str(out), "notes": f"reference {r['id']}; scene {scene.name}"}
            # HF offline: a missing file must fail loudly, never download silently mid-benchmark.
            jobs.append(Job(rid, base, cmd, ROOT, env={"HF_HUB_OFFLINE": "1"}, clip=1, clips=1))
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", help="override inputs/scene.txt (dry runs)")
    ap.add_argument("--jobs", required=True, choices=["h3", "voice", "whisper", "ltx"])
    ap.add_argument("--load-condition", default="clean", choices=["clean", "everyday", "concurrent"])
    ap.add_argument("--builds"); ap.add_argument("--settings"); ap.add_argument("--clips", type=int)
    ap.add_argument("--smoke", action="store_true", help="tiny canvas, 2 steps: plumbing check, rows tagged -smoke")
    a = ap.parse_args()
    from .. import refuse_root, exit_on_signals, validate_manifest, run_hook
    refuse_root()
    exit_on_signals()
    run_hook("pre_run")
    acquire_bench_lock(f"media {a.jobs} {a.load_condition}")
    m = load_manifest()
    validate_manifest(m)
    (ROOT / "results/clips").mkdir(parents=True, exist_ok=True)
    jobs = {"h3": h3_jobs, "voice": voice_jobs}.get(a.jobs, lambda *x: sys.exit(f"--jobs {a.jobs}: adapter not built yet"))(m, a, a.load_condition)
    done = done_ids()
    with open(ROOT / "results/conditions.jsonl", "a") as f:
        f.write(json.dumps({"t": now(), "set": "2", "jobs": a.jobs, "load_condition": a.load_condition, "foreground_apps": foreground_apps() if m.get("record_foreground_apps") else ["(not recorded)"]}) + "\n")
    sampler = (subprocess.Popen([str(SAMPLER_BIN)], stdout=open(ROOT / "results/sampler.log", "ab"), stderr=subprocess.STDOUT, start_new_session=True)
               if SAMPLER_BIN.exists() else None)
    try:
        total = len(jobs)
        for i, j in enumerate(jobs):
            if isinstance(j, tuple):
                log(f"  {j[1]}: skipped — {j[2]}")
                continue
            if j.run_id in done:
                continue
            wait_until_ok(log)
            row = j.run(i + 1, total)
            if row.get("fit") == "yes" and a.jobs in ("h3", "ltx"):
                append_csv(KEEP, ["run_id", "path", "model", "settings", "seed", "prompt_id", "keep"],
                           {"run_id": j.run_id, "path": row["output_path"], "model": row["model"] + " " + row["runner"],
                            "settings": f"{row['width']}x{row['height']} {row['seconds']}s {row['steps']} steps {row['lora']}".strip(),
                            "seed": row["seed"], "prompt_id": row["prompt_id"], "keep": ""})
        EventWriter(f"_queue_end_{time.strftime('%Y%m%d-%H%M%S')}").emit("queue_end")
    finally:
        if sampler:
            sampler.terminate()


if __name__ == "__main__":
    main()
