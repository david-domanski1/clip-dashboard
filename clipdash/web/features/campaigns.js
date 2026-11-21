// Campaign tracker and rule-change watch: a Settings tab with one card per campaign (dates, pay, counts, payout, page
// watch) and a Today card when a deadline is close, the rules page changed or a submission was missed.
// Nothing here edits a campaign's imported rules; the person reads the diff and updates the campaign by hand.
SETTINGS_TABS.push(['campaigns', 'Campaigns']);

const CT = { data: null, at: 0 };
async function ctLoad(force = false) {
  if (!force && CT.data && Date.now() - CT.at < 60000) return CT.data;
  const r = await get('/api/campaigns/tracker');
  CT.data = r.campaigns; CT.at = Date.now();
  return CT.data;
}

const CT_STATUS = { active: ['ok', 'Active'], paused: ['warn', 'Paused'], ended: ['skipped', 'Ended'] };
const ctDays = (r) => r.days_left === null || r.days_left === undefined ? '' : r.days_left < 0 ? `ended ${-r.days_left} day${r.days_left === -1 ? '' : 's'} ago` : r.days_left === 0 ? 'ends today' : `${r.days_left} day${r.days_left === 1 ? '' : 's'} left`;
const ctDay = (d) => d ? new Date(d + 'T12:00:00').toLocaleDateString([], { dateStyle: 'medium' }) : '';

function ctDiffHtml(diff) {
  return `<pre style="margin:6px 0 0;padding:10px;background:var(--bg-2);border:1px solid var(--line);border-radius:var(--r-sm);font-family:var(--mono);font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto">${String(diff || '').split('\n').map((l) => {
    const col = l.startsWith('+++') || l.startsWith('---') ? 'var(--muted)' : l.startsWith('+') ? 'var(--green)' : l.startsWith('-') ? 'var(--red)' : l.startsWith('@@') ? 'var(--teal)' : 'var(--text-2)';
    return `<span style="color:${col}">${esc(l)}</span>`;
  }).join('\n')}</pre>`;
}

function ctCard(r) {
  const [cls, word] = CT_STATUS[r.status] || CT_STATUS.active;
  const n = r.counts;
  const kv = (k, v) => v ? `<div class="row" style="gap:10px;align-items:baseline"><span class="small muted" style="min-width:110px">${k}</span><span>${v}</span></div>` : '';
  const warnColor = { rules_changed: 'var(--red)', missed: 'var(--red)', ending: 'var(--yellow)', ended: 'var(--muted)', page_error: 'var(--yellow)', no_url: 'var(--muted)' };
  return `<div class="panel" style="margin-top:12px" data-ct="${r.id}">
    <div class="row between"><div class="row"><b>${esc(r.name)}</b> <span class="badge ${cls}">${word}</span>${r.page_changed_at ? ' <span class="badge bad">rules changed</span>' : ''}${r.checking ? ' <span class="badge queued">checking page…</span>' : ''}</div>
      <span class="small muted">${esc(r.creator || '')}</span></div>
    <div style="margin-top:8px;display:flex;flex-direction:column;gap:3px">
      ${kv('Joined', r.joined_at ? esc(ctDay(r.joined_at)) : '<span class="muted">not set</span>')}
      ${kv('Ends', r.ends_at ? `${esc(ctDay(r.ends_at))} <span class="muted">(${esc(ctDays(r))})</span>` : '<span class="muted">not set</span>')}
      ${kv('Pays', esc(r.pays))}
      ${kv('Clips', `${n.pending} to review · ${n.approved} approved`)}
      ${kv('Posts', `${n.live} live · ${n.submitted} submitted · ${n.due} due${n.missed ? ` · <b style="color:var(--red)">${n.missed} missed</b>` : ''}`)}
      ${kv('Payout', esc(r.payout))}
      ${kv('Notes', r.payout_notes ? esc(r.payout_notes) : '')}
      ${kv('Page checked', r.url ? (r.page_checked_at ? esc(fmtDate(r.page_checked_at)) + (r.has_baseline ? '' : ' <span class="muted">(no readable text yet)</span>') : '<span class="muted">not yet</span>') + ` · <a href="${esc(r.url)}" target="_blank" rel="noopener">open page</a>` : '')}
    </div>
    ${r.warnings.length ? `<div style="margin-top:8px">${r.warnings.map((w) => `<div class="small" style="color:${warnColor[w.kind] || 'var(--yellow)'}">⚠ ${esc(w.text)}</div>`).join('')}</div>` : ''}
    ${r.page_diff ? `<details style="margin-top:8px" ${r.page_changed_at ? 'open' : ''}><summary class="small">What changed on the page${r.page_changed_at ? ' (' + esc(fmtDate(r.page_changed_at)) + ')' : ''}</summary>${ctDiffHtml(r.page_diff)}
      <div class="small muted" style="margin-top:4px">Green lines are new, red lines are gone. Your campaign settings were not changed: update them under Campaign rules if the rules moved.</div></details>` : ''}
    <div class="row" style="margin-top:10px;flex-wrap:wrap">
      <button class="btn sm" data-ct-check ${r.url ? '' : 'disabled title="Add the campaign page URL first"'}>Check page now</button>
      ${r.page_changed_at ? '<button class="btn sm primary" data-ct-ack>Acknowledge change</button>' : ''}
      <button class="btn sm" data-ct-edit>Edit dates and notes</button>
      <button class="btn sm" data-ct-rules>Campaign rules</button>
    </div>
    <div data-ct-form></div>
  </div>`;
}

function ctForm(r, box, rerender) {
  box.innerHTML = `<div class="option-box" style="margin-top:10px"><div class="form-grid">
      <div class="field"><label>Joined on</label><input type="date" data-f="joined_at" value="${esc(r.joined_at || '')}"></div>
      <div class="field"><label>Ends on</label><input type="date" data-f="ends_at" value="${esc(r.ends_at || '')}"></div>
      <div class="field"><label>Status</label><select data-f="status">${['active', 'paused', 'ended'].map((s) => `<option value="${s}" ${r.status === s ? 'selected' : ''}>${CT_STATUS[s][1]}</option>`).join('')}</select></div>
    </div>
    <div class="field"><label>Payout notes (when and how it pays out, anything to remember)</label><textarea data-f="payout_notes">${esc(r.payout_notes || '')}</textarea></div>
    <div class="small muted">Ended stops watching its sources; Active or Paused turns watching back on.</div>
    <div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn sm" data-cancel>Cancel</button><button class="btn sm primary" data-save>Save</button></div></div>`;
  $('[data-cancel]', box).onclick = () => { box.innerHTML = ''; };
  $('[data-save]', box).onclick = async () => {
    const body = {}; $$('[data-f]', box).forEach((i) => { body[i.dataset.f] = i.value; });
    try { await put(`/api/campaigns/${r.id}/tracker`, body); toast('Saved'); await loadCampaigns(); rerender(); } catch (e) { toastErr(e); }
  };
}

PANES.campaigns = async (el) => {
  let rows;
  try { rows = await ctLoad(true); } catch (e) { el.innerHTML = `<div class="callout pink">${esc(e.message)}</div>`; return; }
  const rerender = () => PANES.campaigns(el);
  el.innerHTML = `<h2 style="margin-top:0">Campaigns</h2>
    <p class="sub">When you joined each campaign, when it ends, what it pays and what you have earned so far. Each campaign page is re-read about every 12 hours; if its text changes you see exactly what changed. Your campaign settings are never changed for you.</p>
    ${rows.length ? rows.map(ctCard).join('') : emptyState('No campaigns yet.', 'New campaign', '#/home')}`;
  rows.forEach((r) => {
    const card = $(`[data-ct="${r.id}"]`, el);
    $('[data-ct-check]', card).onclick = async (e) => {
      e.target.disabled = true;
      try { const j = await post(`/api/campaigns/${r.id}/watch_now`); toast(j.queued ? 'Checking the campaign page' : 'A check is already running'); setTimeout(rerender, 4000); } catch (err) { toastErr(err); e.target.disabled = false; }
    };
    const ack = $('[data-ct-ack]', card);
    if (ack) ack.onclick = async () => { try { await post(`/api/campaigns/${r.id}/ack_change`); toast('Marked as seen'); rerender(); } catch (err) { toastErr(err); } };
    $('[data-ct-edit]', card).onclick = () => { const box = $('[data-ct-form]', card); if (box.innerHTML) box.innerHTML = ''; else ctForm(r, box, rerender); };
    $('[data-ct-rules]', card).onclick = async () => {
      if (!S.campaigns.find((x) => x.id === r.id)) await loadCampaigns();
      const c = S.campaigns.find((x) => x.id === r.id);
      if (!c) { toast('Campaign not found', 'err'); return; }
      openCampaignEditor(c);
      const m = $('#modal-root .modal');
      if (m && r.page_diff && r.page_changed_at) {
        const note = document.createElement('div');
        note.className = 'callout pink'; note.style.marginBottom = '12px';
        note.innerHTML = `<b>The campaign page changed.</b> Compare it with the fields below and update them by hand, then Save. <a href="${esc(r.url || '#')}" target="_blank" rel="noopener">Open the page</a>${ctDiffHtml(r.page_diff)}`;
        const head = $('.modal-head', m); if (head) head.after(note); else m.prepend(note);
      }
    };
  });
};

// Today: one line per active campaign, only when something needs a look.
const ctHome = VIEWS.home;
VIEWS.home = async (el, arg) => { try { await ctLoad(); } catch (e) { /* the card is optional */ } return ctHome(el, arg); };

TODAY_EXTRAS.push(() => {
  const rows = (CT.data || []).filter((r) => r.status === 'active');
  const needs = rows.some((r) => r.page_changed_at || r.counts.missed || (r.days_left !== null && r.days_left !== undefined && r.days_left >= 0 && r.days_left <= 7));
  if (!needs) return '';
  const line = (r) => `<div class="attn"><span><b>${esc(r.name)}</b><span class="muted">${r.days_left !== null && r.days_left !== undefined ? ' · ' + esc(ctDays(r)) : ''} · ${r.counts.submitted} submitted${r.counts.missed ? '' : ' · 0 missed'}</span>${r.counts.missed ? ` · <b style="color:var(--red)">${r.counts.missed} missed</b>` : ''}</span>${r.page_changed_at ? '<a href="#/settings/campaigns" style="color:var(--red);font-weight:700">rules changed →</a>' : ''}</div>`;
  const warn = rows.some((r) => r.page_changed_at || r.counts.missed);
  return `<div class="step ${warn ? 'warn' : ''}">
    <div class="step-n">◷</div>
    <div class="step-body"><div class="step-title">Campaigns <b class="count">${rows.length}</b></div><div class="step-sub">${rows.map(line).join('')}</div></div>
    <a class="btn" href="#/settings/campaigns">Campaigns</a>
  </div>`;
});
