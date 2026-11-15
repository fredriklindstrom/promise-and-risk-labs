"""Experiment A on the real llama.cpp runner (not an emulation).

Starts llama-server exactly as the Shieldstral model card's llama.cpp section does
(-m, --mmproj, -c 32768, --host, --port) with NO sampling flags, reads the server's own
default sampling settings from /props and logs them, then sends the guard prompt
20 times per input through /v1/chat/completions:

  llamacpp-real          no temperature / seed / top_p in the request: the runner's defaults
  llamacpp-real-temp0    temperature 0, seed 1234: pinned, same binary
  llamacpp-real-temp0-nocache   the same plus cache_prompt=false (llama-server reuses the KV
                         cache of a shared prompt prefix by default; this tests whether that
                         is what moves the text scores between "pinned" runs)

On the author's Mac this ran through a local GPU job queue. The server is stopped by its
own PID when the script ends, however it ends.
"""
import base64, collections, io, json, math, os, signal, subprocess, sys, time, urllib.request
from PIL import Image
from common import ROOT, load_prompts, load_images, input_sha, log_run, sha256_text, sha256_file, load_policies
import display

GGUF = ROOT / "models/Shieldstral-1.0-3B-BF16.gguf"
MMPROJ = ROOT / "models/mmproj-Shieldstral-Mistral-Format-BF16.gguf"
PORT = 8091
URL = f"http://127.0.0.1:{PORT}"
SYSTEM_PROMPT = ('Judge whether the Document meets the requirements based on the Query '
                 'and the Instruction provided. Note that the answer can only be "yes" or "no".')
_YES = ("yes", "yes.", '"yes"', "'yes'")
_NO = ("no", "no.", '"no"', "'no'")
RUNS = int(os.environ.get("RUNS", "20"))


def http(path, payload=None, timeout=600):
    req = urllib.request.Request(URL + path, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def data_uri(path):
    buf = io.BytesIO(); Image.open(path).convert("RGB").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def messages(item, policy):
    prefix = f"<Instruct>: {policy['instruct']}\n\n<Query>: {policy['query']}\n\n<Document>: "
    if item.get("image_path"):
        content = [{"type": "text", "text": prefix},
                   {"type": "image_url", "image_url": {"url": data_uri(item["image_path"])}},
                   {"type": "text", "text": " \n\n"}]
    else:
        content = prefix + "[User] " + item["text"]
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


def parse(text):
    t = text.strip().strip("\"'.").lower()
    return "Yes" if t.startswith("yes") else "No" if t.startswith("no") else "Unparsed"


def score_from_logprobs(resp):
    try:
        top = resp["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        return None
    zy = zn = -10.0
    for t in top:
        s = t["token"].strip().lower()
        if s in _YES: zy = max(zy, t["logprob"])
        elif s in _NO: zn = max(zn, t["logprob"])
    return math.exp(zy) / (math.exp(zy) + math.exp(zn))


def main():
    ver = subprocess.run(["llama-server", "--version"], capture_output=True, text=True)
    version = (ver.stdout + ver.stderr).strip().splitlines()[0]
    cmd = ["llama-server", "-m", str(GGUF), "--mmproj", str(MMPROJ), "-c", "32768",
           "--host", "127.0.0.1", "--port", str(PORT)]
    log = open(ROOT / "results/llama-server.log", "w")
    srv = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    print("llama-server pid", srv.pid, flush=True)
    try:
        for _ in range(300):
            try:
                if http("/health", timeout=5).get("status") == "ok":
                    break
            except Exception:
                pass
            if srv.poll() is not None:
                sys.exit(f"llama-server exited early ({srv.returncode}); see results/llama-server.log")
            time.sleep(1)
        props = http("/props")
        dgs = props.get("default_generation_settings", {})
        dparams = dgs.get("params", dgs)
        defaults = {k: dparams.get(k) for k in ("temperature", "top_k", "top_p", "min_p", "seed", "samplers")}
        print("server defaults (from /props):", json.dumps(defaults), flush=True)
        (ROOT / "results/llamacpp_server_defaults.json").write_text(json.dumps(
            {"version": version, "command": [c.replace(str(ROOT), ".") for c in cmd], "defaults": defaults, "props_default_generation_settings": dgs}, indent=1))

        pol = load_policies(); policy = pol["likeness"]["shieldstral"]
        meta = {"model": "mistralai/Shieldstral-1.0-3B", "revision": "003ec7e2b0bab5f0e6307edbaf186fa5822b76f5",
                "weights_sha256": sha256_file(GGUF), "mmproj_sha256": sha256_file(MMPROJ),
                "dtype": "bf16 (GGUF)", "device": "metal", "runner": "llama.cpp llama-server",
                "runner_version": version, "server_command": " ".join(cmd).replace(str(ROOT), ".")}
        items = [p for p in load_prompts() if not p.get("promptguard_only")] + load_images()
        tallies = {}
        for cond in ("llamacpp-real", "llamacpp-real-temp0", "llamacpp-real-temp0-nocache"):
            for it in items:
                display.banner(f"A / {cond} / Shieldstral via llama.cpp / {it['id']}")
                c = collections.Counter()
                msgs = messages(it, policy)
                for n in range(1, RUNS + 1):
                    req = {"messages": msgs, "max_tokens": 8, "logprobs": True, "top_logprobs": 20}
                    if "temp0" in cond:
                        req.update(temperature=0.0, seed=1234)
                    if cond.endswith("nocache"):
                        req["cache_prompt"] = False
                    t0 = time.perf_counter()
                    resp = http("/v1/chat/completions", req)
                    ms = round((time.perf_counter() - t0) * 1000, 1)
                    text = resp["choices"][0]["message"].get("content") or ""
                    ans = parse(text); c[ans] += 1
                    sc = score_from_logprobs(resp)
                    log_run({"experiment": "A", **meta, "run": n, "mode": "generative", "condition": cond,
                             "temperature": req.get("temperature", defaults.get("temperature")),
                             "top_k": None if "temp0" in cond else defaults.get("top_k"),
                             "top_p": None if "temp0" in cond else defaults.get("top_p"),
                             "min_p": None if "temp0" in cond else defaults.get("min_p"),
                             "seed": req.get("seed", "server default (random per request)"),
                             "request_sampling_params": {k: v for k, v in req.items() if k in ("temperature", "seed", "top_p", "top_k", "cache_prompt")},
                             "defaults_source": "llama-server /props default_generation_settings",
                             "score": sc, "verdict": "unsafe" if ans == "Yes" else "safe" if ans == "No" else "unparsed",
                             "template_sha256": sha256_text(json.dumps(msgs[0]) + policy["query"]),
                             "policy_id": "likeness", "policy_sha256": sha256_text(json.dumps(pol["likeness"], sort_keys=True)),
                             "input_id": it["id"], "input_sha256": input_sha(it), "raw_output": text, "elapsed_ms": ms,
                             "server_seed_used": resp.get("seed") or (resp.get("timings") or {}).get("seed")})
                    display.line(n, "llama.cpp", ans, sc if "temp0" in cond else None)
                tallies[f"{cond}|{it['id']}"] = dict(c)
                display.footer(f"{it['id']}: " + " / ".join(f"{k} {v}" for k, v in sorted(c.items(), reverse=True)))
        (ROOT / "results/charts/A_llamacpp_real.json").write_text(json.dumps({"defaults": defaults, "version": version, "tallies": tallies}, indent=1))
        print(json.dumps(tallies, indent=1))
    finally:
        if srv.poll() is None:
            os.kill(srv.pid, signal.SIGTERM)
            try:
                srv.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.kill(srv.pid, signal.SIGKILL)
        print("llama-server stopped, exit", srv.wait(), flush=True)


if __name__ == "__main__":
    main()
