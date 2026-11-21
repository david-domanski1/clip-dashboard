// Your taste: a Settings tab showing what the ranking has learned from your approvals, rejections and views.
// Nothing here approves anything; the model only re-orders suggestions and every adjusted moment says why.
SETTINGS_TABS.push(['taste', 'Your taste']);

PANES.taste = async (el) => {
  const st = await get('/api/taste/status');
  const scopeCard = (s) => `<div class="panel" style="margin-top:12px">
      <div class="row between"><b>${esc(s.name === 'all creators' ? 'All creators' : s.name)}</b>
        <span class="small muted">${s.approved} approved · ${s.rejected} rejected${s.trained ? ' · learned ' + fmtDate(s.trained_at) : ''}</span></div>
      <div style="margin-top:6px">${esc(s.sentence)}</div>
      ${s.trained ? '' : `<div class="small muted" style="margin-top:4px">Until then moments for ${esc(s.name)} keep their usual order.</div>`}
    </div>`;
  el.innerHTML = `<h2 style="margin-top:0">Your taste</h2>
    <p class="sub">The moment list leans toward what you approve and what earned views, one model per creator. Each adjusted moment shows a reason like “Your taste: moments like this were approved 7 of 9 times for lacy (+18%)”.</p>
    <div class="panel">
      <label class="check"><input type="checkbox" id="taste-on" ${st.enabled ? 'checked' : ''}> Lean the moment ranking toward my past decisions</label>
      <div class="small muted" style="margin-top:6px">Nothing is ever auto-approved. This only re-orders suggestions; you still review and approve every clip. Learning starts once a creator has ${st.min_examples} decisions (approve or reject); creators with fewer use what you decided across all creators.</div>
    </div>
    <div id="taste-scopes">${st.scopes.map(scopeCard).join('')}</div>
    <div class="row" style="margin-top:14px"><button class="btn" id="taste-retrain">Retrain now</button><button class="btn" id="taste-reset">Reset</button>
      <span class="small muted">Retrain refits from your decisions right away (it also happens by itself, at most once an hour per creator). Reset forgets the learned weights; your decisions stay and it relearns at the next scan unless you turn it off.</span></div>`;
  $('#taste-on').onchange = async (e) => {
    try { await put('/api/settings', { taste_enabled: e.target.checked }); toast(e.target.checked ? 'Ranking leans toward your taste' : 'Ranking uses the plain signal scores'); } catch (err) { toastErr(err); }
  };
  $('#taste-retrain').onclick = async () => {
    try { const r = await post('/api/taste/retrain'); toast(r.trained.length ? `Learned from your decisions (${r.trained.length} model${r.trained.length === 1 ? '' : 's'})` : `Not enough decisions yet: ${st.min_examples} per creator to start`); PANES.taste(el); } catch (err) { toastErr(err); }
  };
  $('#taste-reset').onclick = async () => {
    try { await post('/api/taste/reset'); toast('Forgot the learned weights'); PANES.taste(el); } catch (err) { toastErr(err); }
  };
};
