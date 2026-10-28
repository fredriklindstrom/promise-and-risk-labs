// Qwen Mini SOC web UI. Rule for this file: data only ever reaches the page through textContent
// (via el()), never innerHTML. Device names and model text are attacker-influenced.
"use strict";

const view = document.getElementById("view");

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "width%") n.style.width = `${v}%`;  // CSSOM: allowed by the CSP, unlike style=""
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-MiniSOC": "1" },  // custom header: no cross-site form can send it
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

const fmt = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "";
const words = (s) => (s || "").replaceAll("_", " ");
const sev = (s) => el("span", { class: `sev ${s}` }, s);

async function refreshStatus() {
  try {
    const s = await api("/api/state");
    const b = document.getElementById("status");
    b.className = `status ${s.status}`;
    b.textContent = { green: "All clear", amber: "Review", red: "Alert", unknown: "No data" }[s.status] || s.status;
    b.title = s.last_poll_ok ? `last poll ${fmt(s.last_poll_ok)}` : "no successful poll";
  } catch { /* status is decoration; the page still works */ }
}

// ---------------------------------------------------------------- alert list
let listFilter = "open";

async function renderList() {
  const alerts = await api(`/api/alerts?status=${encodeURIComponent(listFilter)}`);
  const filters = el("div", { class: "filters" },
    ["open", "acked", "all"].map((f) =>
      el("button", { class: f === listFilter ? "on" : "", onclick: () => { listFilter = f; route(); } },
        { open: "Open", acked: "Closed", all: "All" }[f])));
  const rows = alerts.map((a) => el("li", {},
    el("a", { class: "row", href: `#/alert/${a.id}` },
      el("div", {}, sev(a.severity)),
      el("div", {},
        el("div", { class: "title" }, a.title,
          a.triage && a.triage.escalated ? el("span", { class: "tag esc" }, "escalated") : null,
          a.labelled ? el("span", { class: "tag lab" }, "classified") : null,
          a.occurrences > 1 ? el("span", { class: "tag" }, `${a.occurrences} occurrences`) : null),
        a.occurrences > 1 ? el("div", { class: "sub" }, Object.entries(a.kinds || {}).map(([k, n]) => `${words(k)} ×${n}`).join(" · "),
          `  ·  last ${fmt(a.last_at)}`) : null,
        a.wifi ? el("div", { class: "sub" }, "SSID ",
          el("span", { class: "untrusted inline" }, (a.wifi.ssid && a.wifi.ssid.text) || "?"),
          a.wifi.bssid ? `  ·  BSSID ${a.wifi.bssid}` : "",
          a.wifi.facts && a.wifi.facts.pattern ? `  ·  ${a.wifi.facts.pattern.split(":")[0]}` : "") : null,
        el("div", { class: "sub" },
          a.recommended_action && a.recommended_action.action
            ? `→ ${a.recommended_action.action_source === "model" ? "Qwen: " : ""}${a.recommended_action.action}` : "",
          a.triage && a.triage.assessment ? `  ·  model: ${words(a.triage.assessment)} (${a.triage.confidence || "?"})` : "")),
      el("div", { class: "meta" }, `#${a.id}`, el("br"), fmt(a.created)))));
  const openCount = listFilter === "open" ? alerts.length : null;
  view.replaceChildren(
    el("h1", {}, "Alerts"), filters,
    openCount ? el("p", {}, el("a", { class: "btn primary", href: "#/triage" }, `Triage mode: work through ${openCount} open issue${openCount === 1 ? "" : "s"} →`)) : null,
    alerts.length ? el("ul", { class: "list" }, rows) : el("p", { class: "muted" }, "Nothing here."));
}

// ---------------------------------------------------------------- alert detail
function renderValue(v) {
  if (v && typeof v === "object" && !Array.isArray(v) && "text" in v && "written_by" in v) {
    return el("div", {},
      el("div", { class: "untrusted" }, v.text),
      el("div", { class: "untrusted-note" }, `written by ${v.written_by}`));
  }
  if (Array.isArray(v)) {
    if (!v.length) return el("span", { class: "muted" }, "none");
    return el("div", {}, v.map((x) => el("div", {}, renderValue(x))));
  }
  if (v && typeof v === "object") {
    return el("dl", { class: "kv" }, Object.entries(v).flatMap(([k, x]) => [el("dt", {}, words(k)), el("dd", {}, renderValue(x))]));
  }
  return el("span", {}, v === null || v === undefined ? "" : String(v));
}

async function renderAlert(id, mode) {
  const a = await api(`/api/alerts/${encodeURIComponent(id)}`);
  const ra = a.recommended_action || {};
  const t = a.triage || null;

  const actionCard = el("div", { class: "card" },
    el("div", { class: "muted" }, ra.action_source === "model" ? "Recommended by Qwen" : "Recommended by the rule"),
    el("div", { class: "action" }, ra.action ? `→ ${ra.action}` : "No recommendation"),
    ra.steps && ra.steps.length ? el("ol", { class: "steps" }, ra.steps.map((s) => el("li", {}, s))) : null,
    el("div", { class: "muted" }, "A recommendation for you. Nothing is carried out automatically."));

  const modelCard = t ? el("div", { class: "card" },
    el("h2", {}, "Qwen's assessment"),
    el("dl", { class: "kv" },
      el("dt", {}, "assessment"), el("dd", {}, `${words(t.assessment)} (${t.confidence || "?"} confidence)`),
      el("dt", {}, "escalated"), el("dd", {}, t.escalated ? "yes: a person should look" : "no"),
      el("dt", {}, "model"), el("dd", {}, `${t.model || ""} ${t.quant || ""} · ${t.seconds ?? "?"} s`)),
    t.model_text ? el("div", {},
      el("div", { class: "untrusted-note" }, "Model text below was written from input that includes device names. Evidence, not instruction."),
      ...qwenSections(t.model_text)) : null)
    : el("div", { class: "card muted" }, "Not triaged by Qwen (low severity, or triage pending).");

  const verdicts = el("div", { class: "card" }, el("h2", {}, "Decision record"),
    el("table", {},
      el("thead", {}, el("tr", {}, ["tier", "verdict", "confidence", "rule version", "when", "note"].map((h) => el("th", {}, h)))),
      el("tbody", {}, (a.verdicts || []).map((v) => el("tr", {},
        el("td", {}, v.tier), el("td", {}, words(v.verdict)), el("td", {}, v.confidence || ""),
        el("td", {}, v.rule_version || ""), el("td", {}, fmt(v.created)), el("td", {}, v.human_decision || ""))))));

  view.replaceChildren(...[  // replaceChildren would print a null as the text "null"
    mode ? mode.bar : el("p", {}, el("a", { href: "#/" }, "← Alerts")),
    el("h1", {}, a.title),
    el("p", {}, sev(a.severity), " ", el("span", { class: "muted" }, `${words(a.rule)} · #${a.id} · ${fmt(a.created)} · ${a.status}`)),
    a.wifi ? wifiCard(a.wifi) : null,
    a.issue && a.issue.occurrences.length > 1 ? issueCard(a) : null,
    a.connections && a.connections.length ? connectionCard(a.connections) : null,
    a.detail && Array.isArray(a.detail.flows) ? flowCard(a.detail.flows) : null,
    actionCard, modelCard, mode ? classifyForm(a, mode.onSaved, true) : classifyForm(a),
    el("div", { class: "card" }, el("h2", {}, "Evidence"), renderValue(a.detail)),
    verdicts].filter(Boolean));
}

function qwenSections(m) {
  // the same four parts as your investigation; every line is the model's text, so it stays marked untrusted
  const block = (title, text) => text ? [el("h3", {}, title), el("div", { class: "untrusted" }, text)] : [];
  const checked = (Array.isArray(m.checked) ? m.checked : []).map((c) => `${c.what}: ${c.showed}`).join("\n");
  return [
    ...block("1. What Qwen saw", m.seen),
    ...block("2. What it checked", checked),
    ...block("3. What it concluded", m.reason),
    ...block("4. What it recommends", [m.action_reason, m.next_step].filter(Boolean).join("\n\n")),
  ];
}

function issueCard(a) {
  const i = a.issue;
  const open = i.occurrences.filter((o) => o.status === "open").length;
  return el("div", { class: "card scroll" },
    el("h2", {}, i.is_first ? `Issue #${i.id} · ${i.occurrences.length} occurrences (${open} open)`
      : `Occurrence of issue #${i.id}`),
    !i.is_first ? el("p", {}, el("a", { href: `#/alert/${i.id}` }, `→ open issue #${i.id}`),
      el("span", { class: "muted" }, " to see every occurrence and close the whole issue.")) : null,
    el("p", { class: "muted" }, `Groups ${i.about}. Alerts about the same join this issue while it's open. `
      + "Grouping never keeps an occurrence from Qwen. Repeats of the same kind are summarised in at most one notification an hour; "
      + "a threat repeats only when it's the same source and target with the same IPS category, protocol and service port; "
      + "anything else high, a tamper call, a new kind of alert, a new attacker or a higher severity notifies on its own. "
      + "Closing closes only what's listed here; anything that arrives or joins while you read stays open. "
      + "Once it's closed, the next alert about these starts a new issue."),
    el("table", {}, el("thead", {}, el("tr", {}, ["#", "when", "what", "severity", "status"].map((h) => el("th", {}, h)))),
      el("tbody", {}, i.occurrences.map((o) => el("tr", {},
        el("td", {}, o.id === a.id ? `#${o.id} (this)` : el("a", { href: `#/alert/${o.id}` }, `#${o.id}`)),
        el("td", {}, fmt(o.created)), el("td", {}, words(o.rule), el("div", { class: "muted" }, o.title)),
        el("td", {}, sev(o.severity)), el("td", {}, o.status, o.triaged ? el("div", { class: "muted" }, "assessed") : null))))));
}

function flowCard(flows) {
  if (!flows.length) return el("div", { class: "card muted" }, "No matching flow found in the gateway's flow log for this event.");
  const who = (x) => !x ? "" : typeof x === "string" ? x
    : [x.name && x.name.text, x.hostname && x.hostname.text].filter(Boolean)[0] || x.mac;
  const end = (e) => el("div", {},
    el("div", {}, `${e.ip || "?"}${e.port !== undefined ? `:${e.port}` : ""}`),
    el("div", { class: "muted" }, e.mac ? `MAC ${e.mac}` : ""),
    e.mac_is ? el("div", { class: typeof e.mac_is === "string" ? "muted" : "" },
      typeof e.mac_is === "string" ? e.mac_is : el("span", {}, "your device ", el("span", { class: "untrusted inline" }, who(e.mac_is)),
        e.mac_is.ip ? ` (its address ${e.mac_is.ip})` : "")) : null,
    e.ip_belongs_to ? el("div", {}, "address belongs to ", el("span", { class: "untrusted inline" }, who(e.ip_belongs_to)),
      ` (${e.ip_belongs_to.mac})`) : null);
  return el("div", { class: "card scroll" },
    el("h2", {}, "The blocked flow (from the gateway's flow log)"),
    el("table", {}, el("thead", {}, el("tr", {}, ["when", "rule", "source", "destination", "protocol"].map((h) => el("th", {}, h)))),
      el("tbody", {}, flows.map((f) => el("tr", {},
        el("td", {}, f.time || "", el("div", { class: "muted" }, [f.action, f.risk && `risk ${f.risk}`, f.direction].filter(Boolean).join(" · "))),
        el("td", {}, (f.rules || []).map((r) => el("div", {}, [r.name, r.category && words(r.category).toLowerCase()].filter(Boolean).join(" · "))),
          el("div", { class: "muted" }, f.network || "")),
        el("td", {}, end(f.source)), el("td", {}, end(f.destination)),
        el("td", {}, [f.protocol, f.service].filter(Boolean).join(" · ")))))),
    flows.some((f) => f.facts && f.facts.length) ? el("div", {}, el("h2", {}, "What doesn't add up"),
      el("ul", {}, [...new Set(flows.flatMap((f) => f.facts || []))].map((t) => el("li", {}, t)))) : null);
}

function speed(mbps) {
  if (!mbps) return "not reported";
  return mbps >= 1000 ? `${+(mbps / 1000).toFixed(1)} Gbps` : `${mbps} Mbps`;
}

function connectionCard(cs) {
  const wired = (c) => (c.link || "") === "wired";
  return el("div", { class: "card" },
    el("h2", {}, "Where it's connected"),
    el("table", {},
      el("thead", {}, el("tr", {}, ["device", "connected to", wired(cs[0]) || cs.length > 1 ? "port" : "network", "speed", "IP"].map((h) => el("th", {}, h)))),
      el("tbody", {}, cs.map((c) => el("tr", {},
        el("td", {}, c.mac, el("div", { class: "muted" }, c.active ? "online now" : `last seen ${fmt(c.last_seen)}`)),
        el("td", {}, c.uplink_name || (c.uplink_mac ? "UniFi device" : "not reported"),
          el("div", { class: "muted" }, [c.uplink_model, c.uplink_mac].filter(Boolean).join(" · "))),
        el("td", {}, wired(c) ? (c.port ? `port ${c.port}` : "not reported") : (c.link || "Wi-Fi"),
          !wired(c) && c.signal_dbm ? el("div", { class: "muted" }, `signal ${c.signal_dbm} dBm`) : null),
        el("td", {}, speed(c.link_mbps)),
        el("td", {}, c.ip || el("span", { class: "muted" }, "not reported")))))),
    el("div", { class: "muted" }, "As UniFi last reported it. Names of UniFi devices are set in the console."));
}

function wifiCard(w) {
  const f = w.facts || {};
  const exact = f.bssid_is_one_of_your_radios || [];
  const near = f.bssid_shares_bytes_with || [];
  const nm = (x) => (x && x.text) || "";
  return el("div", { class: "card" },
    el("h2", {}, "The Wi-Fi network"),
    el("p", { class: "action" }, f.pattern || ""),
    el("dl", { class: "kv" },
      el("dt", {}, "network name (SSID)"), el("dd", {}, renderValue(w.ssid)),
      el("dt", {}, "hardware address (BSSID)"), el("dd", {}, w.bssid || "not recorded",
        el("div", { class: "untrusted-note" }, `claimed by ${w.bssid_note}`)),
      el("dt", {}, "channel · signal"), el("dd", {}, `${w.channel || "?"} · ${w.rssi || "?"}`),
      el("dt", {}, "seen by"), el("dd", {}, w.nearest_ap || "?"),
      el("dt", {}, "your surroundings"), el("dd", {}, `${f.site_environment || "unknown"}: ${f.site_environment_means || ""}`),
      el("dt", {}, "one of your network names?"), el("dd", {},
        f.ssid_is_one_of_yours === true ? "yes" : f.ssid_is_one_of_yours === false ? "no" : "unknown",
        f.your_ssids && f.your_ssids.length ? el("span", { class: "muted" }, `  (yours: ${f.your_ssids.map((s) => (s && s.text) || "").join(", ")})`) : null),
      el("dt", {}, "exact address of your radio?"), el("dd", {},
        exact.length ? exact.map((x) => el("div", {}, `yes: ${nm(x.device)}'s ${nm(x.essid)} network (${x.bssid})`))
          : f.radio_inventory_known ? "no" : "unknown (radio list not loaded yet)"),
      el("dt", {}, "shares bytes with"), el("dd", {},
        near.length ? near.map((d) => el("div", {}, `${nm(d.name) || d.model || "device"} (${d.mac})`)) : "none of your hardware")),
    el("p", { class: "muted" }, "A BSSID is broadcast in the clear and any radio can claim one, so a resemblance never proves a network is yours. Confirm in UniFi which radio broadcasts it."));
}

function classifyForm(a, onSaved, closeByDefault) {
  const o = a.options;
  const inv = a.investigation || {};
  const cur = a.label || {};
  const t = a.triage || {};
  const radio = (name, value, text, checked) =>
    el("label", {}, el("input", { type: "radio", name, value, checked: checked || null, required: true }), text);
  const msg = el("span", {});
  const save = el("button", { class: "btn primary", type: "submit" }, cur.created ? "Update classification" : "Save classification");
  const form = el("form", { class: "classify card", onsubmit: async (ev) => {
      ev.preventDefault();
      if (save.disabled) return;
      save.disabled = true;  // a double click must not land on the next issue in triage mode
      const f = new FormData(form);
      msg.className = ""; msg.textContent = "Saving…";
      try {
        await api(`/api/alerts/${a.id}/label`, {
          should_fire: f.get("should_fire"), assessment: f.get("assessment"),
          action: f.get("action") || null, note: f.get("note") || "", close: f.get("close") === "on",
          checks: f.get("checks") || "", confidence: f.get("confidence") || null, action_reason: f.get("action_reason") || "",
          // exactly the occurrences on screen: anything else in the issue stays open
          seen_ids: a.issue ? a.issue.occurrences.map((o) => o.id) : [a.id],
        });
        msg.className = "ok"; msg.textContent = "Saved.";
        if (onSaved) onSaved(f.get("close") === "on"); else setTimeout(route, 400);
      } catch (e) { msg.className = "err"; msg.textContent = e.message; save.disabled = false; }
    } },
    el("h2", {}, "Investigation"),
    el("p", {}, el("a", { href: `/api/alerts/${a.id}/report.md`, download: "" }, "Export report (private)"), " · ",
      el("a", { href: `/api/alerts/${a.id}/report.md?public=1`, download: "" }, "Export report (public, scrubbed)"),
      el("span", { class: "muted" }, " · the public version replaces MACs and addresses, even in your notes; read it before you publish")),
    el("h3", {}, "1. What did you see?"),
    el("ul", {}, (inv.seen || []).map((x) => el("li", {}, x))),
    el("h3", {}, "2. What did you investigate, and why?"),
    el("p", { class: "muted" }, "Checks the Mini SOC ran:"),
    el("ul", {}, (inv.checks || []).map((c) => el("li", {}, el("strong", {}, `${c.check}: `), c.found))),
    el("fieldset", {}, el("legend", {}, "Your own checks (what you looked at, and why)"),
      el("textarea", { name: "checks", rows: 3, maxlength: 2000 }, cur.checks || "")),
    el("h3", {}, "3. What did you conclude?"),
    closeByDefault && cur.created ? el("p", { class: "err" }, `You classified this before (${fmt(cur.created)}). Check your answers: saving will also close it.`) : null,
    el("p", { class: "muted" }, "Your answer is the training label: it records what the rule and Qwen should have said."),
    el("fieldset", {}, el("legend", {}, "Should this alert have fired?"),
      radio("should_fire", "yes", "Yes, worth a look", cur.should_fire !== "no"),
      radio("should_fire", "no", "No, the rule was wrong", cur.should_fire === "no")),
    // nothing pre-selected from Qwen: a label must be your own answer, not a click-through of the model's
    el("fieldset", {}, el("legend", {}, "The correct assessment"),
      t.assessment ? el("p", { class: "muted" }, `Qwen said: ${words(t.assessment)}`) : null,
      o.assessments.map((x) => radio("assessment", x, words(x), cur.assessment === x))),
    el("fieldset", {}, el("legend", {}, "How confident are you?"),
      (inv.confidence_options || ["high", "medium", "low"]).map((x) => el("label", {},
        el("input", { type: "radio", name: "confidence", value: x, checked: cur.confidence === x || null }), x))),
    el("fieldset", {}, el("legend", {}, "Why (your conclusion; it becomes the reason in the training example, and examples without one aren't exported)"),
      el("textarea", { name: "note", rows: 3, maxlength: 500 }, cur.note || "")),
    el("h3", {}, "4. What would you recommend next?"),
    el("fieldset", {}, el("legend", {}, "The correct action"),
      el("select", { name: "action", required: true },
        el("option", { value: "", selected: cur.action ? null : true }, "Choose the action you'd want…"),
        o.actions.map((x) => el("option", { value: x.id, selected: cur.action === x.id || null }, x.title)))),
    el("fieldset", {}, el("legend", {}, "Why that action"),
      el("textarea", { name: "action_reason", rows: 2, maxlength: 1000 }, cur.action_reason || "")),
    inv.required_to_close && a.status === "open"
      ? el("p", { class: "muted" }, "Closing a medium or high issue needs the full record: your conclusion, confidence, the action and why.") : null,
    el("label", {}, el("input", { type: "checkbox", name: "close", checked: a.status === "open" ? (closeByDefault || null) : true }),
      a.status !== "open" ? "Alert is closed"
        : a.issue && a.issue.is_first && a.issue.occurrences.length > 1 ? `Also close this issue (all ${a.issue.occurrences.length} occurrences)`
        : a.issue && !a.issue.is_first ? "Also close this occurrence (the issue stays open)" : "Also close this alert"),
    el("div", {}, save, " ", msg));
  return form;
}

// ---------------------------------------------------------------- training data
async function renderTraining() {
  const s = await api("/api/training");
  const pct = Math.min(100, Math.round((s.ready / s.target) * 100));
  view.replaceChildren(
    el("h1", {}, "Training data"),
    el("div", { class: "card" },
      el("div", {}, `${s.ready} of ${s.target} labelled examples ready for a first LoRA run`),
      el("div", { class: "meter" }, el("span", { "width%": pct })),
      el("dl", { class: "kv" },
        el("dt", {}, "classified alerts"), el("dd", {}, s.labelled),
        el("dt", {}, "ready (prompt + label + reason)"), el("dd", {}, s.ready),
        el("dt", {}, "missing the exact prompt"), el("dd", {}, `${s.no_prompt} (triaged before prompts were kept)`),
        el("dt", {}, "missing a reason"), el("dd", {}, `${s.no_reason} (add a note to the classification)`),
        el("dt", {}, "Qwen agreed with you"), el("dd", {}, `${s.model_agreed} of ${s.model_compared} on assessment and action`))),
    el("div", { class: "card" },
      el("h2", {}, "Export"),
      el("p", {}, "Chat-format JSONL, one example per line, in the shape mlx_lm.lora trains on."),
      el("p", {}, el("a", { class: "btn", href: "/api/training/export.jsonl", download: "minisoc-train.jsonl" }, "Download training JSONL")),
      el("p", { class: "muted" }, "Fine-tuning isn't run from here yet. With enough examples, the first run will be:"),
      el("div", { class: "untrusted" }, s.lora_command)));
}

// ---------------------------------------------------------------- segmentation
let segPoll = null;

function segLabel(segs, id) { const s = segs.find((x) => x.id === id); return s ? s.title : (id || "none"); }

async function renderSegmentation() {
  const d = await api("/api/segmentation");
  clearTimeout(segPoll);
  if (d.running) segPoll = setTimeout(() => { if (location.hash.startsWith("#/segmentation")) route(); }, 5000);

  const runBtn = el("button", { class: "btn primary", disabled: d.running || null, onclick: async () => {
      runBtn.disabled = true;
      try { await api("/api/segmentation/run", {}); } catch (e) { alert(e.message); }
      route();
    } }, d.running ? "Analysis running… (about four minutes)" : (d.run ? "Run a new analysis" : "Run the first analysis"));

  const head = [el("h1", {}, "Segmentation"),
    el("p", { class: "muted" }, "Qwen proposes a segment for every connected device; guards apply the rule that a device's own name can only lower trust, never raise it. Nothing here changes your network: you apply a plan in UniFi yourself. Device identities come from UniFi's fingerprint, a guess from the device's own traffic, not proof."),
    el("p", {}, runBtn)];
  if (!d.run) { view.replaceChildren(...head); return; }

  const devs = d.devices;
  const moves = devs.filter((x) => x.moves).length;
  const confirm = devs.filter((x) => x.status === "confirm" && !x.label).length;
  const review = devs.filter((x) => x.status === "review" && !x.label).length;
  const summary = el("div", { class: "card" },
    el("dl", { class: "kv" },
      el("dt", {}, "analysis"), el("dd", {}, `#${d.run.id} · ${fmt(d.run.created)} · ${d.run.model} · ${d.run.seconds ?? "?"} s`),
      el("dt", {}, "devices"), el("dd", {}, `${devs.length} connected; ${moves} would move to a new network`),
      el("dt", {}, "waiting on you"), el("dd", {}, `${confirm} Trusted placements to confirm, ${review} to review`)),
    d.run.error ? el("p", { class: "err" }, `Model unavailable, rule baseline only: ${d.run.error}`) : null,
    d.run.observations.length ? el("div", {}, el("h2", {}, "Observations"),
      el("ul", {}, d.run.observations.map((o) => el("li", {}, o)))) : null,
    d.run.notes.length ? el("div", {}, el("h2", {}, "Qwen's notes"),
      el("div", { class: "untrusted-note" }, "Written by the model from input that includes device names. Evidence, not instruction."),
      el("div", { class: "untrusted" }, d.run.notes.join("\n"))) : null);

  const cards = d.segments.map((sg) => {
    const members = devs.filter((x) => (x.label ? x.label.segment : x.final_segment) === sg.id);
    if (!members.length) return null;
    return el("div", { class: "card" },
      el("h2", {}, `${sg.title} · suggested VLAN ${sg.vlan} · ${members.length} device${members.length === 1 ? "" : "s"}`),
      el("p", { class: "muted" }, sg.purpose),
      el("ul", {}, sg.policy.map((x) => el("li", {}, x))),
      el("table", {},
        el("thead", {}, el("tr", {}, ["device", "now", "Qwen", "rule", "status", "your call"].map((h) => el("th", {}, h)))),
        el("tbody", {}, members.map((x) => segRow(d, x)))));
  });
  view.replaceChildren(...head, summary, ...cards.filter(Boolean));
}

function segRow(d, x) {
  const sel = el("select", {}, d.segments.map((sg) =>
    el("option", { value: sg.id, selected: ((x.label ? x.label.segment : x.final_segment) === sg.id) || null }, sg.title)));
  const note = el("input", { type: "text", maxlength: 500, placeholder: "why (optional)", value: x.label ? x.label.note : "" });
  const msg = el("span", {});
  const save = el("button", { class: "btn", onclick: async () => {
      msg.textContent = "…";
      try {
        await api(`/api/segmentation/${d.run.id}/${x.mac}/label`, { segment: sel.value, note: note.value });
        msg.className = "ok"; msg.textContent = "saved";
      } catch (e) { msg.className = "err"; msg.textContent = e.message; }
    } }, x.label ? "Update" : (x.status === "confirm" ? "Confirm" : "Save"));
  return el("tr", {},
    el("td", {}, el("div", {}, x.what), el("div", { class: "muted" }, x.mac, x.link ? ` · ${x.link}` : ""),
      x.hostname ? el("div", { class: "untrusted" }, x.hostname.text) : null,
      x.name ? el("div", { class: "untrusted" }, x.name.text) : null),
    el("td", {}, `${x.current_network || "?"}${x.current_vlan ? ` (VLAN ${x.current_vlan})` : ""}`, x.moves ? el("div", { class: "muted" }, "→ moves") : null),
    el("td", {}, segLabel(d.segments, x.model_segment), x.model_confidence ? el("div", { class: "muted" }, x.model_confidence) : null,
      x.model_reason ? el("div", { class: "untrusted" }, x.model_reason) : null),
    el("td", {}, segLabel(d.segments, x.rule_segment)),
    el("td", {}, x.label ? el("span", { class: "tag lab" }, "decided") :
      el("span", { class: `tag ${x.status === "review" ? "esc" : ""}` }, x.status), x.note ? el("div", { class: "muted" }, x.note) : null),
    el("td", {}, sel, note, save, " ", msg));
}

// ---------------------------------------------------------------- inventory
const inv = { show: "all", q: "" };

async function renderInventory() {
  const d = await api("/api/inventory");
  const txt = (x) => (x && x.text) || "";
  const who = (x) => (x.fingerprint && x.fingerprint.model) || x.vendor || "unknown";
  const hay = (x) => [x.mac, x.ip, txt(x.hostname), txt(x.name), x.vendor, x.network, x.link, x.uplink_name,
    ...Object.values(x.fingerprint || {})].filter(Boolean).join(" ").toLowerCase();
  const counts = { all: d.devices.length, online: d.devices.filter((x) => x.online).length };
  counts.offline = counts.all - counts.online;
  counts.unidentified = d.devices.filter((x) => !x.fingerprint).length;
  counts.moves = d.devices.filter((x) => x.recommended.moves).length;
  const toCheck = (x) => x.exposure && x.exposure.priority !== "low";
  counts["php2shell"] = d.devices.filter(toCheck).length;
  const keep = (x) => (inv.show === "all" || (inv.show === "online" ? x.online : inv.show === "offline" ? !x.online
      : inv.show === "moves" ? x.recommended.moves : inv.show === "php2shell" ? toCheck(x) : !x.fingerprint))
    && (!inv.q || hay(x).includes(inv.q.toLowerCase()));

  const body = el("tbody", {});
  const shown = el("span", { class: "muted" });
  const draw = () => {
    const rows = d.devices.filter(keep);
    if (inv.show === "php2shell") rows.sort((a, b) => EXPOSURE_ORDER[a.exposure.priority] - EXPOSURE_ORDER[b.exposure.priority]);
    shown.textContent = `${rows.length} of ${d.devices.length} devices`;
    body.replaceChildren(...rows.map((x) => el("tr", {},
      el("td", {}, el("div", {}, who(x)), el("div", { class: "muted" }, x.mac, x.mac_randomised ? " · private address" : ""),
        x.name ? el("div", { class: "untrusted" }, x.name.text) : null,
        x.hostname ? el("div", { class: "untrusted" }, x.hostname.text) : null,
        x.open_alerts.length ? el("div", {}, x.open_alerts.map((id) => [el("a", { href: `#/alert/${id}` }, `#${id}`), " "])) : null,
        baselineLine(x.baseline),
        x.blocked_7d ? el("div", {}, el("a", { href: `#/blocked/${x.mac}` }, `${x.blocked_7d} blocked in 7 days`)) : null),
      el("td", {}, x.fingerprint
        ? el("div", {}, [x.fingerprint.type, x.fingerprint.family].filter(Boolean).join(" · "),
            el("div", { class: "muted" }, [x.fingerprint.vendor, x.fingerprint.os].filter(Boolean).join(" · ")))
        : el("span", { class: "muted" }, "not fingerprinted"),
        x.vendor ? el("div", { class: "muted" }, `MAC vendor: ${x.vendor}`) : null),
      el("td", {}, x.online ? el("span", { class: "ok" }, "online now") : fmt(x.last_seen),
        el("div", { class: "muted" }, `first seen ${fmt(x.first_seen)}`)),
      el("td", {}, x.uplink_name || (x.uplink_mac ? "UniFi device" : el("span", { class: "muted" }, "not reported")),
        el("div", { class: "muted" }, [(x.link || "") === "wired" ? (x.port ? `port ${x.port}` : "") : x.link,
          x.link_mbps ? speed(x.link_mbps) : "", x.signal_dbm ? `${x.signal_dbm} dBm` : ""].filter(Boolean).join(" · "))),
      el("td", {}, x.ip || el("span", { class: "muted" }, "none"), el("div", { class: "muted" }, x.network || "")),
      el("td", {}, x.current_vlan === null ? el("span", { class: "muted" }, "?") : `VLAN ${x.current_vlan}`),
      el("td", {}, recCell(x.recommended), trafficLine(x.traffic)),
      el("td", {}, exposureCell(x.exposure)))));
  };

  const filters = el("div", { class: "filters" },
    ["all", "online", "offline", "unidentified", "moves", "php2shell"].map((k) => el("button", {
      class: inv.show === k ? "on" : null,
      onclick: (e) => { inv.show = k; e.target.parentNode.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === e.target)); draw(); },
    }, `${k === "php2shell" ? "PHP2Shell: to check" : k} (${counts[k]})`)),
    el("input", { type: "search", class: "search", placeholder: "search name, MAC, IP, model, switch…", value: inv.q,
      oninput: (e) => { inv.q = e.target.value; draw(); } }));

  view.replaceChildren(
    el("h1", {}, "Inventory"),
    el("p", { class: "muted" }, "Every device UniFi has reported, online first, then by when it was last seen. Identity comes "
      + "from UniFi's fingerprint of each device's network behaviour (DHCP/mDNS): harder to fake than a name, not proof. "
      + "UniFi hasn't fingerprinted every device; those show the MAC vendor only. Names are written by devices and console "
      + "users, shown as plain text."),
    el("p", { class: "muted" }, "Recommended VLAN: your own decision on the Segmentation page first, then the latest "
      + "analysis there, then the device type. Traffic: " + d.traffic_visibility
      + (d.traffic_since ? ` Collecting since ${fmt(d.traffic_since)}.` : " Not collected yet.")),
    exposureCard(d.exposure), filters, el("p", {}, shown),
    el("div", { class: "card scroll" }, el("table", {},
      el("thead", {}, el("tr", {}, ["device", "UniFi fingerprint", "last seen", "where", "IP", "VLAN now", "recommended",
        "likely running · PHP"]
        .map((h) => el("th", {}, h)))),
      body)));
  draw();
}

const EXPOSURE_ORDER = { act: 0, check: 1, low: 2 };
const EXPOSURE_TEXT = { act: "check first", check: "check", low: "low" };

function exposureCard(e) {
  if (!e) return null;
  const a = e.advisory;
  return el("div", { class: "card" },
    el("strong", {}, a.title), el("span", { class: "muted" }, ` · published ${a.published}`),
    el("p", {}, a.summary),
    el("p", {}, `Fixed in PHP ${a.fixed}. ${a.older}`),
    el("p", { class: "muted" }, e.visibility),
    e.port_forwards_read_at
      ? el("div", {}, `Port forwards (read ${fmt(e.port_forwards_read_at)}): `,
          e.port_forwards.length ? null : "none found.",
          ...e.port_forwards.map((f) => el("div", { class: f.online_holder ? null : "err" },
            f.text, f.online_holder ? "" : " · no device online holds this address: find out what it reaches")))
      : el("p", { class: "muted" }, "Port forwards not read yet."));
}

function exposureCell(x) {
  if (!x) return null;
  return el("div", {},
    el("div", {}, x.runs),
    el("div", {}, `PHP ${x.php}`, el("span", { class: x.priority === "low" ? "tag" : "tag esc" }, EXPOSURE_TEXT[x.priority])),
    el("details", {}, el("summary", { class: "muted" }, "why"),
      ...x.reasons.map((r) => el("div", { class: "muted" }, r)),
      ...x.evidence.map((r) => el("div", { class: "muted" }, r)),
      el("div", {}, x.check)));
}

function baselineLine(b) {
  if (!b || b.state === "off") return null;
  const text = b.state === "learning" ? `baseline: learning (${b.days_seen} of ${b.needs} days with traffic)`
    : `baseline: ${b.services} services · ${b.domains} domains · ${b.regions} regions`;
  return el("div", { class: "muted" }, text,
    b.unreviewed ? [" · ", el("a", { href: "#/baseline" }, `${b.unreviewed} to review`)] : null);
}

// ---------------------------------------------------------------- blocked
let blockedDays = 7;

async function renderBlocked(mac) {
  const q = new URLSearchParams({ days: String(blockedDays) });
  if (mac) q.set("mac", mac);
  const d = await api(`/api/blocked?${q}`);
  const who = (g) => [g.device_name && g.device_name.text, g.device_hostname && g.device_hostname.text].filter(Boolean)[0] || g.mac;
  const dest = (g) => g.destination.address !== undefined
    ? el("span", {}, g.destination.address || "unknown address")
    : el("span", { class: "untrusted" }, g.destination.text);
  const rows = d.groups.map((g) => el("tr", {},
    el("td", {}, el("a", { href: `#/blocked/${g.mac}` }, el("span", { class: "untrusted" }, who(g))),
      el("div", { class: "muted" }, g.mac)),
    el("td", {}, dest(g), g.addresses > 1 ? el("div", { class: "muted" }, `${g.addresses} addresses`) : null),
    el("td", {}, g.country || el("span", { class: "muted" }, "?"),
      g.network ? el("div", { class: "muted untrusted" }, g.network.text) : null),
    el("td", {}, [g.rule.type, g.rule.name].filter(Boolean).map(words).join(" · ") || "?"),
    el("td", {}, [g.direction, g.protocol, g.ports.length ? `port ${g.ports.join(", ")}` : ""].filter(Boolean).join(" · ")),
    el("td", {}, String(g.count)),
    el("td", {}, g.last || "", el("details", {}, el("summary", { class: "muted" }, "exact times"),
      ...g.recent.map((t) => el("div", { class: "muted" }, t))))));
  const notStored = Object.values(d.not_stored || {}).reduce((a, b) => a + b, 0);
  view.replaceChildren(
    el("h1", {}, "Blocked connections"),
    el("p", { class: "muted" }, d.about, d.since ? ` Collecting since ${fmt(d.since)}.` : " Not collected yet.",
      d.incomplete ? " In some moments a device was blocked so often in a single millisecond, or the controller answered "
        + "so slowly, that not every blocked connection could be listed; some from those moments are missing." : "",
      (d.gaps || []).map((g) => ` Not collected from ${fmt(g.from)} to ${fmt(g.to)} (`
        + (g.cause === "behind" ? "collection fell too far behind, during a flood or a controller outage"
          : "the SOC was off longer than the controller keeps its log") + ").").join(""),
      notStored ? ` About ${notStored} more past the per-device daily limit were counted but not stored.` : ""),
    el("div", { class: "filters" },
      [1, 7, 30, 90].map((n) => el("button", { class: blockedDays === n ? "on" : null,
        onclick: () => { blockedDays = n; renderBlocked(mac); } }, `${n} day${n > 1 ? "s" : ""}`)),
      mac ? el("a", { href: "#/blocked", class: "btn" }, "all devices") : null),
    d.behind_since ? el("p", { class: "err" }, `Catching up: blocked connections since ${fmt(d.behind_since)} `
      + "are still being collected.") : null,
    el("p", {}, d.groups.length < d.group_total
      ? `Showing ${d.groups.length} of ${d.group_total} groups`
        + (d.per_device_limit ? ` (each device's ${d.per_device_limit} biggest); open a device for all of its groups.` : ", the biggest.")
      : `${d.group_total} group${d.group_total === 1 ? "" : "s"}${mac ? ` for ${mac}` : ""}`),
    el("div", { class: "card scroll" }, el("table", {},
      el("thead", {}, el("tr", {}, ["device", "destination", "where", "rule", "how", "count", "last"].map((h) => el("th", {}, h)))),
      el("tbody", {}, ...rows))));
}

// ---------------------------------------------------------------- baseline
let baselineShow = null;  // "sample" or "all"; defaults to the sample once one is drawn

const pct = (x) => (x === null || x === undefined) ? "–" : `${Math.round(x * 100)}%`;

function sampleCard(d) {
  const draw = (again) => el("button", { class: again ? "btn" : "btn primary", onclick: async () => {
      if (again && !confirm("Draw a new sample? The current one is replaced; verdicts you already gave stay on their deviations.")) return;
      await api("/api/baseline/sample", { size: 50 });
      baselineShow = "sample";
      const y = window.scrollY; await renderBaseline(); window.scrollTo(0, y);
    } }, again ? "Re-draw" : "Draw a random review sample (50 per kind)");
  if (!d.sample) {
    return el("div", { class: "card" }, el("h2", {}, "Review sample"),
      el("p", { class: "muted" }, "Reviewing every deviation isn't realistic. A random sample per kind (all of a kind when it has fewer) gives an honest "
        + "false-alarm rate: the share you mark expected, with a 95% range. The sample is fixed once drawn, so it can't drift towards the easy ones."),
      draw(false));
  }
  const s = d.sample;
  return el("div", { class: "card scroll" }, el("h2", {}, "Review sample"),
    el("p", { class: "muted" }, `Drawn ${fmt(s.drawn_at)}, up to ${s.size} per kind. False-alarm share = marked expected ÷ reviewed, `
      + "with its 95% range (Wilson). A deviation you mark expected is one that would have been a false alarm had it paged."),
    el("table", {}, el("thead", {}, el("tr", {}, ["deviation", "recorded", "in sample", "reviewed", "expected", "suspicious", "false-alarm share (95% range)"]
      .map((h) => el("th", {}, h)))),
      el("tbody", {}, Object.entries(s.kinds).map(([k, v]) => el("tr", {},
        el("td", {}, words(k)), el("td", {}, v.population), el("td", {}, v.sample), el("td", {}, `${v.reviewed} of ${v.sample}`),
        el("td", {}, v.expected), el("td", {}, v.suspicious),
        el("td", {}, v.false_alarm_share === null ? "–" : `${pct(v.false_alarm_share)} (${pct(v.interval_95[0])}–${pct(v.interval_95[1])})`))))),
    el("p", {}, draw(true)));
}

async function renderBaseline() {
  const d = await api("/api/baseline");
  if (!baselineShow) baselineShow = d.sample ? "sample" : "all";
  const head = [
    el("h1", {}, "Baseline"),
    el("p", {}, el("span", { class: `tag ${d.mode === "shadow" ? "lab" : ""}` }, d.mode), " ",
      d.mode === "learning" ? `Learning until ${fmt(d.learning_until)}: profiles build from each device's traffic; only the device-type check records anything.`
        : d.mode === "shadow" ? "Shadow mode: deviations are recorded here and never notified, so their false-alarm rate can be measured first."
        : "Traffic collection hasn't started yet."),
    el("p", { class: "muted" }, `Each device's profile is re-learned daily from the last ${d.learn_days} days (today excluded) and is `
      + `compared with its own history once it has ${d.min_days} days of traffic; until then (a device that joins later, or a quiet one) it's compared with the whole network: countries and services no device uses, any traffic across networks, any blocked flow. Anything that deviated stays out of later profiles until you mark it expected. `
      + "Only traffic that crosses the gateway is seen."),
  ];
  const kinds = el("div", { class: "card" }, el("h2", {}, "False-alarm measurement"),
    el("table", {}, el("thead", {}, el("tr", {}, ["deviation", "recorded", "expected", "suspicious", "unreviewed"].map((h) => el("th", {}, h)))),
      el("tbody", {}, Object.entries(d.kinds).map(([k, v]) => el("tr", {},
        el("td", {}, words(k), el("div", { class: "muted" }, v.means)), el("td", {}, v.count), el("td", {}, v.expected),
        el("td", {}, v.suspicious), el("td", {}, v.count - v.expected - v.suspicious))))));
  const keyText = (k) => k.domain ? el("span", { class: "untrusted inline" }, k.domain.text)
    : k.region ? `region ${k.region}` : k.device ? `device ${k.device}` : k.value;
  const shown = baselineShow === "sample" && d.sample ? d.sample_rows : d.deviations;
  const rows = shown.map((x) => {
    const note = el("input", { type: "text", maxlength: 500, placeholder: "why (optional)", value: x.note || "" });
    const msg = el("span", {});
    const btn = (v) => el("button", { class: "btn", onclick: async () => {
        msg.textContent = "…";
        try {
          await api(`/api/baseline/deviations/${x.id}/label`, { verdict: v, note: note.value });
          msg.className = "ok"; msg.textContent = v;
          const y = window.scrollY; await renderBaseline(); window.scrollTo(0, y);  // refresh the sample figures in place
        } catch (e) { msg.className = "err"; msg.textContent = e.message; }
      } }, v);
    return el("tr", {},
      el("td", {}, x.what, el("div", { class: "muted" }, x.mac), x.name ? el("div", { class: "untrusted" }, x.name.text) : null),
      el("td", {}, words(x.kind), el("div", {}, keyText(x.key)),
        Object.keys(x.detail).length ? el("div", { class: "muted" }, Object.entries(x.detail).map(([k, v]) => `${words(k)}: ${v}`).join(" · ")) : null),
      el("td", {}, x.day, el("div", { class: "muted" }, `last ${fmt(x.last_at)}`)),
      el("td", {}, x.verdict ? el("span", { class: "tag lab" }, x.verdict) : null, note, btn("expected"), " ", btn("suspicious"), " ", msg));
  });
  const toggle = d.sample ? el("div", { class: "filters" }, [["sample", `Review the sample (${d.sample_rows.length})`], ["all", "All deviations"]]
    .map(([k, label]) => el("button", { class: baselineShow === k ? "on" : null, onclick: async () => { baselineShow = k; await renderBaseline(); } }, label))) : null;
  view.replaceChildren(...head, sampleCard(d), kinds, el("div", { class: "card scroll" }, el("h2", {}, "Deviations"), toggle,
    rows.length ? el("table", {}, el("thead", {}, el("tr", {}, ["device", "what changed", "day", "your verdict"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows)) : el("p", { class: "muted" }, "None recorded yet.")));
}

function recCell(r) {
  return el("div", {},
    el("div", { class: r.moves ? "action" : "" }, `${r.moves ? "→ " : ""}${r.title} · VLAN ${r.vlan}`),
    el("div", { class: "muted" }, r.source, r.status && r.status !== "ok" ? ` · ${r.status}` : ""),
    r.check && r.traffic_note ? el("div", { class: "err" }, r.traffic_note) : null);  // blocked counts are in the traffic line
}

function bytes(n) {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${i ? n.toFixed(1) : n} ${u[i]}`;
}

function trafficLine(t) {
  if (!t) return null;
  const parts = [`internet ${bytes(t.internet_bytes)} (${t.internet_flows} flows${t.services.length ? `: ${t.services.join(", ")}` : ""})`,
    `local peers seen ${t.local_peers}`];
  if (t.from_internet_flows) parts.push(`${t.from_internet_flows} from the internet`);
  if (t.blocked_flows) parts.push(`${t.blocked_flows} blocked`);
  if (t.incomplete) parts.push("incomplete: some windows hit the page limit");
  return el("div", { class: "muted" }, `last ${t.window_days} d: ${parts.join(" · ")}`);
}

// ---------------------------------------------------------------- triage mode
// A snapshot of the open issues, worked through one at a time. Highest severity first, then what Qwen
// escalated, then oldest. Saving with "close" ticked removes the issue from the queue and moves on.
const tq = { ids: [], pos: 0, closed: 0, labelled: 0, skipped: new Set(), built: false };
const SEV_ORDER = ["info", "low", "medium", "high"];

async function buildQueue() {
  const open = await api("/api/alerts?status=open");
  open.sort((x, y) => (SEV_ORDER.indexOf(y.severity) - SEV_ORDER.indexOf(x.severity))
    || (Number(!!(y.triage && y.triage.escalated)) - Number(!!(x.triage && x.triage.escalated)))
    || (x.created - y.created));
  Object.assign(tq, { ids: open.map((a) => a.id), pos: 0, closed: 0, labelled: 0, skipped: new Set(), built: true });
}

async function renderTriage(pos) {
  if (!tq.built) await buildQueue();
  if (!tq.ids.length || pos >= tq.ids.length) {
    view.replaceChildren(
      el("h1", {}, "Triage done"),
      el("p", {}, `${tq.closed} closed · ${tq.labelled} classified · ${tq.skipped.size} skipped.`),
      el("p", {}, el("a", { class: "btn", href: "#/", onclick: () => { tq.built = false; } }, "← Alerts"), " ",
        tq.skipped.size ? el("a", { class: "btn primary", href: "#/triage/0",
          onclick: () => { tq.ids = tq.ids.filter((i) => tq.skipped.has(i)); tq.skipped = new Set(); } }, "Go through the skipped ones") : null));
    return;
  }
  tq.pos = Math.max(0, pos);
  const id = tq.ids[tq.pos];
  const go = (p) => { location.hash = `#/triage/${p}`; };
  const bar = el("div", { class: "card triage-bar" },
    el("strong", {}, `Triage · ${tq.pos + 1} of ${tq.ids.length}`),
    el("span", { class: "muted" }, `  ${tq.closed} closed · ${tq.skipped.size} skipped · keys: j next, k previous`),
    el("span", { class: "triage-nav" },
      el("button", { class: "btn", disabled: tq.pos === 0 || null, onclick: () => go(tq.pos - 1) }, "← Previous"), " ",
      el("button", { class: "btn", onclick: () => { tq.skipped.add(id); go(tq.pos + 1); } }, "Skip →"), " ",
      el("a", { class: "btn", href: "#/", onclick: () => { tq.built = false; } }, "Exit")));
  await renderAlert(id, { bar, onSaved: (closed) => {
    tq.labelled += 1;
    tq.skipped.delete(id);
    if (closed) {  // out of the queue; the next one slides into this position
      tq.closed += 1;
      tq.ids.splice(tq.pos, 1);
      go(tq.pos);
      route();  // same hash: force the redraw
    } else {
      go(tq.pos + 1);
    }
  } });
}

document.addEventListener("keydown", (e) => {
  const m = (location.hash || "").match(/^#\/triage(?:\/(\d+))?$/);
  if (!m || e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  const at = Number(m[1] || 0);
  if (e.key === "j") location.hash = `#/triage/${at + 1}`;
  if (e.key === "k" && at > 0) location.hash = `#/triage/${at - 1}`;
});

// ---------------------------------------------------------------- routing
async function route() {
  const h = location.hash || "#/";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", (h.startsWith("#/training") ? "training" : h.startsWith("#/segmentation") ? "segmentation"
      : h.startsWith("#/inventory") ? "inventory" : h.startsWith("#/baseline") ? "baseline"
      : h.startsWith("#/blocked") ? "blocked" : "alerts") === a.dataset.nav));
  try {
    const m = h.match(/^#\/alert\/(\d+)$/);
    const tm = h.match(/^#\/triage(?:\/(\d+))?$/);
    const bm = h.match(/^#\/blocked(?:\/([0-9a-f]{2}(?::[0-9a-f]{2}){5}))?$/);
    if (m) await renderAlert(m[1]);
    else if (tm) { if (tm[1] === undefined) tq.built = false; await renderTriage(Number(tm[1] || 0)); }
    else if (h.startsWith("#/training")) await renderTraining();
    else if (h.startsWith("#/segmentation")) await renderSegmentation();
    else if (h.startsWith("#/inventory")) await renderInventory();
    else if (h.startsWith("#/baseline")) await renderBaseline();
    else if (bm) await renderBlocked(bm[1]);
    else await renderList();
  } catch (e) {
    view.replaceChildren(el("p", { class: "err" }, `Couldn't load: ${e.message}`));
  }
  refreshStatus();
}

window.addEventListener("hashchange", route);
route();
setInterval(refreshStatus, 30000);
