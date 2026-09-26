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
          a.labelled ? el("span", { class: "tag lab" }, "classified") : null),
        el("div", { class: "sub" },
          a.recommended_action && a.recommended_action.action
            ? `→ ${a.recommended_action.action_source === "model" ? "Qwen: " : ""}${a.recommended_action.action}` : "",
          a.triage && a.triage.assessment ? `  ·  model: ${words(a.triage.assessment)} (${a.triage.confidence || "?"})` : "")),
      el("div", { class: "meta" }, `#${a.id}`, el("br"), fmt(a.created)))));
  view.replaceChildren(
    el("h1", {}, "Alerts"), filters,
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

async function renderAlert(id) {
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
      el("div", { class: "untrusted" }, [t.model_text.reason, t.model_text.action_reason, t.model_text.next_step].filter(Boolean).join("\n\n"))) : null)
    : el("div", { class: "card muted" }, "Not triaged by Qwen (low severity, or triage pending).");

  const verdicts = el("div", { class: "card" }, el("h2", {}, "Decision record"),
    el("table", {},
      el("thead", {}, el("tr", {}, ["tier", "verdict", "confidence", "rule version", "when", "note"].map((h) => el("th", {}, h)))),
      el("tbody", {}, (a.verdicts || []).map((v) => el("tr", {},
        el("td", {}, v.tier), el("td", {}, words(v.verdict)), el("td", {}, v.confidence || ""),
        el("td", {}, v.rule_version || ""), el("td", {}, fmt(v.created)), el("td", {}, v.human_decision || ""))))));

  view.replaceChildren(
    el("p", {}, el("a", { href: "#/" }, "← Alerts")),
    el("h1", {}, a.title),
    el("p", {}, sev(a.severity), " ", el("span", { class: "muted" }, `${words(a.rule)} · #${a.id} · ${fmt(a.created)} · ${a.status}`)),
    actionCard, modelCard, classifyForm(a),
    el("div", { class: "card" }, el("h2", {}, "Evidence"), renderValue(a.detail)),
    verdicts);
}

function classifyForm(a) {
  const o = a.options;
  const cur = a.label || {};
  const t = a.triage || {};
  const radio = (name, value, text, checked) =>
    el("label", {}, el("input", { type: "radio", name, value, checked: checked || null, required: true }), text);
  const msg = el("span", {});
  const form = el("form", { class: "classify card", onsubmit: async (ev) => {
      ev.preventDefault();
      const f = new FormData(form);
      msg.className = ""; msg.textContent = "Saving…";
      try {
        await api(`/api/alerts/${a.id}/label`, {
          should_fire: f.get("should_fire"), assessment: f.get("assessment"),
          action: f.get("action") || null, note: f.get("note") || "", close: f.get("close") === "on",
        });
        msg.className = "ok"; msg.textContent = "Saved.";
        setTimeout(route, 400);
      } catch (e) { msg.className = "err"; msg.textContent = e.message; }
    } },
    el("h2", {}, "Classify"),
    el("p", { class: "muted" }, "Your answer is the training label: it records what the rule and Qwen should have said."),
    el("fieldset", {}, el("legend", {}, "Should this alert have fired?"),
      radio("should_fire", "yes", "Yes, worth a look", cur.should_fire !== "no"),
      radio("should_fire", "no", "No, the rule was wrong", cur.should_fire === "no")),
    // nothing pre-selected from Qwen: a label must be your own answer, not a click-through of the model's
    el("fieldset", {}, el("legend", {}, "The correct assessment"),
      t.assessment ? el("p", { class: "muted" }, `Qwen said: ${words(t.assessment)}`) : null,
      o.assessments.map((x) => radio("assessment", x, words(x), cur.assessment === x))),
    el("fieldset", {}, el("legend", {}, "The correct action"),
      el("select", { name: "action", required: true },
        el("option", { value: "", selected: cur.action ? null : true }, "Choose the action you'd want…"),
        o.actions.map((x) => el("option", { value: x.id, selected: cur.action === x.id || null }, x.title)))),
    el("fieldset", {}, el("legend", {}, "Why (becomes the reason in the training example; examples without one aren't exported)"),
      el("textarea", { name: "note", rows: 3, maxlength: 500 }, cur.note || "")),
    el("label", {}, el("input", { type: "checkbox", name: "close", checked: a.status === "open" ? null : true }),
      a.status === "open" ? "Also close this alert" : "Alert is closed"),
    el("div", {}, el("button", { class: "btn primary", type: "submit" }, cur.created ? "Update classification" : "Save classification"), " ", msg));
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

// ---------------------------------------------------------------- routing
async function route() {
  const h = location.hash || "#/";
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", (h.startsWith("#/training") ? "training" : "alerts") === a.dataset.nav));
  try {
    const m = h.match(/^#\/alert\/(\d+)$/);
    if (m) await renderAlert(m[1]);
    else if (h.startsWith("#/training")) await renderTraining();
    else await renderList();
  } catch (e) {
    view.replaceChildren(el("p", { class: "err" }, `Couldn't load: ${e.message}`));
  }
  refreshStatus();
}

window.addEventListener("hashchange", route);
route();
setInterval(refreshStatus, 30000);
