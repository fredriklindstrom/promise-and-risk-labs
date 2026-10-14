"""Build results/report.md from the CSVs only (summary.csv, media.csv, mcm/snapshots.jsonl).

    python -m bench.report            # full report
    python -m bench.report --morning  # short overnight summary -> results/morning_<date>.md

Medians with the range of the reps; every number in "Facts for the script" carries its run IDs.
"""
import argparse, csv, datetime, json, re, statistics
from collections import defaultdict
from pathlib import Path
from . import ROOT

R = ROOT / "results"


def cell(x):
    """Free text into a Markdown table cell: no pipes, no HTML, one line (security review 2026-10-13)."""
    return str(x).replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def rows(name):
    p = R / name
    return list(csv.DictReader(open(p))) if p.exists() else []


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def med(vals, nd=1):
    v = [x for x in vals if x is not None]
    if not v:
        return "—"
    m = statistics.median(v)
    return f"{m:.{nd}f}" if len(v) == 1 or min(v) == max(v) else f"{m:.{nd}f} ({min(v):.{nd}f}–{max(v):.{nd}f})"


def ctxk(c):
    c = int(c)
    return f"{c // 1024}K" if c < 1048576 else f"{c // 1048576}M"


def thermal_cell(rs):
    """Worst macOS thermal state across the reps, then GPU clock avg (min) from powermetrics — the throttling evidence."""
    rank = {"nominal": 0, "fair": 1, "moderate": 1, "serious": 2, "heavy": 2, "critical": 3}
    states = [r.get("thermal_state_max") for r in rs if r.get("thermal_state_max")]
    worst = max(states, key=lambda x: rank.get(x, 0)) if states else "—"
    avgs = [f(r.get("gpu_freq_mhz_avg")) for r in rs if r.get("gpu_freq_mhz_avg")]
    mins = [f(r.get("gpu_freq_mhz_min")) for r in rs if r.get("gpu_freq_mhz_min")]
    clock = f" · {statistics.median(avgs):.0f} ({min(mins):.0f}) MHz" if avgs and mins else ""
    temps = [f(r.get("soc_temp_max_c")) for r in rs if r.get("soc_temp_max_c")]
    return worst + clock + (f" · die max {max(temps):.0f} °C" if temps else "")


def set1(summary):
    out, facts = [], []
    speed = [r for r in summary if r["kind"] == "speed" and r["set"] == "1"]
    by_model = defaultdict(list)
    for r in speed:
        by_model[r["model"]].append(r)
    out.append("## Test set 1: language models\n")
    for model, rs in by_model.items():
        out.append(f"### {model}\n")
        out.append("| Runner | Quant | Context | Decode tok/s | Prefill tok/s | TTFT s | Peak GB | System mem peak GB | Avg / peak W (package) | Thermal worst · GPU MHz avg (min) · die max °C | Load s | Fit | Reps | Load condition |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        cells = defaultdict(list)
        for r in rs:
            cells[(r["load_condition"], r["runner"], r["quant"], int(r["context_tokens"]))].append(r)
        for (lc, runner, quant, ctx), cr in sorted(cells.items(), key=lambda k: (k[0][0], k[0][1], k[0][2], k[0][3])):
            ok = [r for r in cr if r["fit"] == "yes"]
            fit = "yes" if ok and len(ok) == len(cr) else (cell(cr[0]["fit"] + ": " + cr[0]["failure_reason"][:80]) if not ok else f"{len(ok)}/{len(cr)}")
            dec = med([f(r["decode_tps"]) for r in ok], 2)
            out.append(f"| {runner} | {quant} | {ctxk(ctx)} | {dec} | {med([f(r['prefill_tps']) for r in ok])} | "
                       f"{med([f(r['ttft_s']) for r in ok], 2)} | {med([f(r['peak_mem_gb']) for r in ok])} | {med([f(r.get('mem_used_gb_max')) for r in ok])} | "
                       f"{med([f(r.get('package_power_w_avg')) for r in ok], 0)} / {med([f(r.get('package_power_w_max')) for r in ok], 0)} | {thermal_cell(ok)} | {med([f(r['load_s']) for r in ok])} | "
                       f"{fit} | {len(ok)} | {lc} |")
            ids = ", ".join(r["run_id"] for r in cr)
            if ok:
                facts.append(f"- {model}, {runner} {quant}, {ctxk(ctx)} ({lc}): decode {dec} tok/s, TTFT {med([f(r['ttft_s']) for r in ok], 2)} s, "
                             f"peak {med([f(r['peak_mem_gb']) for r in ok])} GB — `{ids}`")
            else:
                why = cr[0]["failure_reason"]
                verb = "does not fit" if any(w in why.lower() for w in ("fit", "memory", "507")) else "did not complete"
                facts.append(f"- {model}, {runner} {quant}, {ctxk(ctx)} ({lc}): {verb} ({cell(why[:120])}) — `{ids}`")
        out.append("")
    needle = [r for r in summary if r["kind"] == "needle" and r["set"] == "1"]
    if needle:
        out.append("### Retrieval (needle)\n")
        out.append("| Model | Runner | Quant | Context | 0% | 25% | 50% | 75% | 100% |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        g = defaultdict(lambda: defaultdict(list))
        for r in needle:
            g[(r["model"], r["runner"], r["quant"], int(r["context_tokens"]))][r["needle_depth"]].append(r)
        for k, d in g.items():
            cells = []
            for depth in ["0", "25", "50", "75", "100"]:
                rs = d.get(depth, [])
                if not rs:
                    cells.append("—")
                elif any(r["fit"] != "yes" for r in rs):
                    cells.append("no fit")
                else:
                    cells.append(f"{sum(r['needle_correct'] == 'True' for r in rs)}/{len(rs)}")
            out.append(f"| {k[0]} | {k[1]} | {k[2]} | {ctxk(k[3])} | " + " | ".join(cells) + " |")
        out.append("\nExact match on the final answer; raw answers in `runs/<run_id>/output.txt`.\n")
    q = [r for r in speed if r["model"].startswith("Qwen 3.8 27B") and r["load_condition"] == "clean"]
    if q:
        out.append("### Runner comparison: Qwen 3.8 27B, 1K context\n")
        out.append("| Runner | Quant | Decode tok/s | Prefill tok/s | TTFT s | Peak GB |")
        out.append("|---|---|---|---|---|---|")
        g = defaultdict(list)
        for r in q:
            if int(r["context_tokens"]) == 1024 and r["fit"] == "yes":
                g[(r["runner"], r["quant"])].append(r)
        for (runner, quant), rs in sorted(g.items(), key=lambda kv: -statistics.median([f(r["decode_tps"]) or 0 for r in kv[1]])):
            out.append(f"| {runner} | {quant} | {med([f(r['decode_tps']) for r in rs], 2)} | {med([f(r['prefill_tps']) for r in rs])} | "
                       f"{med([f(r['ttft_s']) for r in rs], 2)} | {med([f(r['peak_mem_gb']) for r in rs])} |")
        out.append("\nMatched quantization levels, not identical files (MLX 4-bit, GGUF Q4_K_M, Ollama NVFP4 and the MTPLX pack differ). "
                   "Splash: not supported on M2 Max (requires M3 or newer).\n")
    return out, facts


def forward_profile(r):
    """From the clip's own mlx-serve log: (label, seconds per FULL forward). mlx-serve fast mode mixes full forwards,
    partial ones (attention broadcast from an earlier step, ~1/5 the cost) and cached-velocity steps (~0 s).
    Full-compute runs (PipeNetwork, fast=false, turbo) are all full."""
    import re
    op = r.get("output_path") or ""
    log = Path(op).with_suffix(".mlxserve.log") if op else None
    if log is None or not log.exists():
        n = r.get("steps") or "?"
        return f"{n} full", f(r.get("s_per_step"))
    t = log.read_text(errors="replace")
    ms = [float(x) for x in re.findall(r"step \d+/\d+ sigma [\d.]+ \(([\d.]+) ms\)", t)]
    cached = len(re.findall(r"step \d+/\d+ sigma [\d.]+ \(cached velocity", t))
    if not ms:
        return "—", None
    top = max(ms)
    full = [x for x in ms if x >= 0.6 * top]
    partial = len(ms) - len(full)
    parts = [f"{len(full)} full"] + ([f"{partial} partial"] if partial else []) + ([f"{cached} cached"] if cached else [])
    return " + ".join(parts) + f" of {len(ms) + cached}", round(sum(full) / len(full) / 1000, 1)


def media(mrows):
    out, facts = [], []
    mrows = [r for r in mrows if not r["run_id"].endswith(("-dry", "-smoke"))]
    if not mrows:
        return out, facts
    out.append("## Test set 2: production stack\n")
    h3 = [r for r in mrows if r["job"] == "b-roll"]
    if h3:
        keep = {r["run_id"]: r["keep"].strip().lower() for r in rows("keep_review.csv")}
        out.append("| Model / runtime | Setting | Clips | Min per clip | Forwards | s per full forward | Wh per clip | Peak W (package) | System mem peak GB | Kept | Min per usable clip | Hours / episode (10 / 15 clips) |")
        out.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        g = defaultdict(list)
        for r in h3:
            mode = re.search(r"fast=(\w+)", r.get("runner_version", ""))
            mode = ("fast mode" if mode.group(1) == "default" else "full compute") if mode else "full compute"
            if r.get("lora"):
                mode = "Turbo LoRA, no step cache"          # mlx-serve turns its step cache off under turbo (log: step-cache 0.000)
            mode += " + ANE" if "--ane-video" in r.get("runner_version", "") else ""
            g[(r["model"], f"{r['runner'].replace(' + ANE', '')} ({mode})", r["width"], r["height"], r["seconds"], r["steps"], r["lora"], r["load_condition"])].append(r)
        for k, rs in g.items():
            ok = [r for r in rs if r["fit"] == "yes"]
            mins = [f(r["wall_s"]) / 60 for r in ok if f(r["wall_s"])]
            kept = [r for r in ok if keep.get(r["run_id"]) in ("y", "yes", "1", "x", "keep")]
            marked = [r for r in ok if keep.get(r["run_id"])]
            if mins and marked:
                per_usable = sum(mins) / max(len(kept), 1e-9) if kept else None
                eps = f"{per_usable * 10 / 60:.1f} / {per_usable * 15 / 60:.1f}" if per_usable else "no usable clips"
                kept_s = f"{len(kept)}/{len(marked)}"
                pu = f"{per_usable:.0f}" if per_usable else "—"
            else:
                eps, kept_s, pu = "keep column not marked yet", "—", "—"
            label = f"{k[2]}×{k[3]} {k[4]}s {k[5]} steps{' + ' + k[6] if k[6] else ''} ({k[7]})"
            out.append(f"| {k[0]} · {k[1]} | {label} | {len(ok)}/{len(rs)} | {med(mins)} | {'; '.join(forward_profile(r)[0] for r in ok) or '—'} | {med([forward_profile(r)[1] for r in ok])} | "
                       f"{med([f(r['energy_wh']) for r in ok])} | {med([f(r.get('package_power_w_max')) for r in ok], 0)} | {med([f(r.get('mem_used_gb_max')) for r in ok])} | {kept_s} | {pu} | {eps} |")
            facts.append(f"- H3 {k[1]} {label}: {med(mins)} min per clip — `{', '.join(r['run_id'] for r in rs)}`")
        out.append("\nOne night = 8 hours. Hours per episode = minutes per usable clip × clips ÷ 60. mlx-serve's default fast mode runs a full "
                   "transformer pass on only some steps, a partial pass (attention reused) on others and a cached velocity on the rest, so compare "
                   "builds on seconds per FULL forward and on minutes per clip, never on an average over all scheduled steps.\n")
        cc = _report_cfg().get("cloud_comparison_csv")
        hg = rows(Path(cc).name) if cc else []
        hg = [r for r in hg if r.get("wall_min")]
        if hg:
            out.append("### Same prompt, same keyframe: local vs cloud\n")
            out.append("| Where | Mode | Minutes to a finished clip | Credits | Resolution | Keep |")
            out.append("|---|---|---|---|---|---|")
            for k, rs in g.items():
                ok = [r for r in rs if r["fit"] == "yes"]
                if ok:
                    out.append(f"| Mac Studio M2 Max (local) | H3 {k[1]}, {k[2]}×{k[3]}, {k[5]} steps{' + ' + k[6] if k[6] else ''} | {med([f(r['wall_s']) / 60 for r in ok])} | 0 | {k[2]}×{k[3]} | "
                               f"{', '.join(keep.get(r['run_id'], '') or '—' for r in ok)} |")
            for r in hg:
                out.append(f"| {cell(r['service'])} (cloud) | {cell(r['mode'])} | {cell(r['wall_min'])} | {cell(r['credits_used'] or '—')} | {cell(r['resolution'] or '—')} | {cell(r['keep'] or '—')} |")
            out.append(f"\nCloud times are submit-to-download wall clock, logged by hand in `{cell(cc)}`.\n")
    v = [r for r in mrows if r["job"] == "narration"]
    if v:
        out.append("### Narration\n\n| Engine | Runner | Reference | Audio s | Wall s | RTF | Peak GB | Peak W | Wh |\n|---|---|---|---|---|---|---|---|---|")
        for r in v:
            ref = (r.get("notes") or "").split(";")[0].replace("reference ", "") or \
                next((x for x in ("refA", "refB") if f"-{x}-" in r["run_id"]), "—")
            out.append(f"| {r['model']} | {r['runner']} | {ref} | {r['audio_s']} | {r['wall_s']} | "
                       f"{r['rtf']} | {r['peak_mem_gb']} | {r.get('package_power_w_max') or '—'} | {r['energy_wh'] or '—'} |")
            facts.append(f"- {r['model']}: real-time factor {r['rtf']} — `{r['run_id']}`")
        out.append("")
    return out, facts


def compare_machines():
    """Same variant ids measured on other Macs (manifest report.compare: [{label, dir, sets: {set: caption}}])."""
    cfg = _report_cfg().get("compare") or []
    if not cfg:
        return []
    here = {r["variant_id"]: r for r in rows("summary.csv") if r.get("variant_id") and r["set"] == "1" and r["kind"] == "speed"}
    out = ["## Same test, other Macs\n"]
    for c in cfg:
        d = (ROOT / c["dir"]).resolve()
        if not d.is_relative_to(ROOT.resolve()) or not (d / "summary.csv").exists():
            continue
        other = list(csv.DictReader(open(d / "summary.csv")))   # another machine's results: every value is escaped
        sets = c.get("sets") or {"1": "run"}
        machine = next((r.get("machine") for r in other if r.get("machine")), c["label"])
        out.append(f"**{cell(c['label'])}** ({cell(machine)}) against this machine, decode tokens/sec, 1K context:\n")
        out.append("| Variant | This machine | " + " | ".join(cell(v) for v in sets.values()) + " | Other machine run IDs |")
        out.append("|---|---|" + "---|" * len(sets) + "---|")
        for vid in sorted({r["variant_id"] for r in other if r.get("variant_id")}):
            mine = here.get(vid)
            cells, ids = [], []
            for k in sets:
                r = next((x for x in other if x["variant_id"] == vid and x["set"] == k and x["fit"] == "yes"), None)
                cells.append(f"{f(r['decode_tps']):.1f}" if r and f(r["decode_tps"]) is not None else "—")
                if r: ids.append(cell(r["run_id"]).replace("`", ""))
            here_tps = f(mine["decode_tps"]) if mine else None
            out.append(f"| {cell(vid)} | {f'{here_tps:.1f}' if here_tps is not None else '—'} | "
                       + " | ".join(cells) + f" | `{', '.join(ids)}` |")
        if (d / "media.csv").exists():
            mine_m = {r["run_id"]: r for r in rows("media.csv")}
            mrows = [r for r in csv.DictReader(open(d / "media.csv")) if r["job"] == "narration"]
            if mrows:
                out.append("\n| Voice engine | Real-time factor here | there | Peak GB there | Fit there |\n|---|---|---|---|---|")
                for r in mrows:
                    out.append(f"| {cell(r['model'])} | {cell(mine_m.get(r['run_id'], {}).get('rtf', '—'))} | {cell(r['rtf'])} | {cell(r['peak_mem_gb'])} | {cell(r['fit'])} |")
        if c.get("note"):
            out.append(f"\n{cell(c['note'])}\n")
        out.append("")
    return out


def mcm():
    p = R / "mcm/snapshots.jsonl"
    if not p.exists():
        return []
    out = ["## Model Cache Manager snapshots\n", "| Label | When | MCM total | du HF cache GB | du Ollama GB | Internal free GB |", "|---|---|---|---|---|---|"]
    for l in p.read_text().splitlines():
        s = json.loads(l)
        out.append(f"| {s['label']} | {s['t'][:16]} | {s['mcm_total_line']} | {s['du_hf_hub_gb']} | {s['du_ollama_gb'] or '—'} | {s['disk']['internal_free_gb']} |")
    out.append("\nOllama's store sits outside the HF cache; the Ollama-aware Model Cache Manager build lists it.\n")
    return out


def set1_notes(summary):
    """Test set 1 runs that did not finish or left blanks, with the error from the logs (batch 1 follow-up, item 5)."""
    out = []
    done = {r["run_id"]: r for r in summary}
    errs = {}
    if (R / "errors.jsonl").exists():
        for l in (R / "errors.jsonl").read_text().splitlines():
            d = json.loads(l)
            errs.setdefault(d["run_id"], []).append(d)
    groups = defaultdict(list)
    for rid in errs:
        if rid.startswith("s1-") and rid not in done:
            groups[re.sub(r"-(r\d+|needle-d\d+-t\d+)$", lambda m: "-needle" if "needle" in m.group(0) else "", rid)].append(rid)
    for g, rids in sorted(groups.items()):
        metal = None
        for d in sorted((ROOT / "runs").glob(g.replace("-needle", "") + "*")):
            lg = d / "runner.log"
            if lg.exists():
                hit = [l.strip() for l in lg.read_text(errors="replace").splitlines() if "[METAL]" in l]
                if hit:
                    metal = (hit[-1], f"runs/{d.name}/runner.log")
                    break
        what = g.replace("s1-clean-", "").replace("-needle", " needle") + " (" + str(len(rids)) + " runs)"
        out.append(f"- **{cell(what)}:** the MLX worker exited" +
                   (f" with `{cell(metal[0][:150])}` (`{metal[1]}`)." if metal else "; no error line in the logs."))
    fn = [r for r in summary if str(r.get("variant_id", "")).startswith("flashnext-omlx4") and r["fit"] == "no"]
    if fn:
        label = {"flashnext-omlx4": "balanced guard", "flashnext-omlx4-aggressive": "aggressive guard",
                 "flashnext-omlx4-custom80": "custom 80 GB ceiling",
                 "flashnext-omlx4-raised86": "kernel GPU limit raised to 91 GB, ceiling 86 GB"}
        tries = {}
        for rid, ds in errs.items():
            for d in ds:
                v = d.get("variant") or ""
                if not v.startswith("flashnext-omlx4"):
                    continue
                m = re.search(r"\((\d+\.\d+)GB\) does not fit under the dynamic memory ceiling \((\d+\.\d+)GB\)", d["reason"])
                k = re.search(r"projected memory (\d+\.\d+)GB would exceed the metal_cap memory ceiling (\d+\.\d+)GB", d["reason"])
                if m or k:
                    key = (d["t"][:13], v)
                    tries.setdefault(key, []).append((float((m or k).group(1)), float((m or k).group(2)), "Metal cap" if k else "dynamic ceiling"))
        parts = []
        for (t, v), xs in sorted(tries.items()):
            size, kind = xs[0][0], xs[0][2]
            lo, hi = min(x[1] for x in xs), max(x[1] for x in xs)
            ple = "lookup table on SSD, " if size < 100 else ""
            parts.append(f"{t[:10]} {t[11:13]}:00 UTC ({ple}{label.get(v, v)}): {size:.2f} GB vs {kind} " + (f"{lo:.2f}–{hi:.2f} GB" if lo != hi else f"{lo:.2f} GB"))
        out.append("- **Qwen3.8-Flash-Next on oMLX: does not fit on 96 GB.** Every attempt was refused with HTTP 507 before loading: " +
                   "; ".join(parts) + ". Putting the n-gram table on the SSD (`qwen4_ple_ssd_offload`) cut oMLX's size estimate from "
                   "109.05 to 77.76 GB. The aggressive guard still left its dynamic ceiling 0.1–0.4 GB short, and a fixed 80 GB ceiling "
                   "ran into the GPU's own limit (Metal cap). Closing every app (the 17:00 UTC attempt) did not raise the ceiling: oMLX "
                   "counts free and inactive memory only. The one remaining lever, raising "
                   "`iogpu.wired_limit_mb`, is ruled out for this test. Error text in `results/errors.jsonl`.")
    blank = defaultdict(list)
    for r in summary:
        if r["set"] == "1" and r["fit"] == "yes" and r.get("kind") == "speed" and not r.get("ttft_s") and "needle" not in r["run_id"]:
            blank[(r["model"], r["runner"], r["quant"], int(r["context_tokens"]))].append(r["run_id"])
    for (model, runner, quant, ctx), rids in sorted(blank.items()):
        why = []
        for rid in rids:
            p = ROOT / "runs" / rid / "result.json"
            if p.exists():
                j = json.loads(p.read_text())
                raw = j.get("raw") or {}
                if runner == "Ollama" and raw.get("eval_count") is None:
                    why.append(f"client-side TTFT {float(j.get('client_ttft_s') or 0):.1f} s, answer {len((ROOT / 'runs' / rid / 'output.txt').read_text()) if (ROOT / 'runs' / rid / 'output.txt').exists() else '?'} characters")
        if why:
            out.append(f"- **{cell(model)}, {cell(runner)} {cell(quant)}, {ctxk(ctx)}:** the answers streamed, but {cell(runner)} "
                       f"({cell(next(r['runner_version'] for r in summary if r['run_id'] == rids[0]))}) never sent its final statistics "
                       f"(`prompt_eval_count` and `eval_count` are empty in `runs/<run_id>/result.json`), so prefill, decode and TTFT can't "
                       f"be computed from the server's own numbers. A runner fault, not the harness, so not rerun. " + "; ".join(why) + ".")
    sup = R / "summary.mtplx-32k-empty-2026-10-11.csv"
    if sup.exists():
        re_rows = [r for r in summary if r["run_id"].startswith("s1-clean-qwen27b-mtplx-32k-") and r.get("decode_tps")]
        out.append("- **Qwen 3.8 27B, MTPLX, 32K:** the 2026-10-11 runs returned nothing: the harness set MTPLX's context window "
                   "to exactly 32K, and MTPLX logged `max_tokens clamped to remaining context` "
                   "(`runs/_superseded-mtplx-32k-2026-10-21/s1-clean-qwen27b-mtplx-32k-r1-warmup/runner.log`). " +
                   ("A rerun with the window 1,024 tokens larger no longer logged the clamp but still returned no text; MTPLX's "
                    "stream carried the reason, which the harness was dropping: `Blocked unsafe long-context MTP prefill path ... Start "
                    "MTPLX with --profile sustained` (`runs/mtplx-32k-debug/stream_head.txt`). The 32K row in the table is the "
                    "2026-10-22 rerun with `--profile sustained` (1K and 8K ran `--profile stable`, which MTPLX accepted at those lengths). "
                    "Its peak memory rose with each rep (" + ", ".join(f"{f(r['peak_mem_gb']):.1f}" for r in sorted(re_rows, key=lambda r: r['run_id'])) +
                    " GB), so read the peak column as the last rep's ceiling, not a steady state."
                    if re_rows else
                    "A rerun on 2026-10-21 with the window 1,024 tokens larger no longer logged the clamp, but still returned no text "
                    "(finish_reason `error`, `runs/_superseded-mtplx-32k-rerun-2026-10-21/`). The harness does not record MTPLX's error "
                    "text, so the cause is still open; the row stays blank."
                    if (R / "summary.mtplx-32k-empty-rerun-2026-10-21.csv").exists() else
                    "A rerun with the window 1,024 tokens larger is queued; the row stays blank until it lands."))
    if not out:
        return []
    return ["### Test set 1: runs that did not finish or left blanks\n", *out,
            "\nOn 96 GB, llama.cpp completed both 128K tests that MLX did not (gpt-oss-120b speed, Qwen 3.8 27B needle).\n"]


def kv_probe_facts():
    p = R / "kv_probe.jsonl"
    if not p.exists():
        return []
    last = {}
    for l in p.read_text().splitlines():
        d = json.loads(l)
        last[d["runner_key"]] = d
    m, ll = last.get("mlx"), last.get("llamacpp-load")
    if not (m and ll):
        return []
    gib = 1.073741824
    return [f"- Qwen 3.8 27B holding a 128K conversation: the cache itself is {m['conversation_cache_gb']:.2f} GB on MLX and "
            f"{(ll['kv_buffer_gib'] + ll['rs_buffer_gib']) * gib:.2f} GB on llama.cpp; MLX's {m['peak_while_reading_gb']:.1f} GB peak adds about "
            f"{m['peak_while_reading_gb'] - m['weights_gb'] - m['conversation_cache_gb']:.1f} GB of working memory on top of weights and cache "
            f"while the prompt is read. `results/kv_probe.jsonl` (runner_key mlx, llamacpp-load)."]


def kv_probe():
    """What the 128K memory numbers include (batch 1 follow-up, item 3), from results/kv_probe.jsonl."""
    p = R / "kv_probe.jsonl"
    if not p.exists():
        return []
    last = {}
    for l in p.read_text().splitlines():
        d = json.loads(l)
        last[d["runner_key"]] = d
    m, lc, ll = last.get("mlx"), last.get("llamacpp"), last.get("llamacpp-load")
    gib = 1.073741824
    out = ["### What the 128K memory numbers include: Qwen 3.8 27B\n",
           "One 128K read per runner with the set 1 prompt (`python -m bench.kv_probe`), recording the conversation cache "
           "separately from the peak while the prompt is read. GB = 10⁹ bytes.\n",
           "| Runner | Weights GB | Conversation cache GB | Peak while reading GB | Source |", "|---|---|---|---|---|"]
    if m:
        parts = ", ".join(f"{k} {v['layers']} layers {v['gb']:.2f}" for k, v in m["cache_by_type"].items())
        out.append(f"| MLX 4-bit | {m['weights_gb']:.2f} | {m['conversation_cache_gb']:.2f} ({cell(parts)}) | {m['peak_while_reading_gb']:.2f} "
                   f"| `runs/kv-probe-mlx/result.json` |")
    if lc:
        cache = f"{(ll['kv_buffer_gib'] + ll['rs_buffer_gib']) * gib:.2f} (KV {ll['kv_buffer_gib'] * gib:.2f}, recurrent state {ll['rs_buffer_gib'] * gib:.2f})" \
            if ll and ll.get("kv_buffer_gib") else "— (load-only probe pending)"
        wts = sum(float(x) for l in (ll or {}).get("log_lines", []) for x in re.findall(r"model buffer size\s*=\s*([\d.]+) MiB", l))
        wcell = f"{wts / 1024 * gib:.2f}" if wts else f"— (in the after-load figure: {lc['weights_and_buffers_after_load_gb']:.2f} GB with all buffers)"
        out.append(f"| llama.cpp GGUF Q4_K_M | {wcell} | {cache} | "
                   f"{lc['peak_while_reading_gb']:.2f} | `runs/kv-probe-llamacpp/result.json`" + (", `runs/kv-probe-llamacpp-load/server.log`" if ll else "") + " |")
    if m:
        scratch = m["peak_while_reading_gb"] - m["weights_gb"] - m["conversation_cache_gb"]
        line = (f"\nMLX's peak is weights + conversation cache + about {scratch:.1f} GB of working memory that exists only while the prompt "
                f"is being read (the process holds {m['active_after_read_gb']:.1f} GB once the read is done).")
        if lc:
            line += (f" llama.cpp reserves its cache and work buffers when it loads, so its peak while reading is only "
                     f"{lc['peak_while_reading_gb'] - lc['weights_and_buffers_after_load_gb']:.1f} GB above what it holds after loading.")
        out.append(line + " Both conversation caches are about the same size; the difference in the 128K peaks is working memory.\n")
    s1 = {}
    for r in rows("summary.csv"):
        for key, vid in (("mlx", "qwen27b-mlx4"), ("llamacpp", "qwen27b-q4km")):
            if r["run_id"].startswith(f"s1-clean-{vid}-128k-r") and f(r["peak_mem_gb"]) is not None:
                s1.setdefault(key, []).append(f(r["peak_mem_gb"]))
    cmp_ = [f"{name} {statistics.median(s1[k]):.1f} GB in set 1 vs {last[k]['peak_while_reading_gb']:.1f} GB here"
            for k, name in (("mlx", "MLX"), ("llamacpp", "llama.cpp")) if k in s1 and k in last]
    if cmp_:
        out.append("Same measures as set 1's peak column: " + "; ".join(cmp_) + ". Set 1's llama.cpp runs had no log at this "
                   "verbosity, so the buffer sizes above come from today's load.\n")
    return out


CACHE_COND = {"A": "Cache-friendly", "B": "Broken at the top", "C": "Broken mid-context", "D": "Thinking stripped"}
CACHE_LABEL = {  # variant_id -> how the runner reads in the report
    "qwen27b-mlx4": "mlx_lm.server · MLX 4-bit", "qwen27b-q4km": "llama-server · GGUF Q4_K_M",
    "qwen27b-ollama": "Ollama · NVFP4 (Ollama default)", "qwen27b-omlx": "oMLX · MLX 4-bit",
    "llama70b-q4km": "llama-server · GGUF Q4_K_M",
    "llama70b-mlx4-cap16-noseed": "mlx_lm.server · MLX 4-bit, prompt cache capped at 16 GB, no seed"}
# Runs with no rows in cache_test.csv, and why (the evidence is in the folders named).
CACHE_FAILED = [
    ("llama70b-mlx4", "Llama 3.3 70B on mlx_lm.server, default prompt cache: out of GPU memory "
     "(`[METAL] ... Insufficient Memory`) at turn 5 in A and turn 4 in B, all four runs. See `results/cache_test_mlx_default_oom.md`."),
    ("llama70b-mlx4-cap16", "Same with `--prompt-cache-bytes 16GB`: failed at the same turns. The harness sends a seed, and "
     "mlx_lm.server only applies the byte cap to requests without one (seeded requests take its single-request path)."),
]


def _int(x):
    v = f(x)
    return int(v) if v is not None else None


def _n(x):
    return "—" if x is None else f"{x:,}"


def cache_loop():
    """Agent-loop cache test (addendum 2026-10-14) from results/cache_test.csv only."""
    p = R / "cache_test.csv"
    if not p.exists():
        return [], []
    raw = list(csv.DictReader(open(p)))
    for i, r in enumerate(raw):
        r["_line"] = i + 2  # line in the CSV file, header = 1
    by = defaultdict(list)  # (variant, condition, turn) -> rows (one per rep)
    variants = []
    for r in raw:
        by[(r["variant_id"], r["condition"], int(r["turn"]))].append(r)
        if r["variant_id"] not in variants:
            variants.append(r["variant_id"])
    model_of = {r["variant_id"]: r["model"] for r in raw}
    turns_of = {v: max(t for (vv, _, t) in by if vv == v) for v in variants}
    conds_of = {v: sorted({c for (vv, c, _) in by if vv == v}) for v in variants}

    def ttft(v, c, t):
        return [f(r["ttft_s"]) for r in by.get((v, c, t), [])]

    def mttft(v, c, t):
        x = [y for y in ttft(v, c, t) if y is not None]
        return statistics.median(x) if x else None

    def mcached(v, c, t):
        x = [_int(r["cached_tokens"]) for r in by.get((v, c, t), []) if _int(r["cached_tokens"]) is not None]
        return int(statistics.median(x)) if x else None

    def mctx(v, c, t):
        x = [_int(r["context_tokens"]) for r in by.get((v, c, t), [])]
        return int(statistics.median(x)) if x else None

    def lines(v, c, t):
        return ", ".join(str(r["_line"]) for r in by.get((v, c, t), []))

    def src_lines(v, c, t):
        return f"`results/cache_test.csv` lines {lines(v, c, t)}"

    def runs(v, c):
        return sorted({r["run_id"] for (vv, cc, _), rs in by.items() if vv == v and cc == c for r in rs})

    def kept(v, c):
        t = turns_of[v]
        cached, ctx = mcached(v, c, t), mctx(v, c, t)
        if cached is None or not ctx:
            return "—"
        share = cached / ctx
        word = "kept" if share >= 0.9 else "partial" if share >= 0.05 else "re-read all"
        return f"{word}: {_n(cached)} of {_n(ctx)} ({share:.0%}), {len(runs(v, c))}/2 runs"

    def ratio(v):
        t = turns_of[v]
        a, b = mttft(v, "A", t), mttft(v, "B", t)
        return (a, b, b / a) if a and b else (a, b, None)

    out = ["## Agent loop: cache hit vs miss\n",
           "A scripted coding-agent session sent to each runner's server: a 33,000-token system prompt (tool descriptions plus "
           "CPython stdlib source, `inputs/agent_loop/`), then about 3,000 tokens of tool output per turn, 256 output tokens per turn, "
           "temperature 0. The script is `inputs/agent_loop/script.json`. Qwen 3.8 27B (hybrid attention, thinking on) runs 10 turns in "
           "four conditions; Llama 3.3 70B (standard attention) runs 6 turns in A and B only, as planned. Two runs per condition, server "
           "restarted for every run. TTFT is client-side, request sent to first streamed token. Cached tokens come from each server's "
           "own report (column `cached_source`), never from timing.\n",
           "| ID | Condition | What changes between turns |", "|---|---|---|",
           "| A | Cache-friendly | Append only |",
           "| B | Broken at the top | A short request ID at the very start of the system prompt changes every turn |",
           "| C | Broken mid-context | One line about halfway through the history is edited every turn |",
           "| D | Thinking stripped | Reasoning is removed from earlier assistant turns before resending |", ""]
    log = R / "cache_test.log"
    if log.exists():
        notes = [l for l in log.read_text().splitlines() if "apps open at start" in l or " quit by " in l]
        if notes:
            out.append("Recorded in `results/cache_test.log` (open apps were not logged for the 2026-10-18 batches):\n")
            out += [f"- {cell(l.strip())}" for l in notes]
            out.append("")

    out += ["### Headline: last turn, cache broken vs intact\n",
            "| Model | Runner | Turn | A: TTFT s · cached tokens | B: TTFT s · cached tokens | B ÷ A |", "|---|---|---|---|---|---|"]
    for v in variants:
        t = turns_of[v]
        a, b, x = ratio(v)
        out.append(f"| {model_of[v]} | {CACHE_LABEL.get(v, v)} | {t} | {med(ttft(v, 'A', t))} · {_n(mcached(v, 'A', t))} | "
                   f"{med(ttft(v, 'B', t))} · {_n(mcached(v, 'B', t))} | {f'{x:.1f}×' if x else '—'} |")
    out.append("\nMedian of two runs, range in brackets. Turn 10 for Qwen, turn 6 (the last) for Llama.\n")

    out += ["### What each runner kept at the last turn\n",
            "| Model | Runner | Version | A | B | C | D | Peak GB |", "|---|---|---|---|---|---|---|---|"]
    for v in variants:
        rs = [r for r in raw if r["variant_id"] == v]
        cells = [kept(v, c) if c in conds_of[v] else
                 ("not applicable (no thinking output)" if c == "D" and "Llama" in model_of[v] else "not run") for c in "ABCD"]
        peak = max((f(r["peak_mem_gb"]) or 0) for r in rs)
        out.append(f"| {model_of[v]} | {CACHE_LABEL.get(v, v)} | {cell(rs[0]['runner_version'])} | " + " | ".join(cells) + f" | {peak:.1f} |")
    out.append("\n\"kept\" = at least 90% of the prompt came from cache; \"partial\" = the server rewound to an earlier point; "
               "\"re-read all\" = under 5% cached.\n")

    # Hybrid Qwen under C and D, by runner, from the same numbers
    q = [v for v in variants if "Qwen" in model_of[v]]
    if q:
        out.append("### C and D on the hybrid model\n")
        for c in "CD":
            parts = []
            for v in q:
                t = turns_of[v]
                parts.append(f"{CACHE_LABEL.get(v, v).split(' · ')[0]} {med(ttft(v, c, t))} s with {_n(mcached(v, c, t))} cached")
            out.append(f"- **{c}, {CACHE_COND[c].lower()}, turn 10:** " + "; ".join(parts) + ".")
        out.append("- D does not apply to Llama 3.3 70B: it emits no separate reasoning to strip.\n")
    # C on both models, same runner and turn (batch 1 follow-up, item 1)
    lq, ql = "llama70b-q4km", "qwen27b-q4km"
    if (lq, "C", 6) in by and (ql, "C", 6) in by:
        out += ["### C on both models, llama-server, turn 6\n",
                "| Model | Attention | Context tokens | C: TTFT s | C: cached tokens | A: TTFT s | B: TTFT s | Source |", "|---|---|---|---|---|---|---|---|"]
        for v, att in ((ql, "hybrid"), (lq, "standard")):
            out.append(f"| {model_of[v]} | {att} | {_n(mctx(v, 'C', 6))} | {med(ttft(v, 'C', 6))} | {_n(mcached(v, 'C', 6))} | "
                       f"{med(ttft(v, 'A', 6))} | {med(ttft(v, 'B', 6))} | {src_lines(v, 'C', 6)} |")
        qc, lc = mcached(ql, "C", 6) or 0, mcached(lq, "C", 6) or 0
        out.append(f"\nWith one line edited about halfway back, Llama 3.3 70B kept the {_n(lc)} tokens before the edit "
                   f"({lc / mctx(lq, 'C', 6):.0%} of the prompt); Qwen 3.8 27B on the same server kept {_n(qc)}.\n")

    out.append("### Turn by turn\n")
    for v in variants:
        cs = conds_of[v]
        out.append(f"**{model_of[v]} · {CACHE_LABEL.get(v, v)}**: TTFT s, median (range) of two runs · cached tokens\n")
        out.append("| Turn | Context tokens | " + " | ".join(f"{c}: {CACHE_COND[c]}" for c in cs) + " |")
        out.append("|---|---|" + "---|" * len(cs))
        for t in range(1, turns_of[v] + 1):
            out.append(f"| {t} | {_n(mctx(v, 'A', t))} | " +
                       " | ".join(f"{med(ttft(v, c, t))} · {_n(mcached(v, c, t))}" for c in cs) + " |")
        out.append("")

    # Turns where A or D (which should keep the cache) reused no more than the turn before (both runs, so not noise)
    stalls = []
    for v in variants:
        for c in [c for c in conds_of[v] if c in "AD"]:
            for t in range(3, turns_of[v] + 1):
                now_, prev = mcached(v, c, t), mcached(v, c, t - 1)
                if prev and now_ is not None and now_ <= prev and (mcached(v, c, t - 2) or 0) < prev:
                    stalls.append(f"{CACHE_LABEL.get(v, v).split(' · ')[0]} {c} turn {t}: {_n(now_)} cached, the same as turn {t - 1}, "
                                  f"{med(ttft(v, c, t))} s against {med(ttft(v, c, t - 1))} s ({src_lines(v, c, t)})")
    if stalls:
        out.append("Turns where the cache stopped growing for one turn, in both runs: " + "; ".join(stalls) +
                   ". The server matched only up to the previous turn's prompt; the logs do not say why.\n")

    failed = []
    for vid, why in CACHE_FAILED:
        dirs = sorted(d.name for d in (ROOT / "runs").glob(f"ct-{vid}-[A-D]-r[0-9]") if (d / "failure.txt").exists())
        if dirs:
            failed.append(f"- **{len(dirs)} runs, `{vid}`:** {why} Folders: {', '.join(f'`runs/{d}/`' for d in dirs)}.")
    qd = ROOT / "runs/_quarantine-ollama-0.40.3"
    if qd.exists():
        n = len([d for d in qd.iterdir() if d.is_dir()])
        failed.append(f"- **{n} runs, Ollama 0.40.3:** the MLX runner crashed past ~33K tokens (`Maximum threads per threadgroup is 896 "
                      "but requested 960`, ollama/ollama#18940, fixed in 0.40.4). Rerun on 0.40.6; the crashed runs are kept in "
                      "`runs/_quarantine-ollama-0.40.3/` and `results/cache_test.ollama-0.40.3-crashed.csv`.")
    if failed:
        out += ["### Runs that did not finish\n", *failed, ""]
        # Turns the failed seeded runs did finish are only in the log; compare them with the no-seed runs on the same turns.
        seeded = defaultdict(list)
        if log.exists():
            for l in log.read_text().splitlines():
                m = re.search(r"ct-llama70b-mlx4(?:-cap16)?-A-r\d turn (\d+)/\d+: .*TTFT ([\d.]+)s", l)
                if m:
                    seeded[int(m.group(1))].append(float(m.group(2)))
        ns = "llama70b-mlx4-cap16-noseed"
        ts = [t for t in sorted(seeded) if t > 1 and (ns, "A", t) in by]
        if ts:
            sv = [x for t in ts for x in seeded[t]]
            nv = [x for t in ts for x in ttft(ns, "A", t) if x is not None]
            out.append(f"Leaving out the seed puts requests on the server's batched path, which is a little slower: in A, turns "
                       f"{ts[0]}–{ts[-1]} took {min(nv):.0f}–{max(nv):.0f} s without a seed against {min(sv):.0f}–{max(sv):.0f} s for "
                       "the seeded runs before they failed (same prompts; seeded turns from `results/cache_test.log`).\n")

    nf = R / "cache_test_notes.md"
    if nf.exists():
        rows_ = [l for l in nf.read_text().splitlines() if l.startswith("| 20")]
        if rows_:
            c = [x.strip() for x in rows_[-1].strip("|").split("|")]
            out += ["### Ollama's default context window\n",
                    f"With no `num_ctx`, {c[1]} loaded {c[2]} with **{_n(_int(c[3].replace('context_length ', '')))} tokens** of context; "
                    f"{c[4].replace('size_vram', 'Ollama reports')} on the GPU, {c[5]} ({c[0][:16]} UTC, `results/cache_test_notes.md`).\n"]

    # Facts for the script: each with its file and CSV lines
    facts = []
    src = "`results/cache_test.csv`"
    for v in ("qwen27b-mlx4", "llama70b-q4km"):
        if v in variants:
            t = turns_of[v]
            a, b, x = ratio(v)
            if x:
                facts.append(f"- {model_of[v]} on {CACHE_LABEL[v].split(' · ')[0]}, turn {t} of an agent session: {a:,.0f} s to the first "
                             f"token with {_n(mcached(v, 'A', t))} tokens reused, {b:,.0f} s when one ID at the top of the prompt changes "
                             f"every turn ({x:.0f}× longer). {src} lines {lines(v, 'A', t)} (A) and {lines(v, 'B', t)} (B).")
    if all(v in variants for v in ("qwen27b-ollama", "qwen27b-q4km")):
        t = turns_of["qwen27b-ollama"]
        facts.append(f"- Editing one line halfway back: Ollama rewound to {_n(mcached('qwen27b-ollama', 'C', t))} cached tokens and "
                     f"answered in {mttft('qwen27b-ollama', 'C', t):.0f} s; llama-server re-read everything and took "
                     f"{mttft('qwen27b-q4km', 'C', t):.0f} s. {src} lines {lines('qwen27b-ollama', 'C', t)} and {lines('qwen27b-q4km', 'C', t)}.")
    if ("llama70b-q4km", "C", 6) in by and ("qwen27b-q4km", "C", 6) in by:
        facts.append(f"- Same edit halfway back, same server (llama-server), turn 6: Llama 3.3 70B kept {_n(mcached('llama70b-q4km', 'C', 6))} "
                     f"tokens and answered in {mttft('llama70b-q4km', 'C', 6):,.0f} s; the hybrid Qwen 3.8 27B kept "
                     f"{_n(mcached('qwen27b-q4km', 'C', 6))}. {src} lines {lines('llama70b-q4km', 'C', 6)} and {lines('qwen27b-q4km', 'C', 6)}.")
    if all(v in variants for v in ("qwen27b-mlx4", "qwen27b-q4km")):
        t = turns_of["qwen27b-mlx4"]
        facts.append(f"- Stripping earlier thinking cost mlx_lm.server its whole cache ({mttft('qwen27b-mlx4', 'D', t):.0f} s, "
                     f"{_n(mcached('qwen27b-mlx4', 'D', t))} cached); llama-server kept {_n(mcached('qwen27b-q4km', 'D', t))} tokens "
                     f"and took {mttft('qwen27b-q4km', 'D', t):.0f} s. {src} lines {lines('qwen27b-mlx4', 'D', t)} and {lines('qwen27b-q4km', 'D', t)}.")
    if failed:
        facts.append("- On its default settings, mlx_lm.server ran out of memory with Llama 3.3 70B by the fifth turn of the same "
                     "session on a 96 GB Mac; llama-server finished it. `results/cache_test_mlx_default_oom.md`, "
                     "`runs/ct-llama70b-mlx4-A-r1/server.log`.")
    if facts:
        out += ["### Facts for the script (agent loop)\n", f"Same disclaimer as the rest of this report: {DISCLAIMER[2:]}\n", *facts, ""]
    return out, facts


def _report_cfg():
    try:
        from . import load_manifest
        return load_manifest().get("report") or {}
    except Exception:
        return {}


DISCLAIMER = (_report_cfg().get("disclaimer") or
              "> Measured on one Mac under the conditions listed below; a different Mac, macOS or set of open apps will give different numbers.")


def conditions_note():
    """The apps that were open at the start of each set, as the harness recorded them."""
    p = R / "conditions.jsonl"
    if not p.exists():
        return []
    seen, out = set(), []
    for l in p.read_text().splitlines():
        c = json.loads(l)
        key = (c.get("set"), c.get("load_condition"), tuple(c.get("foreground_apps", [])))
        if key in seen:
            continue
        seen.add(key)
        apps = [a for a in c.get("foreground_apps", []) if a not in ("Finder", "Terminal", "BenchMonitor", "Claude")]
        out.append(f"- Set {c.get('set')} ({c.get('load_condition')}, started {c['t'][:16].replace('T', ' ')} UTC): apps open: "
                   f"{', '.join(apps) or 'none besides Terminal and Bench Monitor'}")
    return ["### Test conditions as recorded\n", *out,
            "\nSet 1 is labelled `clean` in the CSVs (the harness setting it ran under), but these apps were open while it ran (the harness records which apps are open, not what they were doing). "
            "See the disclaimer above.\n"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--morning", action="store_true")
    a = ap.parse_args()
    summary = rows("summary.csv")
    s1, f1 = set1(summary)
    s2, f2 = media(rows("media.csv"))
    s3, f3 = cache_loop()
    now = datetime.datetime.now()
    if a.morning:
        real = [r for r in summary if r["set"] == "1"]
        errs = (R / "errors.jsonl").read_text().splitlines() if (R / "errors.jsonl").exists() else []
        lines = [f"# Morning summary {now:%Y-%m-%d %H:%M}\n",
                 f"- runs recorded: {len(real)} (fit yes {sum(r['fit'] == 'yes' for r in real)}, no {sum(r['fit'] == 'no' for r in real)}, "
                 f"skipped {sum(r['fit'] == 'skipped' for r in real)})",
                 f"- harness errors (will retry on restart): {len(errs)}"]
        lines += [f"  - {cell(json.loads(e)['run_id'])}: {cell(json.loads(e)['reason'][:160])}" for e in errs[-10:]]
        lines += ["", DISCLAIMER, "", *s1]
        out = R / f"morning_{now:%Y%m%d}.md"
        out.write_text("\n".join(lines) + "\n")
        print(out)
        return
    md = [f"# Nov 17 benchmark results\n\n{DISCLAIMER}\n\nGenerated {now:%Y-%m-%d %H:%M} from results/*.csv only. "
          "Median of the timed reps, range in brackets.\n", *conditions_note(), *s1, *set1_notes(summary), *kv_probe(), *s2, *compare_machines(), *mcm(), *s3,
          "## Facts for the script\n", f"Every number below carries the disclaimer: {DISCLAIMER[2:]}\n", *f1, *f2, *kv_probe_facts()]
    (R / "report.md").write_text("\n".join(md) + "\n")
    print(R / "report.md")


if __name__ == "__main__":
    main()
