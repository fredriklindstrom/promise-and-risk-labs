"""Load-time gate (security review 2026-10-13): a model may only be loaded if every file of its pinned revision has a
verified hash in results/checksums.jsonl (written by `python -m bench.download` / `--verify`).
Override for experiments only: BENCH_ALLOW_UNVERIFIED=1 (the run is then labelled in its notes)."""
import json, os
from . import ROOT

SUMS = ROOT / "results/checksums.jsonl"


def tokenizer_source(v):
    """(repo, revision) of a variant's tokenizer when it comes from another repo, else None."""
    if v.get("tok_from"):
        return tuple(v["tok_from"])
    return (v["tokenizer_repo"], v.get("tokenizer_revision")) if v.get("tokenizer_repo") else None


def check_variant(v, tok=None):
    """Weights, then the tokenizer when it comes from another repo. tok: a pinned (repo, revision) from the caller."""
    if v.get("repo"):
        ok, why = check(v["repo"], v.get("revision"), file=v.get("file"))
        if not ok:
            return ok, why
    tok = tok or tokenizer_source(v)
    return check(*tok, tokenizer=True) if tok else (True, "")


def check(repo, revision, tokenizer=False, file=None):
    """(ok, reason). revision may be a prefix of the full commit SHA recorded at download time. Records from a
    tokenizer-only job count only with tokenizer=True; file: this file must be among the verified records."""
    if os.environ.get("BENCH_ALLOW_UNVERIFIED") == "1":
        return True, "UNVERIFIED (BENCH_ALLOW_UNVERIFIED=1)"
    if not revision:
        return False, f"{repo}: no pinned revision in the manifest"
    if not SUMS.exists():
        return False, f"{repo}: no checksum records — run python -m bench.download --verify"
    latest = {}
    for line in SUMS.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("scope") == "tokenizer" and not tokenizer:
            continue
        if r.get("repo") == repo and str(r.get("revision", "")).startswith(revision):
            latest[(r["revision"], r["file"])] = r          # later records (re-verifications) win
    if not latest:
        return False, f"{repo}@{revision}: never verified — run python -m bench.download --verify"
    shas = {k[0] for k in latest}
    if len(shas) > 1:
        return False, f"{repo}@{revision}: prefix matches several commits {sorted(shas)} — pin the full SHA"
    doc = lambda name: name.split("/")[-1].lower().startswith(("readme", "license", "notice", "usage_policy"))
    bad = [k[1] for k, r in latest.items() if r.get("verified") is not True and not doc(k[1])]   # docs are never loaded
    if bad:
        return False, f"{repo}@{revision}: {len(bad)} file(s) not verified (e.g. {bad[0]}) — re-run python -m bench.download --verify"
    if file and (next(iter(shas)), file) not in latest:
        return False, f"{repo}@{revision}: {file} never verified — run python -m bench.download --verify"
    return True, f"verified {len(latest)} files @ {next(iter(shas))[:12]}"
