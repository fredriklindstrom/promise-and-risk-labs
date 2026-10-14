"""Narrate one scene with one engine (bench/media/tts_engines.py). Run with the engine's own venv; the media runner
passes everything on the command line. Prints JSON progress lines and a final {"voice_result": ...} line."""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tts_engines  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=sorted(tts_engines.CHUNK))
    ap.add_argument("--model-path", required=True, help="local, verified snapshot folder")
    ap.add_argument("--ref", required=True, help="reference voice WAV (a voice you have the right to use)")
    ap.add_argument("--ref-text", default="", help="exact transcript of the reference (Qwen3-TTS needs it)")
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    import numpy as np, soundfile as sf
    text = " ".join(open(a.scene).read().split())
    chunks = tts_engines.chunk_text(text, tts_engines.CHUNK[a.kind])
    eng = tts_engines.Engine(a.kind, a.model_path, a.ref, a.ref_text)
    load_s = eng.load()
    print(f"  load: {load_s:.1f}s", flush=True)
    t = time.monotonic(); eng.say(chunks[0][:120]); warm = time.monotonic() - t     # warm-up, as in the bake-off
    parts, per = [], []
    t_gen = time.monotonic()
    for i, c in enumerate(chunks):
        t0 = time.monotonic()
        audio, sr = eng.say(c)
        el = time.monotonic() - t0
        parts.append(audio)
        if i < len(chunks) - 1:
            parts.append(np.zeros(int(sr * 0.24), dtype=np.float32))
        per.append({"i": i + 1, "chars": len(c), "wall": round(el, 2), "audio": round(len(audio) / sr, 2)})
        print(json.dumps({"media_progress": {"step": i + 1, "steps": len(chunks), "s_per_step": round(el, 2), "eta_s": None}}), flush=True)
    gen = time.monotonic() - t_gen
    wav = np.concatenate(parts).astype(np.float32)
    sf.write(a.out, wav, eng.sr)
    audio_s = len(wav) / eng.sr
    print(json.dumps({"voice_result": {"engine": a.kind, "label": a.label, "model": a.model_path,
                                        "load_s": round(load_s, 2), "warmup_s": round(warm, 2), "gen_s": round(gen, 2),
                                        "audio_s": round(audio_s, 2), "rtf": round(gen / audio_s, 3), "chunks": len(chunks),
                                        "per_chunk": per, "device_peak_gb": round(eng.dev_peak(), 2), "sample_rate": eng.sr,
                                        "words": len(text.split())}}), flush=True)


if __name__ == "__main__":
    main()
