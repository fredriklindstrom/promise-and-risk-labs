"""One MiniMax-H3 clip through mlx-serve (its own server process per clip, 127.0.0.1 only — mlx-serve's
default bind is 0.0.0.0). Prints PipeNetwork-style lines ("  step i/N  X.Xs", "  text encoder: Xs",
"X.Xs per step, Y min total") so bench.media.run_media parses both builds the same way. Muxes the raw
rgb8 frames + pcm_s16le audio to MP4 with ffmpeg. Stdlib only."""
import argparse, base64, http.client, json, os, re, signal, socket, subprocess, sys, time

BIN = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "mlx-serve-26.10.1", "mlx-serve")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True); ap.add_argument("--prompt", required=True)
    ap.add_argument("--width", type=int, required=True); ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--seconds", type=float, required=True); ap.add_argument("--steps", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--keyframe"); ap.add_argument("--turbo", action="store_true")
    ap.add_argument("--fast", choices=["default", "true", "false"], default="default")
    ap.add_argument("--ane", action="store_true", help="mlx-serve --ane-video: Neural Engine beside the GPU (lossy int8/fp16)")
    a = ap.parse_args()
    port = free_port()
    log_path = os.path.splitext(a.out)[0] + ".mlxserve.log"
    srv = subprocess.Popen([BIN, "--serve", "--model", a.pack, "--host", "127.0.0.1", "--port", str(port)] + (["--ane-video"] if a.ane else []),
                           stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
                           env={**os.environ, "HF_HUB_OFFLINE": "1"})   # same process group as this wrapper: the runner's
                                                                       # killpg (watchdog, Ctrl-C, SIGTERM) takes both
    try:
        t0 = time.time()
        while True:
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=3); c.request("GET", "/health")
                if c.getresponse().status == 200:
                    break
            except Exception:
                pass
            if srv.poll() is not None:
                sys.exit(f"mlx-serve exited during startup (code {srv.returncode}); see {log_path}")
            time.sleep(1)
        print(f"  server start: {time.time() - t0:.1f}s", flush=True)
        frames = round(a.seconds * 24)
        body = {"prompt": a.prompt, "width": a.width, "height": a.height, "num_frames": frames, "steps": a.steps,
                "seed": a.seed, "stream": True}
        if a.fast != "default":
            body["fast"] = a.fast == "true"
        if a.turbo:
            body["turbo"] = True
        if a.keyframe:
            img = base64.b64encode(open(a.keyframe, "rb").read()).decode()
            body["first_frame_image"] = img; body["last_frame_image"] = img
        print("  request: " + json.dumps({k: (v if k not in ("first_frame_image", "last_frame_image") else "<png>") for k, v in body.items()}), flush=True)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=6 * 3600)
        t_req = time.time()
        c.request("POST", "/v1/video/generations", json.dumps(body), {"Content-Type": "application/json"})
        r = c.getresponse()
        if r.status != 200:
            sys.exit(f"HTTP {r.status}: {r.read()[:400]!r}")
        last_t, done = None, None
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            j = json.loads(line[5:].strip())
            if j.get("type") == "progress":
                now = time.time()
                if j.get("stage") == "Generating" and j.get("step"):
                    dt = now - last_t if last_t else now - t_req
                    print(f"  step {j['step']}/{j['total']}  {dt:.1f}s  (client clock)", flush=True)
                last_t = now
            elif j.get("type") == "complete":
                done = j
            elif j.get("type") == "error" or j.get("error"):
                sys.exit(f"mlx-serve error: {json.dumps(j)[:400]}")
        total = time.time() - t_req
        if not done:
            sys.exit("no complete payload from mlx-serve")
        w, h, n = done["width"], done["height"], done["frames"]
        wav = os.path.splitext(a.out)[0] + ".pcm"
        open(wav, "wb").write(base64.b64decode(done["audio_data"]))
        ff = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(done["fps"]),
                             "-i", "-", "-f", "s16le", "-ar", str(done["audio_sample_rate"]), "-ac", str(done["audio_channels"]), "-i", wav,
                             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", a.out],
                            input=base64.b64decode(done["data"]))
        os.remove(wav)
        if ff.returncode:
            sys.exit("ffmpeg mux failed")
        # Exact per-phase / per-step timings from mlx-serve's own log
        txt = open(log_path, errors="replace").read()
        # Fast mode reuses a cached velocity on most steps ("(cached velocity, 1 ms)"); only computed forwards are comparable.
        steps_ms = [float(x) for x in re.findall(r"\[minimax-h3\] step \d+/\d+ sigma [\d.]+ \(([\d.]+) ms\)", txt)]
        cached = len(re.findall(r"\[minimax-h3\] step \d+/\d+ sigma [\d.]+ \(cached velocity", txt))
        m = lambda pat: (re.search(pat, txt) or [None, None])[1]
        te, dit, vae, aud = m(r"\(([\d.]+) ms load\+encode\)"), m(r"dit loaded in ([\d.]+) ms"), m(r"video decoded in ([\d.]+) ms"), m(r"audio decoded: .*\(([\d.]+) ms\)")
        for label, v in (("text encoder (load+encode)", te), ("transformer (load)", dit), ("video vae (load+decode)", vae), ("audio vae (decode)", aud)):
            if v:
                print(f"  {label}: {float(v) / 1000:.1f}s", flush=True)
        recipe = m(r"(speed recipe: [^\n]+)")
        if recipe:
            print(f"  {recipe}", flush=True)
        for line in txt.splitlines():                     # record whatever mlx-serve says about the ANE share
            if "ane" in line.lower() and ("split" in line.lower() or "neural engine" in line.lower() or "calibrat" in line.lower()):
                print(f"  ANE: {line.strip()[:200]}", flush=True)
        print(f"\nwrote {a.out} ({n} frames, {w}x{h})")
        sps = sum(steps_ms) / len(steps_ms) / 1000 if steps_ms else total / max(a.steps, 1)
        print(f"  computed forwards: {len(steps_ms)} of {len(steps_ms) + cached} steps ({cached} reused a cached velocity)", flush=True)
        # "s per step" = mean over COMPUTED forwards only, so it compares 1:1 with full-compute runs.
        print(f"{sps:.1f}s per step, {total / 60:.1f} min total", flush=True)
    finally:
        try:
            srv.terminate(); srv.wait(30)
        except Exception:
            srv.kill()


if __name__ == "__main__":
    main()
