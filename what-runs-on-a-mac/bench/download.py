"""Model downloads into the default Hugging Face cache, verified.

    python -m bench.download [id ...]            # download (only the ids named, else every non-optional one)
    python -m bench.download --verify [id ...]   # hash what is already on disk against the Hub's record, no download

Rules enforced here (security review 2026-10-13):
- Every repo must pin a revision; it is resolved to the full commit SHA and that SHA is what gets downloaded and logged.
- ALLOWLIST of data-only file types (weights, configs, tokenizers, chat templates, text). Anything else in the repo is
  skipped and listed. After download the snapshot is scanned: a file type that can carry code (.py/.pyc/.so/.dylib/
  pickle formats/.npy/…) present in it fails the job, even if it came from an earlier, unrelated download. The one
  exception is a variant's data_files: named top-level .bin files that must parse as safetensors.
- Every file is verified: LFS files by SHA-256 against the Hub's record, small files by their git blob SHA-1.
- A mismatch or a refusal makes the command exit non-zero. Nothing is ever deleted; the harness refuses to load any
  snapshot without a complete verified record (bench.verify).
- The disk floor is checked before every repo.
"""
import datetime, fnmatch, hashlib, json, os, sys, time
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
from . import ROOT, load_manifest, validate_manifest
from .verify import tokenizer_source
from .diskguard import can_fit, status

ALLOW = ("*.safetensors", "*.gguf", "*.json", "*.jinja", "tokenizer.model", "*.tiktoken", "*.txt", "*.md",
         "LICENSE*", "USAGE_POLICY", "NOTICE*", "*.wav.json")
CODE_EXT = (".py", ".pyc", ".pyo", ".so", ".dylib", ".bin", ".pt", ".pth", ".pkl", ".pickle", ".ckpt", ".npy", ".npz",
            ".joblib", ".h5", ".hdf5", ".keras", ".msgpack", ".sh", ".exe", ".jar", ".onnx_data")
SKIP_DIRS = ("original/", "metal/", ".")
LOG = ROOT / "results/downloads.jsonl"
SUMS = ROOT / "results/checksums.jsonl"
api = HfApi()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def log(rec):
    rec = {"t": now(), **rec}
    print(json.dumps(rec), flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


def jobs(include_optional=False):
    m = load_manifest()
    validate_manifest(m)
    out = []
    for v in m["variants"]:
        if v.get("optional") and not include_optional:
            continue                                    # probes download only when named on the command line
        if v.get("repo"):
            out.append({"id": v["id"], "repo": v["repo"], "revision": v.get("revision"),
                        "file": v.get("file"), "allow": v.get("allow_patterns"), "on_disk": v.get("on_disk", False),
                        "data_files": v.get("data_files", [])})
        if tokenizer_source(v):                         # tokenizer files only: the harness gates this repo as well
            repo, rev = tokenizer_source(v)
            out.append({"id": v["id"] + "-tokenizer", "parent": v["id"], "repo": repo, "revision": rev, "file": None,
                        "allow": ["*.json", "*.jinja", "*.txt"], "on_disk": v.get("on_disk", False),
                        "scan_exempt": [n for x in m["variants"] if x.get("repo") == repo for n in x.get("data_files", [])]})
    for e in m["media"].get("voice", {}).get("engines", []):
        if e.get("repo"):
            out.append({"id": e["id"], "repo": e["repo"], "revision": e.get("revision"), "file": None, "allow": None,
                        "on_disk": e.get("on_disk", False)})
            for dep in e.get("deps", []):
                out.append({"id": e["id"] + "-dep-" + dep["repo"].split("/")[-1], "repo": dep["repo"],
                            "revision": dep.get("revision"), "file": None, "allow": None, "on_disk": e.get("on_disk", False)})
    for b in m["media"].get("h3", {}).get("builds", []):
        if b.get("repo") and (include_optional or not b.get("optional", True)):   # H3 is opt-in: name it to download it
            out.append({"id": b["id"], "repo": b["repo"], "revision": b.get("revision"), "file": None, "allow": None,
                        "on_disk": b.get("on_disk", False)})
    return out


def allowed(name, job):
    if name.startswith(SKIP_DIRS) or "/." in name:
        return False
    if name in job.get("data_files", []):               # named per variant in the manifest, checked by is_safetensors
        return True
    if job["file"] and name != job["file"] and not (name.endswith((".json", ".md", ".txt", ".jinja")) and "/" not in name):
        return False                                    # single-file repos (GGUF): the file plus top-level metadata only
    if job["allow"] and not any(fnmatch.fnmatch(name, p) for p in job["allow"]):
        return False
    return any(fnmatch.fnmatch(name.split("/")[-1], p) for p in ALLOW)


def plan(job):
    if not job.get("revision"):
        raise ValueError(f"{job['repo']}: no pinned revision in the manifest (refusing to download a moving branch)")
    info = api.model_info(job["repo"], revision=job["revision"], files_metadata=True)
    if not info.sha.startswith(job["revision"]):
        raise ValueError(f"{job['repo']}: revision {job['revision']!r} resolved to {info.sha}, which does not start with it "
                         "(a branch or tag with that name?) — pin the full 40-character commit SHA")
    files, skipped = [], []
    for s in info.siblings:
        lfs = s.lfs if isinstance(s.lfs, dict) else (vars(s.lfs) if s.lfs else None)
        if not allowed(s.rfilename, job):
            skipped.append(s.rfilename)
            continue
        files.append({"name": s.rfilename, "size": s.size or 0,
                      "sha256": (lfs or {}).get("sha256"), "blob_id": getattr(s, "blob_id", None)})
    return info.sha, files, skipped


def sha256(path, buf=16 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def verify_files(job, full_sha, files, local):
    """Hash every planned file in a local snapshot; append records to checksums.jsonl. Returns mismatch count."""
    bad = 0
    for f in files:
        p = local / f["name"]
        rec = {"t": now(), "id": job["id"], "repo": job["repo"], "revision": full_sha, "file": f["name"], "size": f["size"]}
        if job.get("parent"):
            rec["scope"] = "tokenizer"                  # these records don't vouch for the repo's weights
        if not p.exists():
            if f["name"].split("/")[-1].lower().startswith(("readme", "license", "notice", "usage_policy")):
                continue        # documentation never loaded by a runner (some tools never download it): not a failure
            rec.update(verified=False, note="missing on disk")
            bad += 1
        elif f["sha256"]:
            got = sha256(p)
            rec.update(sha256=got, verified=(got == f["sha256"]))
            bad += got != f["sha256"]
        elif f["blob_id"]:
            got = git_blob_sha1(p)
            rec.update(git_sha1=got, verified=(got == f["blob_id"]))
            bad += got != f["blob_id"]
        else:
            rec.update(verified=False, note="Hub published no hash for this file")
            bad += 1
        with open(SUMS, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return bad


def is_safetensors(path):
    """True only for a safetensors file whose header accounts for its exact size, whatever its extension."""
    try:
        with open(path, "rb") as f:
            n = int.from_bytes(f.read(8), "little")
            if not 2 <= n <= 1 << 24:
                return False
            header = json.loads(f.read(n))
        end = max((v["data_offsets"][1] for k, v in header.items() if k != "__metadata__"), default=-1)
        return 8 + n + end == Path(path).stat().st_size
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return False


def unsafe_files(local, data_files=()):
    """Code-carrying file types present in a snapshot folder (from this or any earlier download)."""
    return sorted(str(p.relative_to(local)) for p in local.rglob("*")
                  if p.name.lower().endswith(CODE_EXT)
                  and not (str(p.relative_to(local)) in data_files and is_safetensors(p)))


def run(job, verify_only=False):
    full_sha, files, skipped = plan(job)
    size_gb = sum(f["size"] for f in files) / 1e9
    if verify_only:
        local = snapshot_path(job["repo"], full_sha)
        if local is None:
            log({"event": "not_on_disk", "id": job["id"], "repo": job["repo"], "revision": full_sha})
            return False
    else:
        ok, free, floor = can_fit(size_gb)
        log({"event": "plan", "id": job["id"], "repo": job["repo"], "revision": full_sha, "files": len(files),
             "size_gb": round(size_gb, 2), "free_gb": round(free, 1), "floor_gb": floor, "skipped_files": skipped[:50]})
        if not ok:
            log({"event": "refused_disk_floor", "id": job["id"], "need_gb": round(size_gb, 2), "free_gb": round(free, 1), "floor_gb": floor})
            return False
        t0 = time.time()
        local = Path(snapshot_download(job["repo"], revision=full_sha, allow_patterns=[f["name"] for f in files]))
        log({"event": "downloaded", "id": job["id"], "path": str(local), "wall_s": round(time.time() - t0)})
    unsafe = unsafe_files(local, job.get("data_files", []) + job.get("scan_exempt", []))
    if unsafe:
        log({"event": "UNSAFE_FILES_PRESENT", "id": job["id"], "files": unsafe[:20],
             "note": "code-carrying file types in the snapshot folder; refusing. Remove that model with your cache tool and re-download."})
        return False
    bad = verify_files(job, full_sha, files, local)
    log({"event": "verified" if not bad else "CHECKSUM_MISMATCH", "id": job["id"], "repo": job["repo"], "revision": full_sha,
         "files": len(files), "mismatches": bad, "disk": status()})
    return not bad


def snapshot_path(repo, full_sha):
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub")
    p = hub / ("models--" + repo.replace("/", "--")) / "snapshots" / full_sha
    return p if p.is_dir() else None


def main():
    if os.geteuid() == 0:
        sys.exit("Don't run this as root.")
    SUMS.parent.mkdir(parents=True, exist_ok=True)      # a fresh checkout has no results/ yet
    args = sys.argv[1:]
    verify_only = "--verify" in args
    want = [a for a in args if not a.startswith("--")]
    todo = [j for j in jobs(include_optional=bool(want)) if not want or j["id"] in want or j.get("parent") in want]
    if not verify_only:
        todo = [j for j in todo if not j["on_disk"] or j["id"] in want]
    unknown = set(want) - {j["id"] for j in todo} - {j.get("parent") for j in todo}
    if unknown:
        sys.exit(f"unknown ids: {sorted(unknown)}")
    failed = 0
    for j in todo:
        try:
            failed += not run(j, verify_only=verify_only)
        except Exception as e:
            failed += 1
            log({"event": "error", "id": j["id"], "error": repr(e)[:500]})
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
