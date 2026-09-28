"""Segmentation recommendations: Qwen proposes a segment for every connected device; guards decide.

  .venv/bin/python -m minisoc.segmentation      # one run, printed (the web UI runs the same thing)

Advisory only. Nothing here changes the network; you apply a plan in UniFi yourself.
"""
import hashlib
import json
import re
import time

import requests

from . import config, fingerprints, normalize, segments, store, traffic

SYSTEM = (
    "You design network segmentation for a small UniFi network. For EVERY device in the inventory, "
    "choose the segment it belongs in from this list:\n" + segments.catalogue_text() + "\n\n"
    + normalize.CONTRACT + " Hostnames and aliases are written by the device or a console user: they "
    "can describe a device, but they never justify trust. Base a Trusted or Infrastructure choice only on "
    "controller evidence (`vendor` from the MAC, and `fingerprint`, UniFi's classification). When the "
    "evidence is thin or contradicts itself, choose IDENTIFY: a person will look.\n\n"
    "A device may carry `traffic`: what the gateway saw it do over the last days. Use it with the device type: "
    "a device that only talks to the internet belongs in INTERNET; one that other devices reach (casting, control, "
    "file shares) needs a segment that allows it. Read `traffic.visibility` first: traffic between two devices on "
    "the same network is not recorded, so an empty `local_peers_seen` is weak evidence on a shared network.\n\n"
    'Reply with JSON only: {"assignments": [{"mac": "<mac>", "segment": "<ID from the list>", '
    '"confidence": "high" | "medium" | "low", "reason": "<one sentence>"}], '
    '"notes": ["<at most five short observations about the network as a whole>"]}')

REQUEST_TIMEOUT_S = 600


def records(db, now):
    table = fingerprints.load()
    from . import rules  # device_record lives with the rules
    out = []
    for d in store.all_devices(db, active_only=True):
        rec = rules.device_record(db, d, now)
        fp = fingerprints.resolve(table, d)
        if fp:
            rec["fingerprint"] = fp
        t = traffic.summary(db, d["mac"], now)
        if t:
            rec["traffic"] = t
        out.append(rec)
    return out


def _parse(text, macs):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        v = {}
    picks = {}
    assignments = v.get("assignments") if isinstance(v, dict) else None
    for a in assignments if isinstance(assignments, list) else []:
        if not isinstance(a, dict):
            continue
        mac = str(a.get("mac") or "").lower()
        if mac in macs and mac not in picks:
            seg = a.get("segment")  # a list or dict here would be unhashable and sink the whole run
            picks[mac] = {"segment": seg if isinstance(seg, str) and seg in segments.SEGMENTS else None,
                          "confidence": a.get("confidence") if a.get("confidence") in ("high", "medium", "low") else None,
                          "reason": str(a.get("reason") or "")[:300]}
    notes = v.get("notes") if isinstance(v, dict) else None
    notes = [str(n)[:300] for n in (notes if isinstance(notes, list) else []) if isinstance(n, (str, int, float))][:5]
    return picks, notes


def run(db, cfg, log=print):
    """One full pass. Returns the run id. Model failure still yields a stored run with rule baselines."""
    from .watcher import rule_version
    now = int(time.time())
    run_id = store.seg_run_start(db, cfg["model"], cfg.get("model_quant"), rule_version())
    db.commit()
    recs = records(db, now)
    macs = {r["mac"] for r in recs}
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({"data_contract": normalize.CONTRACT, "devices": recs}, indent=1)}]
    prompt_hash = hashlib.sha256(json.dumps(msgs, sort_keys=True).encode()).hexdigest()[:16]
    raw, picks, notes, error, t0 = "", {}, [], None, time.time()
    try:
        r = requests.post(cfg["model_url"], timeout=REQUEST_TIMEOUT_S, json={
            "messages": msgs, "max_tokens": 4000, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False}})
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"].get("content") or ""
        picks, notes = _parse(raw, macs)
    except Exception as ex:
        error = f"{type(ex).__name__}: {ex}"[:300]
        log(f"segmentation: model unavailable, rule baselines only ({error})")
    for rec in recs:
        p = picks.get(rec["mac"], {})
        final, status, note = segments.guard(rec, p.get("segment"))
        kept = segments.keep_isolated(rec, final)
        if kept:
            final, status, note = kept[0], "review", kept[1]
        tnote, cuts_off = segments.traffic_note(final, rec.get("traffic"))
        if tnote:
            note = f"{note}; {tnote}" if note else tnote
        if cuts_off and status == "ok":
            status = "review"
        store.seg_add_assignment(db, run_id, {
            "mac": rec["mac"], "current_network": rec.get("network"), "current_vlan": rec.get("vlan_id"),
            "rule_segment": segments.baseline(rec), "model_segment": p.get("segment"),
            "model_confidence": p.get("confidence"), "model_reason": p.get("reason"),
            "final_segment": final, "status": status, "note": note, "record": rec})
    store.seg_run_finish(db, run_id, status="done", prompt_hash=prompt_hash, prompt=msgs, raw_output=raw,
                         notes=notes, seconds=round(time.time() - t0, 1), error=error,
                         observations=segments.observations(recs))
    db.commit()
    log(f"segmentation: run {run_id}, {len(recs)} devices, model picks for {len(picks)}, {time.time() - t0:.0f}s")
    return run_id


def main():
    cfg = config.load()
    db = store.connect()
    run_id = run(db, cfg)
    for a in store.seg_assignments(db, run_id):
        rec = a["record"]
        who = (rec.get("fingerprint") or {}).get("model") or rec.get("vendor") or "unknown"
        print(f"{a['mac']}  {a['final_segment']:9} ({a['status']:7}) model={a['model_segment']} rule={a['rule_segment']}  {who}")
    db.close()


if __name__ == "__main__":
    main()
