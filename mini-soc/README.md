# Mini SOC

A self-hosted security monitor for a UniFi network, running on one Mac. Build log and current status: [promiseandrisk.ai/mini-soc](https://promiseandrisk.ai/mini-soc/).

**Purpose:** automate as much of home and small-business security and connectivity management as can be shown to be safe, and show it before automating it. Every kind of action climbs the same ladder, one step at a time: **observe** (record and show), **recommend** (tell a person what to do), **propose** (prepare the change; a person approves it with one click), **act and report** (narrow, reversible, logged, undoable). Each step up needs a measured record from the step below: the false-alarm rate, and how often a person overrules it. Today everything is at observe or recommend, and the code only reads from the controller.

- **L1: rules.** Deterministic checks decide whether something is an alert. Yes or no.
- **L2: a local model.** Qwen3.8-27B (MLX, 8-bit, thinking off) assesses each alert, rates its confidence, and escalates anything short of a confident "likely benign".
- **Human.** Only a person closes an alert. No tool, model or agent can.

Every alert also carries a **recommended action** from a fixed catalogue (`minisoc/actions.py`): rename a device, confirm an admin change, find a rogue access point, reset a tampered label, and so on. Each rule has a default; the model may only pick from the actions that rule allows, and it can never override a tamper call or answer "no action" on a medium alert. Recommendations only: nothing is carried out.

A local **web UI** (`minisoc/webui.py`, http://127.0.0.1:8095) lists alerts and lets a person classify them: should it have fired, the correct assessment, the correct action, and why. Each classification is a training label; with the exact prompt the model saw, it exports as chat-format JSONL for LoRA fine-tuning with `mlx_lm.lora`. The desktop widget and menu-bar app open it.

**Segmentation recommendations** (web UI button, or `python -m minisoc.segmentation`): the model proposes a network segment for each connected device from a fixed catalogue (Trusted, Media, Internet only, IoT, Cameras, Infrastructure, Guest, Identify first), with suggested VLANs and firewall policies. A device's own name can only lower its trust. Trusted and Infrastructure need controller evidence (the MAC vendor or UniFi's fingerprint) that doesn't contradict itself, and Trusted always waits for a person to confirm. Game consoles go to Internet only unless traffic shows your other devices reaching them, and isolating a device UniFi fingerprints as yours or as infrastructure waits for review. Advisory only: nothing is changed on the controller.

An **inventory** page lists every device the controller has reported: UniFi's fingerprint (model, type, vendor, OS), the MAC vendor, first and last seen, the switch or access point it was last on with port, speed and signal, its address, its current VLAN and the recommended one, and links to its open alerts. Alert pages show the same connection details.

No alert data leaves the machine. There's no cloud model and no cloud fallback.

This folder is the code only. The runtime output describes a real home network, so it isn't published.

## Guardrails in the code

| Guardrail | Where |
|---|---|
| Read-only: an allowlist of six GETs and two query POSTs, the system log and the flow log (full-path match); anything else raises before a request is sent. Redirects refused, system proxies ignored. Writes, when an action earns them, will go through a separate component with its own narrow credential, a per-action allowlist, undo and an audit log; this reader stays read-only | `minisoc/unifi.py` |
| The console's self-signed certificate is pinned by SHA-256, so an impostor on the network never receives the API key | `minisoc/unifi.py` |
| The model annotates alerts and never opens, closes, silences or reorders one. Notifications are rule-driven, sent before triage and retried until delivered | `minisoc/triage.py`, `minisoc/watcher.py` |
| Free text reaches the model wrapped with who wrote it (device, console user, audit log), plus a standing rule that free text is evidence, never instruction | `minisoc/normalize.py` |
| Every decision is logged: tier (rules / L2 / human), rule version (git commit), model, quantisation, prompt hash, raw output, human decision | `verdicts` table, `minisoc/store.py` |
| The MCP server is read-only and has no acknowledge tool; acknowledging is a CLI command for a person | `minisoc/mcp_server.py`, `minisoc/ack.py` |
| Notification text goes to `osascript` as arguments after `--`, never spliced into AppleScript | `minisoc/notify.py` |
| The MCP server opens the database read-only, and labels the model's free text as model output from attacker-influenced input | `minisoc/mcp_server.py` |
| One bad record is skipped and reported, never allowed to stop polling; three failed polls in a row notify you | `minisoc/watcher.py` |
| The web UI has no login, so it guards against other web pages in the same browser: loopback only, Host allowlist (DNS rebinding), a custom header and JSON on every write, Origin check, strict CSP, and device names only ever inserted as text | `minisoc/webui.py`, `minisoc/web/app.js` |
| Training labels stay human: the form pre-fills nothing from the model, and the model's evidence only enters an example when the human agreed with it | `minisoc/webui.py`, `minisoc/web/app.js` |

## Rules

| Rule | Fires on | Severity |
|---|---|---|
| `new_device` | A MAC address never seen before | medium; low only for a randomised MAC on Wi-Fi with an ordinary hostname (the address bit alone never lowers it, since an attacker picks their own MAC) |
| `label_changed` | A console alias changes (immediately) or a hostname changes (same value on two consecutive polls, or unstable for three) | medium |
| `config_change` | Any admin configuration change, from any address | medium |
| `admin_login` / `admin_login_new_ip` | An admin login: known address / unknown address | low / medium |
| `ingest_error`, `event_gap` | A record the monitor couldn't read; the event fetch hit its page limit | medium |
| `unifi_security_event` | UniFi's own security detections (rogue AP, threat, IPS) | medium or high |
| `unfamiliar_event` | An event category the rules don't know | medium |
| `hostname_collision` | Two or more connected devices report the same hostname (a naming problem, or impersonation) | medium |

The first poll records every known device as the starting point and raises no alerts, apart from a summary, "identify this device" items for connected devices with no name, and "recently added device" items.

Dedup only suppresses repeats while an alert is open: acknowledging it lets the same thing fire again. Don't add iCloud Private Relay or other shared egress addresses to the baseline; they vouch for everyone behind them.

## Rogue access points

UniFi's rogue-AP event carries two things the other side controls: the network name (SSID, set by whoever runs it) and the radio address (BSSID, which any radio can claim). The monitor keeps both as claims and compares them with your own radios (read hourly from `stat/device`, radio fields only) and with every network name your radios have broadcast. It names a pattern instead of reassuring, and never says "probably yours":

| Pattern | What it means |
|---|---|
| clone | Another radio using the exact address of one of yours, whatever it calls its network |
| evil twin | Your network name on an address shaped like one of your access points' |
| impersonation | Your network name from hardware that isn't one of your access points, including a copy of one of your networks that's switched off |
| look-alike | A name that folds to yours: case, spacing, accents, invisible or blank characters, punctuation, Greek or Cyrillic look-alike letters |
| resembles | A name that contains yours or is a typo away (`Home-5G`, `Horne`): the usual shape of a phishing lure, or a neighbour's naming |
| unknown | The list of your network names isn't available, so nothing can be compared |
| confirm in UniFi | Not a network your access points broadcast, on an address shaped like your hardware's: its own mesh or setup radio, or a forgery |
| foreign | Not your network name and not your hardware |

`config.json` → `site_environment` (`dense`, `isolated` or `unknown`) changes only how a **foreign** or **resembling** network is read. Isolated raises both to high, because nothing else should be broadcasting there. Dense lowers a foreign network to low (a neighbour's, most likely), but only when its name is plainly ordinary; anything unusual keeps UniFi's severity, because a list of look-alike letters can never be complete. Clone, evil twin, impersonation and look-alike are high everywhere, since a crowded building is where an evil twin is easiest to run. UniFi's own high or critical is never lowered.

## Traffic and baselines

Every 15 minutes the watcher reads the gateway's flow log (`traffic-flows`) and keeps daily totals per device: internet services and domains, countries, gateway services, devices on other networks it talks to, and blocked flows. Storage is capped per device and day, because a device chooses its own destinations and ports.

The gateway only sees traffic it routes. Two devices on the same network talk through the switch and never appear, so on a flat network "no local traffic seen" proves nothing. Every summary says so, and the model is told so.

Each device gets a **baseline** of normal: services, main domains, countries, cross-network peers, its usual busy days, and the hours it's active. It learns for 14 days, then runs in **shadow mode**: deviations are listed on the web UI's Baseline page and never notified or sent to the model. A person marks each one expected or suspicious, which gives a false-alarm rate per kind of deviation before any of them is allowed to page. Anything that deviated stays out of later profiles until it's marked expected, and a day flagged for volume never raises the volume bar, so a slow ramp can't teach the baseline that it's normal. One check runs from day one: an IoT device moving over a gigabyte a day. A device with less than a week of its own history (one that joins later, or a quiet one) is compared with the whole network instead: a country or service no device uses, any traffic across networks, any blocked flow.

## Run it

Requires Apple Silicon, Python 3.12, and a UniFi OS console reachable from the Mac.

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp config.example.json config.json            # set host, site, key_file, tls_sha256
python3 -c "import ssl,hashlib; print(hashlib.sha256(ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate(('192.0.2.1',443)))).hexdigest())"   # your console's IP
cp baseline_seed.example.json baseline_seed.json
.venv/bin/python -m tests.unit                   # no network, no model
.venv/bin/python -m tests.webui_test             # web UI guards, loopback only
.venv/bin/python -m minisoc.watcher --no-triage   # one poll, rules only
```

Use a UniFi API key from an admin with the **View Only** role. The allowlist blocks writes either way, but the credential shouldn't be able to make them in the first place.

Scheduling (edit the paths in `launchd/` first; the third job keeps the web UI running):

```bash
cp launchd/com.example.qwen-mini-soc.model.plist launchd/com.example.qwen-mini-soc.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.example.qwen-mini-soc.model.plist   # keeps Qwen loaded on :8090
launchctl load ~/Library/LaunchAgents/com.example.qwen-mini-soc.plist         # polls every 2 minutes
launchctl load ~/Library/LaunchAgents/com.example.qwen-mini-soc.webui.plist   # web UI on :8095
```

The watcher writes `state/state.json`. The macOS menu-bar app and desktop widget in `mac/` read it. Set your team in `mac/project.yml` (a free personal team works; macOS won't list an unsigned widget), then build and install with `tools/install-mac.sh`, which also removes the build copy: a second registered copy of the app makes macOS drop the widget. The MCP server (`python -m minisoc.mcp_server`, stdio) serves the same store to an AI assistant, read-only.

Known, accepted risk: `mlx_lm.server` answers any web page open in a local browser (CORS `*`, no auth), and it honours `model` and `adapters` in the request body, so a page can queue requests or force it to load a different cached model or adapter. Triage requests use the server's default model, which it reloads if a page switched it, and a failed or odd triage fails safe: the rule's alert and notification stand, marked not triaged and escalated. Put an authenticating proxy in front of it if that matters to you.

Code is MIT-licensed, like the rest of this repository.
