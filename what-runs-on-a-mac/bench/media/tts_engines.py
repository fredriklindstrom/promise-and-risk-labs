"""Text-to-speech engines for the narration benchmark.

The engine code and sampling parameters are the ones used for the 2026-09-21 voice bake-off, so results stay
comparable with it; references and their transcripts now come from the manifest (you supply your own — use a voice
you have the right to clone). Models load from a local, verified snapshot path; nothing is fetched at run time
(the harness runs voice jobs with HF_HUB_OFFLINE=1).

Engines:
  chatterbox-torch   Chatterbox 0.1.7 on PyTorch/MPS (needs the `chatterbox-tts` package; the slow baseline)
  chatterbox-mlx     Chatterbox Multilingual fp16 on MLX (mlx-audio)
  qwen3-tts          Qwen3-TTS Base on MLX (0.6B or 1.7B — the checkpoint is the manifest's choice)
  moss               MOSS-TTS v1.5 (8B) on MLX
  moss-local         MOSS-TTS Local-Transformer v1.5 on MLX (48 kHz label; see the decimation note below)
"""
import re
import time

SENT = re.compile(r"(?<=[.!?])\s+")

# Chunk size is an engine property: Chatterbox has a ~1000-token (~22 s) ceiling; the others are kept to roughly half
# a minute per call (Qwen3-TTS was seen inserting words past ~360 words).
CHUNK = {"chatterbox-torch": 220, "chatterbox-mlx": 220, "qwen3-tts": 600, "moss": 600, "moss-local": 600}


def chunk_text(text, limit):
    """Whole sentences grouped up to `limit` characters, never split mid-sentence."""
    units = [s.strip() for s in SENT.split(" ".join(text.split())) if s.strip()]
    out, cur, n = [], [], 0
    for s in units:
        if cur and n + 1 + len(s) > limit:
            out.append(" ".join(cur)); cur, n = [], 0
        cur.append(s); n += len(s) + 1
    if cur:
        out.append(" ".join(cur))
    return out


class Engine:
    def __init__(self, kind, model_path, ref_path, ref_text):
        if kind not in CHUNK:
            raise ValueError(f"unknown engine kind {kind!r} (one of {sorted(CHUNK)})")
        self.kind, self.model_path, self.ref, self.ref_text = kind, model_path, ref_path, ref_text
        self.sr = 24000
        self.peak_dev = 0.0

    def load(self):
        t = time.monotonic()
        if self.kind == "chatterbox-torch":
            import torch
            from chatterbox.tts import ChatterboxTTS
            self.torch = torch
            self.m = ChatterboxTTS.from_local(self.model_path, device="mps")
            self.sr = self.m.sr
            self.m.prepare_conditionals(self.ref, exaggeration=0.45)
        else:
            import mlx.core as mx
            from mlx_audio.tts.utils import load_model
            self.mx = mx
            mx.reset_peak_memory() if hasattr(mx, "reset_peak_memory") else mx.metal.reset_peak_memory()
            self.m = load_model(self.model_path)
        return time.monotonic() - t

    def dev_peak(self):
        if self.kind == "chatterbox-torch":
            v = self.torch.mps.driver_allocated_memory() / 1e9
        else:
            v = (self.mx.get_peak_memory() if hasattr(self.mx, "get_peak_memory") else self.mx.metal.get_peak_memory()) / 1e9
        self.peak_dev = max(self.peak_dev, v)
        return v

    def say(self, text):
        import numpy as np
        if self.kind == "chatterbox-torch":
            wav = self.m.generate(text, exaggeration=0.45, cfg_weight=0.5, temperature=0.8)
            return wav.squeeze(0).detach().cpu().numpy().astype(np.float32), self.sr
        if self.kind == "chatterbox-mlx":
            gen = self.m.generate(text=text, ref_audio=self.ref, exaggeration=0.45, cfg_weight=0.5, temperature=0.8, verbose=False)
        elif self.kind == "qwen3-tts":
            gen = self.m.generate(text=text, ref_audio=self.ref, ref_text=self.ref_text, lang_code="english", verbose=False)
        else:   # MOSS family — reference audio only; language pinned so v1.5 does not guess
            gen = self.m.generate(text=text, ref_audio=self.ref, language="English", max_tokens=4096)
        pieces, sr = [], None
        for r in gen:
            pieces.append(np.asarray(r.audio, dtype=np.float32).reshape(-1))
            sr = getattr(r, "sample_rate", None) or sr
        self.sr = sr or self.sr
        audio = np.concatenate(pieces)
        if self.kind == "moss-local":
            # mlx-audio 0.5.0 returns twice the samples its 48 kHz label implies (half speed, half F0, measured
            # 2026-09-21). Treating the buffer as 96 kHz and decimating 2:1 restores pitch, tempo and duration.
            from scipy.signal import resample_poly
            audio = resample_poly(audio, 1, 2).astype(np.float32)
        return audio, self.sr
