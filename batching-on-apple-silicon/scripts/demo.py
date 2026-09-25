"""On-camera batching demo.

Measurement follows mlx_lm.benchmark (mlx_lm 0.31.3) exactly: random prompts
(seed 0), end-of-sequence disabled, one warm-up pass then one timed pass per
batch size; batch 1 uses stream_generate, 2+ use batch_generate. The model is
loaded once instead of once per batch size.

Everything mlx_lm prints goes to runs/demo-log.txt; the screen shows only the
script's own lines.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # lab root
LOG = HERE / "runs" / "demo-log.txt"

MODELS = [
    ("Qwen 3.8 27B", "lmstudio-community/Qwen3.8-27B-MLX-8bit", "dense, 8-bit"),
    ("Qwen 3.6 27B", "lmstudio-community/Qwen3.6-27B-MLX-8bit", "dense, 8-bit"),
    ("Mistral Small 3.2 24B", "lmstudio-community/Mistral-Small-3.2-24B-Instruct-2506-MLX-8bit", "dense, 8-bit"),
]
BATCHES = [1, 2, 4, 8, 16]
PROMPT_TOKENS = 128
GEN_TOKENS = 128

# Keep a handle on the real terminal, then send fds 1 and 2 to the log so
# nothing from mlx_lm, tqdm or warnings reaches the screen.
TTY = os.fdopen(os.dup(1), "w", buffering=1)
TTY_IN = sys.stdin
LOG.parent.mkdir(exist_ok=True)
_logf = open(LOG, "w", buffering=1)
os.dup2(_logf.fileno(), 1)
os.dup2(_logf.fileno(), 2)
sys.stdout = sys.stderr = _logf

DIM, BOLD, CYAN, GREEN, YELLOW, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[32m", "\033[33m", "\033[0m"


def say(text="", delay=0.018, end="\n"):
    # Typewriter effect; colour codes are written whole, without a pause.
    for part in re.split(r"(\033\[[0-9;]*m)", text):
        if part.startswith("\033"):
            TTY.write(part)
            continue
        for ch in part:
            TTY.write(ch)
            TTY.flush()
            time.sleep(delay)
    TTY.write(end)
    TTY.flush()


def status(text):
    TTY.write("\r\033[K" + text)
    TTY.flush()


def ask(prompt):
    TTY.write(prompt)
    TTY.flush()
    return TTY_IN.readline().strip()


def log(msg):
    print(f"### {time.strftime('%H:%M:%S')} {msg}", flush=True)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def hardware():
    prof = sh("system_profiler SPHardwareDataType SPDisplaysDataType")
    get = lambda key: next((l.split(":", 1)[1].strip() for l in prof.splitlines() if l.strip().startswith(key)), "?")
    cores = [l.split(":", 1)[1].strip() for l in prof.splitlines() if "Total Number of Cores" in l]
    return {
        "machine": f"{get('Model Name')} ({get('Model Identifier')})",
        "chip": get("Chip"),
        "cpu": cores[0] if cores else "?",
        "gpu": f"{cores[1]}-core" if len(cores) > 1 else "?",
        "memory": f"{get('Memory')} unified",
        "os": f"macOS {sh('sw_vers -productVersion')}",
    }


def main():
    os.system("clear >/dev/tty 2>/dev/null")
    say()
    say(f"  {BOLD}Happy to test this.{RESET} First question: what hardware are we working with today?")
    time.sleep(0.6)
    hw = hardware()
    say()
    for label, key in [("Machine", "machine"), ("Chip", "chip"), ("CPU", "cpu"), ("GPU", "gpu"), ("Memory", "memory"), ("OS", "os")]:
        say(f"    {DIM}{label:<8}{RESET} {CYAN}{hw[key]}{RESET}", delay=0.006)
        time.sleep(0.15)
    say()
    say(f"  {BOLD}OK. And which model are we putting through its paces?{RESET}")
    say()
    for i, (name, _, desc) in enumerate(MODELS, 1):
        say(f"    {YELLOW}{i}{RESET}  {name}  {DIM}({desc}){RESET}", delay=0.006)
    say()
    choice = ""
    while choice not in {"1", "2", "3"}:
        choice = ask("  Pick 1, 2 or 3: ")
    name, repo, desc = MODELS[int(choice) - 1]

    os.environ.setdefault("HF_HUB_OFFLINE", "0")  # set to 1 once the models are downloaded
    import mlx.core as mx
    from mlx_lm import batch_generate, load, stream_generate

    say()
    status(f"  Loading {name} into unified memory...")
    log(f"load {repo}")
    t0 = time.perf_counter()
    model, tokenizer, config = load(repo, return_config=True, tokenizer_config={"trust_remote_code": True})
    mx.eval(model.parameters())
    load_s = time.perf_counter() - t0
    status("")
    say(f"  {GREEN}✓{RESET} Loaded {name} in {load_s:.1f} s  {DIM}(peak memory {mx.get_peak_memory() / 1e9:.1f} GB){RESET}")

    tokenizer._eos_token_ids = {}
    vocab = config.get("vocab_size") or config["text_config"]["vocab_size"]
    prompts_all = {}
    for b in BATCHES:  # same prompts mlx_lm.benchmark draws for each batch size
        mx.random.seed(0)
        prompts_all[b] = mx.random.randint(0, vocab, (b, PROMPT_TOKENS)).tolist()

    status(f"  Pre-filling a {PROMPT_TOKENS}-token prompt...")
    log("prefill check")
    resp = None
    for resp in stream_generate(model, tokenizer, prompts_all[1][0], max_tokens=1):
        pass
    status("")
    say(f"  {GREEN}✓{RESET} Pre-fill working  {DIM}({resp.prompt_tps:.0f} prompt tokens/sec){RESET}")
    say()
    say(f"  {BOLD}Ready to test batch processing on the {hw['chip']} with {name}.{RESET}")
    say(f"  Each request: {PROMPT_TOKENS}-token prompt, {GEN_TOKENS} tokens back. We go 1, 2, 4, 8, then 16 at once.")
    go = ask(f"  {BOLD}Shall we commence?{RESET} [Y/n] ")
    if go.lower().startswith("n"):
        say("  Another time, then.")
        return

    say()
    say(f"  {BOLD}{'Requests at once':<18}{'Total tokens / sec':>22}{'Per request tokens / sec':>28}{RESET}", delay=0.004)
    say(f"  {DIM}{'-' * 16:<18}{'-' * 18:>22}{'-' * 24:>28}{RESET}", delay=0.002)

    def run(b):
        if b == 1:
            r = None
            for r in stream_generate(model, tokenizer, prompts_all[1][0], max_tokens=GEN_TOKENS):
                pass
            return r
        return batch_generate(model, tokenizer, prompts_all[b], max_tokens=GEN_TOKENS).stats

    base = None
    for b in BATCHES:
        status(f"  {DIM}{b:<18}warming up...{RESET}")
        log(f"batch={b} warmup")
        run(b)
        status(f"  {DIM}{b:<18}measuring...{RESET}")
        log(f"batch={b} timed")
        tic = time.perf_counter()
        r = run(b)
        total = time.perf_counter() - tic
        tps = r.generation_tps
        print(f"batch={b} generation_tps={tps:.3f} prompt_tps={r.prompt_tps:.3f} "
              f"peak_memory={r.peak_memory:.3f} total_time={total:.3f}", flush=True)
        base = base or tps
        status("")
        say(f"  {b:<18}{GREEN}{tps:>22.1f}{RESET}{tps / b:>28.1f}   {DIM}x{tps / base:.1f}{RESET}", delay=0.004)

    say()
    say(f"  {DIM}{name} ({desc}) on {hw['machine']}, {hw['chip']}, {hw['memory']}. mlx_lm, measured live.{RESET}", delay=0.004)
    ask("  ")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        TTY.write("\n")
