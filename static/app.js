"use strict";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
let META = null;

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const money0 = (v) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 }));
const pct = (v) => (v === null || v === undefined ? "—" : (v * 100).toFixed(0) + "%");
const typeLabel = (t) => (META?.doc_types?.[t] ?? t ?? "—");
const statusPill = (s) => `<span class="pill ${s === "approved" ? "s-pass" : s === "rejected" ? "s-fail" : s === "duplicate" ? "s-warn" : ""}">${esc(s.replace("_", " "))}</span>`;

const reviewer = $("#reviewer");
try { reviewer.value = localStorage.getItem("reviewer") || ""; } catch {}
reviewer.addEventListener("change", () => { try { localStorage.setItem("reviewer", reviewer.value.trim()); } catch {} });

function banner(msg, ok = false) {
  const b = $("#banner");
  if (!msg) { b.hidden = true; return; }
  b.textContent = msg; b.className = "banner" + (ok ? " ok" : ""); b.hidden = false;
  window.scrollTo({ top: 0 });
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res;
}

async function refreshQueueCount() {
  const q = await api("/api/documents?status=queue");
  $("#queue-count").textContent = q.length || "";
}

// ---------------------------------------------------------------- dashboard
async function renderDashboard() {
  const [funds, out] = await Promise.all([api("/api/funds"), api("/api/outstanding")]);
  const sum = (k) => funds.reduce((a, f) => a + (f[k] || 0), 0);
  const suspense = funds.filter((f) => Math.abs(f.suspense) > 0.005);
  view.innerHTML = `
    <h1>Portfolio</h1>
    <p class="sub">${funds.length} funds · books as of approved documents · extraction engine: <b>${esc(META.extractor)}</b></p>
    <div class="tiles">
      <div class="tile"><div class="k">Commitments</div><div class="v">${money0(sum("commitment"))}</div></div>
      <div class="tile"><div class="k">Called to date</div><div class="v">${money0(sum("called"))}</div></div>
      <div class="tile"><div class="k">Unfunded</div><div class="v">${money0(sum("unfunded"))}</div></div>
      <div class="tile"><div class="k">Distributed</div><div class="v">${money0(sum("distributed"))}</div></div>
      <div class="tile"><div class="k">Book NAV</div><div class="v">${money0(sum("book_nav"))}</div></div>
      <div class="tile"><div class="k">Open items</div><div class="v">${out.items.length}</div></div>
    </div>
    ${suspense.length ? `<div class="panel s-warn">Reconciliation suspense is open for ${suspense.map((f) => `<a href="#fund/${f.code}">${esc(f.code)}</a> (${money(f.suspense)})`).join(", ")}. Clear it once the missing notices are booked.</div>` : ""}
    <div class="panel table-wrap">
      <h2 style="margin-top:0">Funds</h2>
      <table><thead><tr><th>Fund</th><th>Strategy</th><th class="num">Commitment</th><th class="num">Called</th><th class="num">% called</th><th class="num">Unfunded</th><th class="num">Distributed</th><th class="num">Book NAV</th><th>Last statement</th><th class="num">DPI</th><th class="num">TVPI</th></tr></thead>
      <tbody>${funds.map((f) => `<tr class="click" data-href="#fund/${f.code}">
        <td><b>${esc(f.code)}</b> <span class="muted">${esc(f.name)}</span></td><td>${esc(f.strategy)}</td>
        <td class="num">${money0(f.commitment)}</td><td class="num">${money0(f.called)}</td><td class="num">${pct(f.pct_called)}</td>
        <td class="num">${money0(f.unfunded)}</td><td class="num">${money0(f.distributed)}</td><td class="num">${money0(f.book_nav)}</td>
        <td>${esc(f.last_statement_period || "—")}</td><td class="num">${f.dpi ?? "—"}</td><td class="num">${f.tvpi ?? "—"}</td></tr>`).join("")}</tbody></table>
    </div>
    <div class="panel table-wrap">
      <h2 style="margin-top:0">Outstanding items</h2>
      <p class="sub">Statements expected for quarter ended ${esc(out.as_of_quarter)}, final K-1s for ${out.tax_year}, and capital calls coming due.</p>
      <table><thead><tr><th>Fund</th><th>Item</th><th>Status</th><th>Where to get it</th></tr></thead>
      <tbody>${out.items.map((i) => `<tr ${i.document_id ? `class="click" data-href="#doc/${i.document_id}"` : ""}>
        <td>${esc(i.fund_code || "—")}</td><td>${esc(i.item)}</td>
        <td><span class="pill ${i.state === "not received" ? "s-fail" : "s-warn"}">${esc(i.state)}</span></td><td class="muted">${esc(i.portal || "")}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Nothing outstanding.</td></tr>`}</tbody></table>
    </div>`;
}

// ---------------------------------------------------------------- queue
async function renderQueue() {
  const docs = await api("/api/documents?status=queue");
  view.innerHTML = `
    <h1>Review queue</h1>
    <p class="sub">Every document is checked against the books before anything posts. Open one to review it against the PDF.</p>
    <div class="drop" id="drop">Drop PDF statements here, or <label class="btn">choose files<input type="file" id="file" accept="application/pdf" multiple hidden></label></div>
    <div class="panel table-wrap"><table><thead><tr><th>#</th><th>File</th><th>Type</th><th>Fund</th><th>Checks</th><th>Engine</th><th>Status</th></tr></thead>
    <tbody>${docs.map((d) => `<tr class="click" data-href="#doc/${d.id}">
      <td>${d.id}</td><td>${esc(d.file_name)}</td><td>${esc(typeLabel(d.doc_type))}</td><td>${esc(d.fund_code || "unmatched")}</td>
      <td>${d.fails ? `<span class="pill s-fail">${d.fails} failed</span> ` : ""}${d.warns ? `<span class="pill s-warn">${d.warns} to review</span>` : ""}${!d.fails && !d.warns ? `<span class="pill s-pass">all passed</span>` : ""}</td>
      <td class="muted">${esc(d.extractor)}${d.confidence != null ? ` · ${Math.round(d.confidence * 100)}%` : ""}</td><td>${statusPill(d.status)}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">Queue is empty.</td></tr>`}</tbody></table></div>`;
  const drop = $("#drop");
  const send = async (files) => {
    const pdfs = [...files].filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) return banner("Only PDF files can be uploaded.");
    drop.textContent = `Reading ${pdfs.length} file(s)…`;
    const fd = new FormData();
    pdfs.forEach((f) => fd.append("files", f));
    fd.append("actor", reviewer.value.trim());
    try {
      const res = await api("/api/documents", { method: "POST", body: fd });
      const errs = res.filter((r) => r.error);
      banner(errs.length ? errs.map((e) => `${e.file_name}: ${e.error}`).join("; ") : `${res.length} document(s) added to the queue.`, !errs.length);
    } catch (e) { banner(e.message); }
    route();
  };
  $("#file").addEventListener("change", (e) => send(e.target.files));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); send(e.dataTransfer.files); });
}

// ---------------------------------------------------------------- document review
const SECTION_FIELDS = {
  capital_call: ["capital_call", [["investment", "Investment"], ["management_fee", "Management fee"], ["fund_expenses", "Fund expenses"], ["other", "Other"], ["total", "Total due"]]],
  distribution: ["distribution", [["return_of_capital", "Return of capital"], ["realized_gain", "Realized gain"], ["income", "Income"], ["withholding", "Tax withheld"], ["net_amount", "Net cash"], ["recallable", "Recallable"]]],
  capital_account_statement: ["capital_account", [["beginning_balance", "Beginning capital"], ["contributions", "Contributions"], ["distributions", "Distributions"], ["investment_income", "Investment income"], ["expenses", "Fees & expenses"], ["realized_gain", "Realized gain"], ["unrealized_gain", "Unrealized gain"], ["carried_interest", "Carried interest"], ["ending_balance", "Ending capital (NAV)"], ["unfunded_commitment", "Unfunded commitment"]]],
  k1: ["k1", [["ordinary_income", "Box 1 Ordinary income"], ["interest_income", "Box 5 Interest"], ["dividends", "Box 6a Dividends"], ["st_capital_gain", "Box 8 ST capital gain"], ["lt_capital_gain", "Box 9a LT capital gain"], ["distributions", "Box 19 Distributions"], ["ending_capital", "Ending capital (Item L)"]]],
  financial_statements: ["financial_statements", [["fund_total_nav", "Fund total partners' capital"]]],
};
const DATE_FIELDS = {
  capital_call: [["notice_date", "Notice date"], ["effective_date", "Due date"]],
  distribution: [["notice_date", "Notice date"], ["effective_date", "Payment date"]],
  capital_account_statement: [["period_start", "Period start"], ["period_end", "Period end"]],
  financial_statements: [["period_end", "As of"]],
  k1: [],
  other: [["notice_date", "Document date"]],
};

function jeTable(entries, title) {
  if (!entries?.length) return `<p class="muted">${title === "Posted" ? "Nothing posted." : "No journal entry for this document type (tracked only)."}</p>`;
  const acct = (k, l) => l.account_number ? `${l.account_number} ${l.account_name}` : `${META.accounts[k].number} ${META.accounts[k].name}`;
  return entries.map((e) => `<div class="je"><div class="je-head">${esc(e.entry_date)} · ${esc(e.memo)}${e.balanced === false ? ' <span class="pill s-fail">unbalanced</span>' : ""}${e.export_batch ? ` · exported ${esc(e.export_batch)}` : ""}</div>
    <table><thead><tr><th>Account</th><th class="num">Debit</th><th class="num">Credit</th><th>Memo</th></tr></thead><tbody>
    ${e.lines.map((l) => `<tr${l.account_key === "recon_suspense" ? ' class="s-warn"' : ""}><td>${esc(acct(l.account_key, l))}</td><td class="num">${l.debit ? money(l.debit) : ""}</td><td class="num">${l.credit ? money(l.credit) : ""}</td><td class="muted">${esc(l.memo || "")}</td></tr>`).join("")}
    </tbody></table></div>`).join("");
}

async function renderDoc(id) {
  const [doc, funds] = await Promise.all([api(`/api/documents/${id}`), api("/api/funds")]);
  const ex = doc.extraction || {};
  const t = doc.doc_type || "other";
  const editable = ["needs_review", "duplicate"].includes(doc.status);
  const unsure = new Set(ex.uncertain_fields || []);
  const input = (name, label, value, type = "number") => `<label class="${unsure.has(name) ? "uncertain" : ""}">${esc(label)}${unsure.has(name) ? " (unclear)" : ""}
      <input name="${name}" type="${type}" ${type === "number" ? 'step="0.01" class="num"' : ""} value="${esc(value ?? "")}" ${editable ? "" : "disabled"}></label>`;
  const [section, fields] = SECTION_FIELDS[t] || [null, []];
  const sec = (section && ex[section]) || {};
  const checks = doc.validation || [];
  const hasFail = checks.some((c) => c.status === "fail");

  view.innerHTML = `
    <p><a href="#queue">← Review queue</a></p>
    <h1>${esc(doc.file_name)}</h1>
    <p class="sub">#${doc.id} · uploaded ${esc(doc.uploaded_at)} by ${esc(doc.uploaded_by || "—")} · engine ${esc(doc.extractor)}${doc.confidence != null ? ` (${Math.round(doc.confidence * 100)}% confidence)` : ""} · ${statusPill(doc.status)}
      ${doc.reviewed_by ? ` · ${esc(doc.status)} by ${esc(doc.reviewed_by)} ${esc(doc.reviewed_at)}${doc.review_note ? ` — “${esc(doc.review_note)}”` : ""}` : ""}</p>
    <div class="review">
      <iframe src="/api/documents/${doc.id}/file" title="Source PDF"></iframe>
      <div>
        <form class="panel" id="form">
          <h2 style="margin-top:0">Extracted data</h2>
          <div class="fields">
            <label>Document type<select name="doc_type" ${editable ? "" : "disabled"}>${Object.entries(META.doc_types).map(([k, v]) => `<option value="${k}" ${k === t ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
            <label>Fund<select name="fund_code" ${editable ? "" : "disabled"}><option value="">— not matched —</option>${funds.map((f) => `<option value="${f.code}" ${f.code === doc.fund_code ? "selected" : ""}>${esc(f.code)} · ${esc(f.name)}</option>`).join("")}</select></label>
            ${input("fund_name", "Fund name as printed", ex.fund_name, "text")}
            ${t === "k1" ? input("tax_year", "Tax year", ex.tax_year) : ""}
            ${(DATE_FIELDS[t] || []).map(([k, l]) => input(k, l, ex[k], "date")).join("")}
            ${fields.map(([k, l]) => input(`${section}.${k}`, l, sec[k])).join("")}
            ${t === "k1" ? `<label>Final K-1?<select name="k1.is_final" ${editable ? "" : "disabled"}><option value="true" ${sec.is_final ? "selected" : ""}>Final</option><option value="false" ${sec.is_final ? "" : "selected"}>Estimate</option></select></label>` : ""}
          </div>
          ${ex.notes ? `<p class="muted" style="margin-bottom:0">${esc(ex.notes)}</p>` : ""}
          ${editable ? `<div class="actions"><button type="submit">Save changes &amp; re-check</button></div>` : ""}
        </form>
        <div class="panel"><h2 style="margin-top:0">Checks</h2><ul class="checks">
          ${checks.map((c) => `<li><span class="badge s-${c.status}">${c.status}</span><div><div>${esc(c.label)}</div>${c.detail ? `<div class="detail">${esc(c.detail)}</div>` : ""}</div></li>`).join("")}
        </ul></div>
        <div class="panel"><h2 style="margin-top:0">${doc.status === "approved" ? "Posted journal entries" : "Journal entries that will post"}</h2>
          ${doc.status === "approved" ? jeTable(doc.posted_entries, "Posted") : jeTable(doc.proposed_entries, "Proposed")}
        </div>
        ${editable ? `<div class="panel"><h2 style="margin-top:0">Decision</h2>
          <textarea id="note" rows="2" style="width:100%" placeholder="Note (required to override a failed check or to reject)"></textarea>
          <div class="actions">
            ${hasFail ? `<label class="row"><input type="checkbox" id="override"> Override failed checks</label>` : ""}
            <button class="primary" id="approve">Approve &amp; post</button>
            <button class="danger" id="reject">Reject</button>
          </div></div>` : ""}
        <div class="panel"><h2 style="margin-top:0">History</h2>${doc.history.map((h) => `<div class="muted">${esc(h.ts)} · ${esc(h.actor || "system")} · ${esc(h.action)}</div>`).join("")}</div>
      </div>
    </div>`;

  if (!editable) return;
  $("#form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const extraction = {};
    for (const [k, v] of fd.entries()) {
      if (k === "doc_type" || k === "fund_code") continue;
      const val = v === "" ? null : k === "k1.is_final" ? v === "true" : k.includes(".") || k === "tax_year" ? Number(v) : v;
      if (k.includes(".")) { const [s, f] = k.split("."); (extraction[s] ||= { ...(ex[s] || {}) })[f] = val; }
      else extraction[k] = val;
    }
    try {
      await api(`/api/documents/${doc.id}`, { method: "PATCH", body: JSON.stringify({ actor: reviewer.value.trim() || null, extraction, fund_code: fd.get("fund_code"), doc_type: fd.get("doc_type") }) });
      banner("Saved. Checks re-run.", true); renderDoc(doc.id);
    } catch (err) { banner(err.message); }
  });
  const decide = async (action) => {
    const actor = reviewer.value.trim();
    if (!actor) { reviewer.focus(); return banner("Enter your name in the Reviewer box first; it is recorded on the approval."); }
    const note = $("#note").value.trim() || null;
    try {
      await api(`/api/documents/${doc.id}/${action}`, { method: "POST", body: JSON.stringify({ actor, note, override: !!$("#override")?.checked }) });
      banner(action === "approve" ? "Approved and posted." : "Rejected.", true);
      refreshQueueCount(); renderDoc(doc.id);
    } catch (err) { banner(err.message); }
  };
  $("#approve").addEventListener("click", () => decide("approve"));
  $("#reject").addEventListener("click", () => decide("reject"));
}

// ---------------------------------------------------------------- funds
async function renderFunds() {
  const funds = await api("/api/funds");
  view.innerHTML = `<h1>Funds</h1><p class="sub">The fund master list. Names and aliases are used to match incoming documents.</p>
    <div class="panel table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>GP</th><th>Aliases</th><th>Vintage</th><th class="num">Commitment</th><th>Portal</th></tr></thead>
    <tbody>${funds.map((f) => `<tr class="click" data-href="#fund/${f.code}"><td><b>${esc(f.code)}</b></td><td>${esc(f.name)}</td><td>${esc(f.gp_name)}</td><td class="muted">${esc(f.aliases.join(", "))}</td><td>${esc(f.vintage)}</td><td class="num">${money0(f.commitment)}</td><td>${esc(f.portal)}</td></tr>`).join("")}</tbody></table></div>
    <form class="panel" id="newfund"><h2 style="margin-top:0">Add a fund</h2><div class="row">
      <input name="code" placeholder="Code" required size="6"><input name="name" placeholder="Legal name" required size="34"><input name="gp_name" placeholder="GP">
      <input name="aliases" placeholder="Aliases (comma separated)"><input name="vintage" type="number" placeholder="Vintage" style="width:90px">
      <input name="commitment" type="number" step="0.01" placeholder="Commitment" required><input name="portal" placeholder="Portal"><button class="primary">Add</button></div></form>`;
  $("#newfund").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    f.aliases = f.aliases ? f.aliases.split(",").map((s) => s.trim()).filter(Boolean) : [];
    f.commitment = Number(f.commitment); f.vintage = f.vintage ? Number(f.vintage) : null;
    try { await api("/api/funds", { method: "POST", body: JSON.stringify(f) }); banner(`Added ${f.code}. Re-open unmatched documents and pick the fund.`, true); renderFunds(); }
    catch (err) { banner(err.message); }
  });
}

async function renderFund(code) {
  const d = await api(`/api/funds/${code}`);
  const s = d.summary;
  view.innerHTML = `<p><a href="#dashboard">← Dashboard</a></p><h1>${esc(s.code)} · ${esc(s.name)}</h1>
    <p class="sub">${esc(s.gp_name)} · ${esc(s.strategy)} · vintage ${esc(s.vintage)} · portal: ${esc(s.portal)}</p>
    <div class="tiles">
      <div class="tile"><div class="k">Commitment</div><div class="v">${money0(s.commitment)}</div></div>
      <div class="tile"><div class="k">Called</div><div class="v">${money0(s.called)}</div></div>
      <div class="tile"><div class="k">Unfunded</div><div class="v">${money0(s.unfunded)}</div></div>
      <div class="tile"><div class="k">Distributed</div><div class="v">${money0(s.distributed)}</div></div>
      <div class="tile"><div class="k">Book NAV</div><div class="v">${money0(s.book_nav)}</div></div>
      <div class="tile"><div class="k">Suspense</div><div class="v">${money(s.suspense)}</div></div>
    </div>
    <div class="panel table-wrap"><h2 style="margin-top:0">Cash activity</h2><table><thead><tr><th>Date</th><th>Type</th><th class="num">Amount</th><th class="num">Recallable</th><th>Source</th></tr></thead>
      <tbody>${d.transactions.map((t) => `<tr ${t.document_id ? `class="click" data-href="#doc/${t.document_id}"` : ""}><td>${esc(t.effective_date)}</td><td>${esc(t.txn_type.replace("_", " "))}</td><td class="num">${money(t.amount)}</td><td class="num">${t.recallable ? money(t.recallable) : ""}</td><td class="muted">${esc(t.file_name || "conversion")}</td></tr>`).join("")}</tbody></table></div>
    <div class="panel table-wrap"><h2 style="margin-top:0">Capital account (GP statements)</h2><table><thead><tr><th>Period end</th><th class="num">Beginning</th><th class="num">Contrib.</th><th class="num">Distrib.</th><th class="num">Income</th><th class="num">Expenses</th><th class="num">Realized</th><th class="num">Unrealized</th><th class="num">Carry</th><th class="num">Ending NAV</th></tr></thead>
      <tbody>${d.statements.map((r) => `<tr ${r.document_id ? `class="click" data-href="#doc/${r.document_id}"` : ""}><td>${esc(r.period_end)}${r.document_id ? "" : ' <span class="muted">(opening)</span>'}</td>${["beginning_balance", "contributions", "distributions", "investment_income", "expenses", "realized_gain", "unrealized_gain", "carried_interest", "ending_balance"].map((k) => `<td class="num">${money(r[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <div class="panel table-wrap"><h2 style="margin-top:0">Documents</h2><table><tbody>${d.documents.map((x) => `<tr class="click" data-href="#doc/${x.id}"><td>#${x.id}</td><td>${esc(x.file_name)}</td><td>${esc(typeLabel(x.doc_type))}</td><td>${statusPill(x.status)}</td></tr>`).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------- journal
async function renderJournal() {
  const all = await api("/api/journal");
  const pending = all.filter((e) => !e.export_batch);
  view.innerHTML = `<h1>Journal &amp; export</h1>
    <p class="sub">Entries created from approved documents. Export produces a CSV in a generic GL-import layout; map its columns to your accounting system's import template. Account numbers are set in <code>config/accounts.json</code>.</p>
    <div class="panel row"><b>${pending.length}</b> entries not yet exported.
      <button class="primary" id="export" ${pending.length ? "" : "disabled"}>Export new entries (CSV)</button>
      <button id="reexport">Re-download everything</button></div>
    <div class="panel">${jeTable(all, "Posted")}</div>`;
  const dl = async (all) => {
    try {
      const res = await api(`/api/journal/export?actor=${encodeURIComponent(reviewer.value.trim())}&include_exported=${all}`, { method: "POST" });
      const blob = await res.blob();
      const name = (res.headers.get("content-disposition") || "").match(/filename="(.+)"/)?.[1] || "journal.csv";
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
      a.click(); URL.revokeObjectURL(a.href);
      banner(`Downloaded ${name} (${res.headers.get("x-entry-count")} entries).`, true); renderJournal();
    } catch (err) { banner(err.message); }
  };
  $("#export").addEventListener("click", () => dl(false));
  $("#reexport").addEventListener("click", () => dl(true));
}

// ---------------------------------------------------------------- K-1s and audit
async function renderK1s() {
  const [k1s, out] = await Promise.all([api("/api/k1s"), api("/api/outstanding")]);
  const missing = out.items.filter((i) => i.kind === "k1");
  view.innerHTML = `<h1>K-1 tracker</h1><p class="sub">Approved K-1s with the main boxes, for the tax team. ${missing.length} fund(s) still owe a final ${out.tax_year} K-1.</p>
    ${missing.length ? `<div class="panel">${missing.map((m) => `<div><b>${esc(m.fund_code)}</b> ${esc(m.fund)} — <span class="pill ${m.state === "not received" ? "s-fail" : "s-warn"}">${esc(m.state)}</span> <span class="muted">${esc(m.portal || "")}</span></div>`).join("")}</div>` : ""}
    <div class="panel table-wrap"><table><thead><tr><th>Year</th><th>Fund</th><th>Final?</th><th class="num">Box 1</th><th class="num">Box 5</th><th class="num">Box 6a</th><th class="num">Box 8</th><th class="num">Box 9a</th><th class="num">Box 19</th></tr></thead>
    <tbody>${k1s.map((k) => `<tr class="click" data-href="#doc/${k.document_id}"><td>${k.tax_year}</td><td>${esc(k.fund_code)} <span class="muted">${esc(k.fund)}</span></td><td>${k.is_final ? "Final" : '<span class="pill s-warn">Estimate</span>'}</td>
      ${["ordinary_income", "interest_income", "dividends", "st_capital_gain", "lt_capital_gain", "distributions"].map((b) => `<td class="num">${money(k.boxes[b])}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="9" class="muted">None yet.</td></tr>`}</tbody></table></div>`;
}

async function renderAudit() {
  const log = await api("/api/audit");
  view.innerHTML = `<h1>Audit log</h1><p class="sub">Every upload, edit, approval, rejection and export, newest first.</p>
    <div class="panel table-wrap"><table><thead><tr><th>When (UTC)</th><th>Who</th><th>Action</th><th>Document</th><th>Detail</th></tr></thead>
    <tbody>${log.map((a) => `<tr ${a.document_id ? `class="click" data-href="#doc/${a.document_id}"` : ""}><td class="mono">${esc(a.ts)}</td><td>${esc(a.actor || "system")}</td><td>${esc(a.action)}</td><td>${esc(a.file_name || "")}</td><td class="mono muted">${esc(a.detail ? JSON.stringify(a.detail) : "")}</td></tr>`).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------- router
async function route() {
  const [name, arg] = (location.hash.slice(1) || "dashboard").split("/");
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + (name === "doc" ? "queue" : name === "fund" ? "funds" : name)));
  try {
    META ||= await api("/api/meta");
    const pages = { dashboard: renderDashboard, queue: renderQueue, doc: () => renderDoc(arg), funds: renderFunds, fund: () => renderFund(arg), journal: renderJournal, k1s: renderK1s, audit: renderAudit };
    await (pages[name] || renderDashboard)();
    refreshQueueCount();
  } catch (e) { banner(e.message); }
}
view.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-href]");
  if (tr && !e.target.closest("a, button, input, select")) location.hash = tr.dataset.href;
});
window.addEventListener("hashchange", () => { banner(null); route(); });
route();
