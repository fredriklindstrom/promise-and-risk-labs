"""Large, high-contrast run lines for screen recording."""
import sys, time
B, R, G, Y, D, X = "\033[1m", "\033[1;97;41m", "\033[1;30;42m", "\033[1;30;43m", "\033[2m", "\033[0m"


def banner(text):
    print(f"\n{B}{'=' * 64}\n  {text}\n{'=' * 64}{X}\n", flush=True)


def badge(word):
    w = word.upper()
    if w in ("YES", "UNSAFE"):
        return f"{R}  {w:^7s} {X}"
    if w in ("NO", "SAFE"):
        return f"{G}  {w:^7s} {X}"
    return f"{Y}  {w:^7s} {X}"


def line(n, model, word, score=None, h=None, delay=0.4):
    s = f"   {score:.10f}" if score is not None else ""
    hh = f"   {D}#{h}{X}" if h else ""
    print(f"{B} run {n:>2} {X}  {model:<16s} {badge(word)}{B}{s}{X}{hh}", flush=True)
    if delay:
        time.sleep(delay)


def footer(text):
    print(f"\n{B}   >>> {text}{X}\n", flush=True)
