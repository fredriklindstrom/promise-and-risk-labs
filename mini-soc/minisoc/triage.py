"""L2 triage: Qwen3.8-27B, resident behind mlx_lm.server (the .model job in launchd/).

Tier model (Fredrik, 2026-09-26):
  L1 = the rules. Yes or no: an alert fires or it doesn't.
  L2 = Qwen. It assesses each rule alert and escalates whenever it isn't confident.

The model annotates; it never opens, closes, or silences an alert. Notifications stay rule-driven,
because text inside the data can argue for "benign".

Prompt layout for prefix caching: the system prompt (contract + playbook) is byte-identical on
every call, slow-changing context comes next, and the alert itself goes last.
"""
import hashlib
import json
import re
import time

import requests

from . import actions, normalize

ASSESSMENTS = ("likely_benign", "likely_malicious", "uncertain")
REQUEST_TIMEOUT_S = 180
CONFIDENCES = ("high", "medium", "low")

PLAYBOOK = """Rules that can raise an alert:
- new_device: a MAC address the network has never seen. Randomised (private) addresses are normal for phones and guests.
- label_changed: a console alias (set by a user with write access) or a hostname (set by the device itself) changed.
- config_change: an admin changed controller configuration. `known_normal` lists admin addresses the owner has confirmed.
- admin_login_new_ip: an admin logged in from an address not seen before.
- unifi_security_event: UniFi's own security detection (rogue AP, threat, IPS). For a rogue AP, a BSSID
  that resembles or equals your hardware never settles it as benign: an impersonator copies exactly that.
  Only a person confirming in UniFi which radio broadcasts it can. `site.environment` (set by the owner)
  changes how likely a FOREIGN network is to be a neighbour's; it never softens a network that copies yours.
- unfamiliar_event: an event category this system has no rule for.
- hostname_collision: more than one recently seen device reports the same hostname.
`facts.hostname_shared_with` lists other devices currently reporting the same hostname."""

SYSTEM = (
    "You are the L2 security analyst for a small UniFi network. A deterministic rule has already raised "
    "the alert; you assess what it most likely means and say how sure you are. If the evidence does not "
    "settle it, answer \"uncertain\": a human will look. Never guess to avoid escalating. "
    "`known_normal` lists what the owner has confirmed is theirs (for example an address); it never "
    "pre-approves an action, so a change or login from a known address still needs its own assessment.\n\n"
    + normalize.CONTRACT + "\n\n" + PLAYBOOK + "\n\n"
    "Recommend exactly one action from this list (a person carries it out; nothing is automatic):\n"
    + actions.catalogue_text() + "\n\n"
    'Reply with JSON only: {"assessment": "likely_benign" | "likely_malicious" | "uncertain", '
    '"confidence": "high" | "medium" | "low", "reason": "<one or two sentences>", '
    '"evidence": ["<field paths you relied on>"], "action": "<one ID from the list>", '
    '"action_reason": "<one sentence>", "next_step": "<one sentence>"}')


def _messages(alert, known_normal, networks, notable_events, site_environment="unknown"):
    from .rules import ENVIRONMENTS
    env = site_environment if isinstance(site_environment, str) and site_environment in ENVIRONMENTS else "unknown"
    ctx = {"site": {"environment": env, "means": ENVIRONMENTS[env], "set_by": "the owner, in config.json"},
           "known_normal": known_normal, "networks": networks,
           "recent_notable_events": notable_events,
           # alert last; the rule's default action is withheld so the model's pick is its own
           "alert": {k: alert[k] for k in ("id", "rule", "severity", "title")}
                    | {"detail": {k: v for k, v in (alert.get("detail") or {}).items() if k != "default_action"}}}
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(ctx, indent=1)}]


def _parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        v = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    if not isinstance(v, dict) or v.get("assessment") not in ASSESSMENTS or v.get("confidence") not in CONFIDENCES:
        return None
    ev = v.get("evidence") if isinstance(v.get("evidence"), list) else []
    return {"assessment": v["assessment"], "confidence": v["confidence"],
            "reason": str(v.get("reason", ""))[:600], "evidence": [str(x)[:120] for x in ev][:10],
            "next_step": str(v.get("next_step", ""))[:300],
            # an ID outside the catalogue is dropped, and the rule's default action stands
            "action": v.get("action") if v.get("action") in actions.ACTIONS else None,
            "action_reason": str(v.get("action_reason", ""))[:300]}


def escalated(verdict):
    """Anything short of a confident benign goes to the human with a flag on it."""
    return (verdict is None or verdict["assessment"] != "likely_benign" or verdict["confidence"] == "low")


def assess(alert, cfg, known_normal, networks, notable_events, log=print, retries=1):
    msgs = _messages(alert, known_normal, networks, notable_events, cfg.get("site_environment", "unknown"))
    prompt_hash = hashlib.sha256(json.dumps(msgs, sort_keys=True).encode()).hexdigest()[:16]
    raw, verdict, t0 = "", None, time.time()
    for attempt in range(retries + 1):  # malformed output is rejected and retried, never routed on
        r = requests.post(cfg["model_url"], timeout=REQUEST_TIMEOUT_S, json={
            "messages": msgs, "max_tokens": 500, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False}})
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"].get("content") or ""
        verdict = _parse(raw)
        if verdict:
            break
        log(f"triage: alert {alert['id']} malformed output (attempt {attempt + 1})")
    out = {**(verdict or {"assessment": "unparsed", "confidence": "low", "reason": raw.strip()[:400],
                          "evidence": [], "next_step": ""}),
           "escalated": escalated(verdict), "model": cfg["model"], "quant": cfg.get("model_quant"),
           "prompt_hash": prompt_hash, "prompt": msgs, "raw_output": raw, "seconds": round(time.time() - t0, 1),
           "at": int(time.time())}
    log(f"triage: alert {alert['id']} -> {out['assessment']} ({out['confidence']}), "
        f"{'ESCALATED' if out['escalated'] else 'not escalated'}, {out['seconds']}s")
    return out


def server_up(cfg):
    try:
        return requests.get(cfg["model_url"].replace("/chat/completions", "/models"), timeout=3).ok
    except requests.RequestException:
        return False
