"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const inr = p => "₹" + (p / 100).toLocaleString("en-IN", {minimumFractionDigits: p % 100 ? 2 : 0, maximumFractionDigits: 2});
const inrFull = p => "₹" + (p / 100).toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const words = s => String(s || "").toLowerCase().replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
const hhmm = ts => ts ? new Date(ts).toLocaleTimeString("en-IN", {hour: "2-digit", minute: "2-digit", hour12: false}) : "";
const dmon = d => d ? new Date(d).toLocaleDateString("en-IN", {day: "numeric", month: "short"}) : "";

const PAGES = [["overview", "/", "Overview"], ["payments", "/payments", "Payments"], ["exceptions", "/exceptions", "Exceptions"],
               ["approvals", "/approvals", "Approvals"], ["books", "/books", "Books"]];
const here = (PAGES.find(p => p[1] === location.pathname) || PAGES[0]);
const SEV = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3};
const CLOSED = ["RESOLVED", "FALSE_POSITIVE", "ACCEPTED_RISK", "CLOSED_NO_ACTION"];
const ui = {open: new Set(), filter: {}, prev: null, lastBody: "", run: "", exByItem: {}};
if (location.hash === "#bank") ui.filter.tab = "bank";
try { ui.run = sessionStorage.getItem("pp-run") || ""; } catch (e) {}

/* theme */
try { const t = localStorage.getItem("pp-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
$("#theme").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("pp-theme", next); } catch (e) {}
};

/* what state is an item in, in words a person uses. Works for invoices, bank lines and statement lines. */
function verdict(it) {
  const V = (key, label, board) => ({key, label, board: board || label});
  if (it.kind === "BankLine") {
    if (it.state === "POSTED") return V("posted", "Posted");
    if (it.state === "HELD") return V("held", "Held");
    if (it.state === "CANNOT_VERIFY") return V("cannot", "Cannot verify");
    if (it.state === "RECONCILED") {
      if (it.flags.includes("DUPLICATE_DEBIT")) return V("dupdebit", "Duplicate debit");
      if (it.flags.includes("RETURNED_PAYMENT")) return V("returned", "Returned payment", "Returned");
      if ((ui.exByItem[it.item_id] || []).includes("UNMATCHED_PAYMENT")) return V("unmatched", "Unmatched");
      return V("matched", "Matched");
    }
    return V("other", words(it.state));
  }
  const p = it.payment;
  if (p) {
    if (p.status === "PROVIDER_CONFIRMED") return V("paid", "Paid");
    if (p.status === "RETURNED" || p.status === "REVERSED") return V("returned", "Returned by bank", "Returned");
    if (p.status === "PAYMENT_UNCERTAIN") return V("unknown", "Outcome unknown", "Unknown");
    if (p.status === "FAILED") return V("failed", "Declined", "Declined");
    return V("inflight", "In flight");
  }
  if (it.state === "HELD") return V("held", "Held");
  if (it.state === "AWAITING_APPROVAL") return V("approval", "Needs approval");
  if (it.state === "REJECTED") return V("rejected", "Rejected");
  if (it.state === "CANNOT_VERIFY") return V("cannot", "Cannot verify");
  return V("other", words(it.state));
}
const toneOf = k => ({paid: "go", posted: "go", matched: "go", returned: "stop", held: "stop", failed: "stop", dupdebit: "stop", rejected: "stop",
                      unknown: "wait", approval: "wait", cannot: "wait", unmatched: "wait", inflight: "steel"}[k] || "mute");

function lifecycle(it) {
  const p = it.payment, a = it.approval, names = ["Checked", "Approved", "Sent", "Confirmed", "Settled"];
  const s = ["done", "todo", "todo", "todo", "todo"];
  const held = !p && it.state === "HELD";
  if (held) s[0] = "blocked";
  else if (a && a.status === "APPROVED") s[1] = "done";
  else s[1] = "wait";
  if (p) {
    s[1] = "done"; s[2] = "done";
    s[3] = p.status === "PROVIDER_CONFIRMED" ? "done" : (["RETURNED", "REVERSED", "FAILED"].includes(p.status) ? "bad" : "wait");
    s[4] = p.settlement === "MATCHED" ? "done" : (p.status === "PROVIDER_CONFIRMED" ? "wait" : "todo");
  }
  const label = names.map((n, i) => `${n}: ${s[i] === "todo" ? "not reached" : s[i] === "blocked" ? "stopped here" : s[i]}`).join(", ");
  return `<ol class="life" role="img" aria-label="${esc(label)}">${names.map((n, i) => `<li class="${s[i]}">${n}</li>`).join("")}</ol>`;
}

const copyCmd = c => `<div class="cmd"><code>${esc(c)}</code><button type="button" data-copy="${esc(c)}" data-k="copy-${esc(c.slice(-12))}">Copy</button></div>`;
const invoices = s => s.items.filter(i => i.kind === "Invoice");
const runName = (s, id) => (s.runs.find(r => r.run_id === id) || {}).label || id;
const ago = ts => ts ? new Date(ts).toLocaleTimeString("en-IN", {hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}) : "";
const openEx = s => s.exceptions.filter(e => !CLOSED.includes(e.status));
const awaiting = s => invoices(s).filter(i => i.state === "AWAITING_APPROVAL" && !i.payment);

/* ---------- pages ---------- */
const FOOT = [["paid", "paid"], ["approval", "need approval"], ["inflight", "in flight"], ["unknown", "unknown"], ["held", "held"], ["failed", "declined"],
              ["returned", "returned"], ["rejected", "rejected"], ["cannot", "cannot verify"], ["posted", "posted"], ["matched", "matched"],
              ["dupdebit", "duplicate debits"], ["unmatched", "unmatched"]];

function boardHTML(s, limit) {
  const all = s.items.slice().sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  if (!all.length) return `<div class="board"><div class="board-empty">Nothing has run yet</div></div>`;
  const rows = all.slice(0, limit).map(it => {
    const v = verdict(it), isInv = it.kind === "Invoice";
    const changed = ui.prev && ui.prev[it.item_id] && ui.prev[it.item_id] !== v.key;
    return `<a class="brow" href="${isInv ? "/payments#" + esc(it.ref) : "/payments#bank"}"><span class="num">${esc(ago(it.updated_at))}</span><span>${esc(it.label)}</span>
      <span class="ref">${esc(it.ref)}</span><span class="amt num">${it.amount_paise == null ? "n/a" : inr(it.amount_paise)}</span>
      <span><span class="st ${v.key}${changed ? " flip" : ""}">${esc(v.board)}</span></span></a>`;
  }).join("");
  const n = k => all.filter(i => verdict(i).key === k).length;
  const foot = FOOT.filter(([k]) => n(k)).map(([k, l]) => `<span><b>${n(k)}</b> ${l}</span>`).join("");
  return `<div class="board" role="table" aria-label="Latest changes across every run">
    <div class="board-head" role="row"><span>Updated</span><span>Payee</span><span>Reference</span><span style="text-align:right">Amount</span><span>Status</span></div>
    ${rows}
    <div class="board-foot">${foot}<span class="more">${all.length > limit ? `Showing the ${limit} newest of ${all.length}` : `${all.length} items`}</span></div></div>`;
}

function statementHTML(s) {
  const m = s.money, total = m.paid_paise + m.inflight_paise + m.awaiting_paise + m.held_paise + m.returned_paise || 1;
  const ln = (label, v, tone, col) => `<div class="ln"><span>${label}</span><b class="num tone-${tone}">${inr(v)}</b><div class="bar"><i style="width:${(100 * v / total).toFixed(1)}%;background:${col}"></i></div></div>`;
  return `<div class="stmt">${ln("Paid to vendors", m.paid_paise, "go", "var(--go)")}${ln("In flight with the provider", m.inflight_paise, "steel", "var(--steel)")}
    ${ln("Waiting for approval", m.awaiting_paise, "wait", "var(--wait)")}${ln("Held, not paid", m.held_paise, "stop", "var(--stop)")}
    ${ln("Returned by the bank", m.returned_paise, "stop", "var(--stop)")}</div>`;
}

function runsHTML(s) {
  if (!s.runs.length) return `<div class="empty">No runs yet. Start one with <code>python -m paypilot run --scenario PATH</code>.</div>`;
  return `<div class="tbl"><table><thead><tr><th>Run</th><th>Request</th><th>Items</th><th>Outcomes</th><th class="r">Open exceptions</th></tr></thead><tbody>` +
    s.runs.map(r => `<tr${ui.run === r.run_id ? ' style="background:color-mix(in srgb,var(--steel) 9%,transparent)"' : ""}>
      <td><button type="button" class="linkbtn" data-run="${esc(r.run_id)}" data-k="run-${esc(r.run_id)}"><b>${esc(r.label)}</b></button><div class="small mute">${esc(r.run_id)}, ${esc(ago(r.started_at))}</div></td>
      <td class="small mute">${esc(r.request_id)}</td><td class="num">${r.items}</td>
      <td class="small">${Object.entries(r.states).map(([k, n]) => `${n} ${esc(words(k).toLowerCase())}`).join(", ") || "none"}</td>
      <td class="r num ${r.open_exceptions ? "tone-stop" : ""}">${r.open_exceptions}</td></tr>`).join("") + `</tbody></table></div>`;
}

function attentionHTML(s) {
  const ex = openEx(s).filter(e => e.blocking).sort((a, b) => SEV[a.severity] - SEV[b.severity]).slice(0, 4);
  const aw = awaiting(s);
  const rows = aw.map(i => `<a class="item" href="/approvals"><div class="t"><span>Approve ${esc(i.ref)}</span><span class="num">${inr(i.amount_paise || 0)}</span></div><div class="d">${esc(i.label)} is waiting for an approver.</div></a>`).join("")
    + ex.map(e => `<a class="item" href="/exceptions#${esc(e.exception_id)}"><div class="t"><span>${esc(words(e.type))}</span><span class="sev ${esc(e.severity)}">${esc(words(e.severity))}</span></div><div class="d">${esc(e.details || e.summary)}</div></a>`).join("");
  return rows ? `<div class="list">${rows}</div>` : `<div class="empty">Nothing needs a person right now.</div>`;
}

/* Turn a raw audit event into a sentence a person can read, and say which item it is about. Internal bookkeeping steps are not "key". */
const INTERNAL_ITEM = ["PARSED", "RESOLVED", "CHECKED", "DECIDED"];
const ITEM_SAYS = {AWAITING_APPROVAL: "Waiting for approval", APPROVED: "Approved", PAYMENT_IN_PROGRESS: "Payment sent to the provider", PAID: "Paid",
  HELD: "Held", POSTED: "Posted to the books", CANNOT_VERIFY: "Cannot verify", REJECTED: "Rejected", RECONCILED: "Reconciled",
  PAYMENT_UNCERTAIN: "Outcome unknown", FAILED: "Failed"};
const PAY_SAYS = {CREATED: "Payment created", SUBMITTED: "Submitted to the provider", PROVIDER_CONFIRMED: "Provider confirmed the payment",
  RETURNED: "Money returned by the bank", REVERSED: "Payment reversed", FAILED: "Provider declined the payment", PAYMENT_UNCERTAIN: "Outcome unknown, will reconcile"};
function explain(e, s) {
  const item = {}, byPay = {}, byExc = {}, byAppr = {};
  s.items.forEach(i => { item[i.item_id] = i; if (i.payment) byPay[i.payment.payment_id] = i; });
  s.exceptions.forEach(x => { byExc[x.exception_id] = x; });
  s.approvals.forEach(a => { byAppr[a.approval_id] = a; });
  let it = null, text = "", major = true, tone = "mute";
  const t = e.entity_type, n = e.new_state, why = e.reason ? e.reason.split(";")[0] : "";
  if (t === "item") {
    it = item[e.entity_id];
    major = !INTERNAL_ITEM.includes(n);
    text = ITEM_SAYS[n] || words(n);
    if (n === "APPROVED") text = "Approved by " + e.actor;
    if (n === "HELD" && why) text = "Held: " + why;
    tone = {PAID: "go", POSTED: "go", RECONCILED: "go", HELD: "stop", REJECTED: "stop", FAILED: "stop"}[n] || "mute";
  } else if (t === "payment") {
    it = byPay[e.entity_id];
    text = PAY_SAYS[n] || words(n);
    major = !(e.previous_state === n);                       // "SUBMITTED to SUBMITTED: reference saved" is bookkeeping
    if (e.previous_state === "CREATED" && n === "SUBMITTED") major = false;
    tone = {PROVIDER_CONFIRMED: "go", RETURNED: "stop", REVERSED: "stop", FAILED: "stop", PAYMENT_UNCERTAIN: "wait"}[n] || "steel";
  } else if (t === "exception") {
    const x = byExc[e.entity_id]; it = x && item[x.item_id];
    text = "Exception: " + (why || words(x?.type)); tone = x && SEV[x.severity] <= 1 ? "stop" : "wait";
  } else if (t === "approval") {
    const a = byAppr[e.entity_id]; it = a && item[a.item_id];
    text = "Approval " + words(n || "recorded").toLowerCase() + (a?.reviewer ? " by " + a.reviewer : ""); major = false;
  } else if (t === "recovery_case") {
    text = "Recovery case " + words(n || "opened").toLowerCase(); tone = "wait";
  } else text = words(t) + ": " + words(n);
  return {ref: it ? it.ref : "", run: it ? runName(s, it.run_id) : "", text, major, tone};
}

function feedRows(s, events) {
  return events.map(e => { const x = explain(e, s);
    return `<div class="feed-row"><time class="num">${esc(ago(e.ts))}</time><b>${esc(x.ref || "")}</b>
      <span class="tone-${x.tone}">${esc(x.text)}</span><span class="mute small">${esc(x.run)}</span></div>`; }).join("");
}

function activityHTML(s) {
  const ev = s.audit.filter(e => explain(e, s).major).slice(0, 10);
  return ev.length ? `<div class="list">${feedRows(s, ev)}</div>` : `<div class="empty">No activity yet.</div>`;
}

function overview(s) {
  return `<section><h2>Live board</h2><p class="lede">Every invoice, bank line and statement line from every run, newest change first. A status flips when it changes.</p>${boardHTML(s, 12)}</section>
  <section class="sec"><h2>Runs</h2><p class="lede">Each run is one batch the system worked through. Select one to focus every page on it.</p>${runsHTML(s)}</section>
  <div class="cols2 sec"><section><h2>Money position</h2><p class="lede">Where the invoice value sits right now.</p>${statementHTML(s)}</section>
  <section><h2>Needs a person</h2><p class="lede">Waiting for approval, or blocked by an exception.</p>${attentionHTML(s)}</section></div>
  <section class="sec"><h2>Latest activity</h2><p class="lede">Most recent state changes. The full trail is under Books.</p>${activityHTML(s)}</section>`;
}

let S = null;   // the state being rendered, for helpers that need other runs
function payRow(it) {
  const v = verdict(it), tone = toneOf(v.key), isOpen = ui.open.has(it.ref);
  const why = it.reasons.map(r => `<li>${esc(r.text || words(r.code))}<small>${esc(words(r.code))}${r.rule ? ", rule " + esc(r.rule) : ""}</small></li>`).join("");
  const att = (it.payment?.attempts || []).map(a => `Attempt ${a.no}: ${esc(words(a.outcome))}${a.error ? " (" + esc(a.error) + ")" : ""}`).join("; ");
  return `<div class="pay${isOpen ? " open" : ""}" id="${esc(it.ref)}" data-ref="${esc(it.ref)}">
    <button type="button" aria-expanded="${isOpen}" data-k="pay-${esc(it.ref)}">
      <div class="who"><b>${esc(it.label)}</b><span>${esc(it.ref)}${it.invoice_no ? ", " + esc(it.invoice_no) : ""}, ${esc(runName(S, it.run_id))}</span></div>
      ${lifecycle(it)}
      <div class="amt"><b class="num">${inr(it.amount_paise || 0)}</b><span class="tag tone-${tone}">${esc(v.label)}</span></div>
    </button>
    <div class="panel">
      <div><h3>${why ? "Why it stopped" : "Checks"}</h3>${why ? `<ul class="why">${why}</ul>` : `<p class="mute">No flags raised. Every check passed.</p>`}
        ${copyCmd("python -m paypilot show " + it.ref)}</div>
      <div><h3>Details</h3><dl class="facts">
        <dt>Vendor</dt><dd>${esc(it.vendor_id || "Unresolved")}</dd><dt>Invoice date</dt><dd>${esc(it.date || "n/a")}</dd><dt>Due</dt><dd>${esc(it.due_date || "n/a")}</dd>
        <dt>Approval</dt><dd>${it.approval ? esc(words(it.approval.status)) + (it.approval.reviewer ? " by " + esc(it.approval.reviewer) : "") : "None yet"}</dd>
        ${it.payment ? `<dt>Provider</dt><dd>${esc(it.payment.provider)}</dd><dt>Settlement</dt><dd>${esc(words(it.payment.settlement))}</dd>` : ""}
        ${att ? `<dt>Attempts</dt><dd>${att}</dd>` : ""}</dl></div></div></div>`;
}

function chips(group, options, items, keyOf) {
  const cur = ui.filter[group] || "all";
  const count = k => k === "all" ? items.length : items.filter(i => keyOf(i) === k).length;
  return `<div class="filters" role="group" aria-label="Filter">${options.map(([k, l]) => `<button type="button" data-filter="${group}:${k}" data-k="f-${group}-${k}" aria-pressed="${cur === k}">${l}<span class="c num">${count(k)}</span></button>`).join("")}</div>`;
}

function payments(s) {
  const tab = ui.filter.tab || "invoices", inv = invoices(s), cur = ui.filter.pay || "all";
  const shown = inv.filter(i => cur === "all" || verdict(i).key === cur);
  const bank = s.items.filter(i => i.kind === "BankLine");
  const tabs = `<div class="filters" role="group" aria-label="View">${[["invoices", "Invoices"], ["bank", "Bank lines"]].map(([k, l]) => `<button type="button" data-filter="tab:${k}" data-k="tab-${k}" aria-pressed="${tab === k}">${l}</button>`).join("")}</div>`;
  if (tab === "bank") {
    const tone = {POSTED: "go", RECONCILED: "go", HELD: "stop", CANNOT_VERIFY: "wait"};
    return tabs + (bank.length ? `<div class="tbl"><table><thead><tr><th>Line</th><th>Descriptor</th><th>Date</th><th>State</th><th class="r">Amount</th></tr></thead><tbody>` +
      bank.map(x => `<tr><td>${esc(x.ref)}</td><td>${esc(x.label)}${x.flags.length ? `<div class="small tone-stop">${x.flags.map(f => esc(words(f))).join(", ")}</div>` : ""}</td><td class="num">${esc(x.date || "")}</td>
      <td class="tag tone-${tone[x.state] || "mute"}">${esc(words(x.state))}</td><td class="r num">${inrFull(x.amount_paise || 0)}</td></tr>`).join("") + `</tbody></table></div>`
      : `<div class="empty">No bank lines in this run.</div>`);
  }
  const opts = [["all", "All"], ["approval", "Needs approval"], ["inflight", "In flight"], ["unknown", "Outcome unknown"], ["paid", "Paid"], ["held", "Held"], ["failed", "Declined"], ["returned", "Returned"], ["rejected", "Rejected"], ["cannot", "Cannot verify"]];
  return tabs + chips("pay", opts, inv, i => verdict(i).key) +
    (shown.length ? `<div>${shown.map(payRow).join("")}</div>` : `<div class="empty">No invoices match this filter.</div>`);
}

function exceptions(s) {
  const all = s.exceptions.slice().sort((a, b) => SEV[a.severity] - SEV[b.severity] || (b.created_at || "").localeCompare(a.created_at || ""));
  const f = ui.filter.ex || "open";
  const keyOf = e => CLOSED.includes(e.status) ? "closed" : "open";
  const shown = all.filter(e => f === "all" || keyOf(e) === f);
  const blocking = openEx(s).filter(e => e.blocking).length;
  const rows = shown.map(e => {
    const isOpen = ui.open.has(e.exception_id);
    return `<div class="exr${isOpen ? " open" : ""}" id="${esc(e.exception_id)}" data-ref="${esc(e.exception_id)}">
      <button type="button" aria-expanded="${isOpen}" data-k="ex-${esc(e.exception_id)}"><span class="sev ${esc(e.severity)}">${esc(words(e.severity))}</span>
        <span><b>${esc(words(e.type))}</b><p>${esc(e.details || e.summary)}</p></span><span class="tag tone-${e.blocking ? "stop" : "mute"}">${e.blocking ? "Blocks payment" : esc(words(e.status))}</span></button>
      <div class="panel"><dl class="facts" style="max-width:520px"><dt>Status</dt><dd>${esc(words(e.status))}</dd><dt>Vendor</dt><dd>${esc(e.vendor_id || "n/a")}</dd>
        <dt>Evidence records</dt><dd>${e.evidence_count}</dd>${e.related.length ? `<dt>Related</dt><dd>${e.related.map(esc).join(", ")}</dd>` : ""}
        <dt>Raised</dt><dd>${esc((e.created_at || "").slice(0, 16).replace("T", " "))}</dd></dl>
        <p style="margin-top:12px"><b>Recommended action.</b> ${esc(e.recommended_action)}</p></div></div>`;
  }).join("");
  return `<p class="lede">${blocking ? `${blocking} open exception${blocking === 1 ? "" : "s"} block payment to the vendor involved.` : "Nothing is blocking payment."} Resolve them from the CLI with <code>python -m paypilot exceptions</code>.</p>
    ${chips("ex", [["open", "Open"], ["closed", "Closed"], ["all", "All"]], all, keyOf)}
    ${rows ? `<div>${rows}</div>` : `<div class="empty">No exceptions here.</div>`}`;
}

function approvals(s) {
  const aw = awaiting(s), byItem = Object.fromEntries(s.items.map(i => [i.item_id, i]));
  const waiting = aw.length ? `<div>${aw.map(i => `<div class="pay open" style="border-top:0"><div class="panel" style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:36px;padding-top:14px">
      <div><b>${esc(i.label)}</b><div class="mute small">${esc(i.ref)}${i.invoice_no ? ", " + esc(i.invoice_no) : ""}, due ${esc(dmon(i.due_date))}</div><div style="font-size:22px;font-weight:650;margin-top:6px" class="num">${inr(i.amount_paise || 0)}</div></div>
      <div><div class="mute small">Run this as an allowlisted approver:</div>${copyCmd('python -m paypilot approve ' + i.ref + ' --by "NAME"')}</div></div></div>`).join("")}</div>`
    : `<div class="empty">Nothing is waiting for approval.</div>`;
  const done = s.approvals.length ? `<div class="tbl"><table><thead><tr><th>Invoice</th><th>Decision</th><th>Approver</th><th>When</th><th>Bound to decision</th></tr></thead><tbody>` +
    s.approvals.map(a => { const it = byItem[a.item_id]; const tone = {APPROVED: "go", REJECTED: "stop", PENDING: "wait"}[a.status] || "mute";
      return `<tr><td>${esc(it ? it.ref : a.item_id.slice(0, 8))}${a.approval_seq > 1 ? ` <span class="small mute">(approval ${a.approval_seq}, after a decline)</span>` : ""}<div class="small mute">${esc(it ? it.label : "")}</div></td><td class="tag tone-${tone}">${esc(words(a.status))}</td>
      <td>${esc(a.reviewer || "Waiting")}${a.overrides.length ? `<div class="small mute">Overrides: ${a.overrides.map(esc).join(", ")}</div>` : ""}</td><td class="num">${esc(ago(a.decided_at || a.requested_at))}</td><td><code>${esc(a.decision_hash)}</code></td></tr>`; }).join("") + `</tbody></table></div>`
    : `<div class="empty">No approvals recorded yet.</div>`;
  return `<section><h2>Waiting for approval</h2><p class="lede">Approving happens in the CLI so the approver allowlist and the never-overridable checks always apply. This page only reads.</p>${waiting}</section>
    <section class="sec"><h2>Decided</h2><p class="lede">Each approval is tied to the exact decision it covered. If the facts change, it no longer applies.</p>${done}</section>`;
}

function books(s) {
  const tb = s.trial_balance, d = tb.reduce((a, r) => a + r.debit, 0), c = tb.reduce((a, r) => a + r.credit, 0);
  const trial = tb.length ? `<div class="tbl"><table><thead><tr><th>Account</th><th class="r">Debit</th><th class="r">Credit</th></tr></thead><tbody>` +
    tb.map(r => `<tr><td>${esc(r.account)}</td><td class="r num">${r.debit ? inrFull(r.debit) : ""}</td><td class="r num">${r.credit ? inrFull(r.credit) : ""}</td></tr>`).join("") +
    `<tr><td><b>Total</b></td><td class="r num"><b>${inrFull(d)}</b></td><td class="r num"><b>${inrFull(c)}</b></td></tr></tbody></table></div>
    <p class="small" style="margin-top:8px">${d === c ? `<span class="tone-go">Debits equal credits.</span>` : `<span class="tone-stop">Out of balance by ${inrFull(Math.abs(d - c))}.</span>`}</p>` : `<div class="empty">No journal entries in this run.</div>`;
  const ref = Object.fromEntries(s.items.map(i => [i.item_id, i]));
  const jr = s.journal.length ? `<div class="journal">${s.journal.map(e => `<div class="je"><b>${esc(words(e.kind))}</b> <span class="mute">${esc(ref[e.item_id]?.ref || "")}, ${esc(e.memo)}</span>
    <table><tbody>${e.lines.map(l => `<tr><td>${esc(l.account)}</td><td class="r num">${l.debit_paise ? inrFull(l.debit_paise) : ""}</td><td class="r num">${l.credit_paise ? inrFull(l.credit_paise) : ""}</td></tr>`).join("")}</tbody></table></div>`).join("")}</div>` : `<div class="empty">No entries.</div>`;
  const af = ui.filter.audit || "key";
  const aud = s.audit.filter(e => af === "all" || explain(e, s).major);
  const mode = `<div class="filters" role="group" aria-label="Detail">${[["key", "Key events"], ["all", "Every step"]].map(([k, l]) => `<button type="button" data-filter="audit:${k}" data-k="f-audit-${k}" aria-pressed="${af === k}">${l}<span class="c num">${k === "all" ? s.audit.length : s.audit.filter(e => explain(e, s).major).length}</span></button>`).join("")}</div>`;
  const audit = mode + (aud.length ? `<div class="list">${feedRows(s, aud.slice(0, 120))}</div>` : `<div class="empty">No events.</div>`);
  const k = s.cost, money = v => "₹" + v.toFixed(2);
  const head = k.status === "UNKNOWN" ? `<span class="unknown">UNKNOWN</span>` : k.status === "PARTIAL" ? `${money(k.total)} <span class="unknown">plus unknown</span>` : money(k.total);
  const cost = (k.calls || k.cache_hits) ? `<p style="font-size:24px;font-weight:650;margin:0 0 4px" class="num">${head}</p>
    <p class="lede">${k.calls} lookups, ${k.cache_hits} from cache. ${k.status === "PARTIAL" ? "Some calls have no price, so the total is a lower bound. " : ""}${k.unconfirmed ? money(k.unconfirmed) + " is reserved for calls with no recorded outcome." : ""}</p>
    <div class="tbl"><table><thead><tr><th>Provider</th><th class="r">Spend</th></tr></thead><tbody>${k.providers.map(p => `<tr><td>${esc(p.provider)}</td><td class="r num">${p.unknown ? `<span class="unknown">UNKNOWN</span>` : money(p.spend)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No paid lookups yet.</div>`;
  return `<div class="cols2"><section><h2>Trial balance</h2><p class="lede">Every journal entry balances.</p>${trial}</section><section><h2>Lookup cost</h2><p class="lede">A missing price is shown as unknown, never zero.</p>${cost}</section></div>
    <section class="sec"><h2>Journal</h2><p class="lede">Each posting and payment, line by line.</p>${jr}</section>
    <section class="sec"><h2>Audit trail</h2><p class="lede">What happened, to which item, newest first. Switch to every step to see the system bookkeeping too.</p>${audit}</section>`;
}

const VIEWS = {overview, payments, exceptions, approvals, books};

/* ---------- shell ---------- */
function shell(s) {
  document.title = here[2] + " · PayPilot";
  $("#title").textContent = here[0] === "overview" ? "Payments overview" : here[2];
  const nExc = openEx(s).filter(e => e.blocking).length, nAp = awaiting(s).length;
  const badge = {exceptions: nExc, approvals: nAp};
  $("#nav").innerHTML = PAGES.map(([k, href, label]) => `<a href="${href}"${k === here[0] ? ' aria-current="page"' : ""}>${label}${badge[k] ? `<span class="n num" aria-label="${badge[k]} need attention">${badge[k]}</span>` : ""}</a>`).join("");
  const p = s.payment_providers;
  $("#provider").textContent = p.includes("razorpayx_test") ? "RazorpayX test mode, no real money" : "Simulated provider, no real money";
  const sel = $("#run");
  if (sel.options.length !== s.runs.length + 1 || [...sel.options].some((o, i) => i && !o.textContent.startsWith(s.runs[i - 1].label + " (" + s.runs[i - 1].items))) {
    sel.innerHTML = `<option value="">All runs</option>` + s.runs.map(r => `<option value="${esc(r.run_id)}">${esc(r.label)} (${r.items} items)</option>`).join("");
    sel.value = ui.run;
  }
}

function render(s) {
  S = s;
  ui.exByItem = {};
  s.exceptions.forEach(e => { if (e.item_id) (ui.exByItem[e.item_id] ||= []).push(e.type); });
  const focus = document.activeElement?.dataset?.k, y = window.scrollY;
  shell(s);
  $("#view").innerHTML = VIEWS[here[0]](s);
  ui.prev = Object.fromEntries(invoices(s).map(i => [i.item_id, verdict(i).key]));
  if (focus) document.querySelector(`[data-k="${CSS.escape(focus)}"]`)?.focus({preventScroll: true});
  window.scrollTo(0, y);
}

document.addEventListener("click", ev => {
  const row = ev.target.closest(".pay > button, .exr > button");
  if (row) {
    const wrap = row.parentElement, ref = wrap.dataset.ref, o = wrap.classList.toggle("open");
    row.setAttribute("aria-expanded", o); o ? ui.open.add(ref) : ui.open.delete(ref); return;
  }
  const f = ev.target.closest("[data-filter]");
  if (f) { const [g, k] = f.dataset.filter.split(":"); ui.filter[g] = k; ui.lastBody = ""; load(); return; }
  const r = ev.target.closest("[data-run]");
  if (r) { ui.run = r.dataset.run; $("#run").value = ui.run; try { sessionStorage.setItem("pp-run", ui.run); } catch (x) {} ui.lastBody = ""; ui.prev = null; load(); return; }
  const c = ev.target.closest("[data-copy]");
  if (c) {
    const done = () => { c.textContent = "Copied"; setTimeout(() => { c.textContent = "Copy"; }, 1500); };
    (navigator.clipboard ? navigator.clipboard.writeText(c.dataset.copy).then(done, () => { c.textContent = "Select and copy"; }) : (c.textContent = "Select and copy"));
  }
});
$("#run").onchange = e => { ui.run = e.target.value; try { sessionStorage.setItem("pp-run", ui.run); } catch (x) {} ui.lastBody = ""; ui.prev = null; load(); };

async function load() {
  try {
    const r = await fetch("/api/state" + (ui.run ? "?run=" + encodeURIComponent(ui.run) : ""));
    const text = await r.text(), s = JSON.parse(text);
    if (!r.ok) throw new Error(s.error || r.status);
    $("#error").innerHTML = "";
    if (ui.run && s.runs.length && !s.runs.some(r => r.run_id === ui.run)) {   // the remembered run no longer exists (demo was reset)
      ui.run = ""; try { sessionStorage.removeItem("pp-run"); } catch (x) {} $("#run").innerHTML = ""; ui.lastBody = ""; return load();
    }
    if (text !== ui.lastBody) {
      ui.lastBody = text; render(s);
      if (location.hash && !ui.hashed) { ui.hashed = true; const t = document.getElementById(decodeURIComponent(location.hash.slice(1)));
        if (t) { t.classList.add("open"); ui.open.add(t.dataset.ref); t.querySelector("button")?.setAttribute("aria-expanded", "true"); t.scrollIntoView({block: "center"}); } }
    }
    $("#live").classList.remove("off");
    $("#stamp").textContent = "Updated " + new Date().toLocaleTimeString("en-IN", {hour12: false});
  } catch (e) {
    $("#live").classList.add("off"); $("#stamp").textContent = "Disconnected";
    $("#error").innerHTML = `<div class="err">Can't read the database: ${esc(e.message)}. Start the server with <code>python scripts/dashboard.py --db PATH</code>.</div>`;
  }
}
load(); setInterval(load, 2000);
