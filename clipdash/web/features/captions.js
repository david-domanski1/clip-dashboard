// Caption styles and brand kit: a Settings tab to pick how burned-in captions look, per campaign or as the default.
// Styles only change the look and timing of the words; captions always transcribe exactly what was said.
SETTINGS_TABS.push(['captions', 'Captions']);

const CAPTION_STYLE_INFO = {
  classic: ['Classic', 'Groups of about four words, white with a black outline.'],
  pop: ['Pop', 'Each word appears the moment it is said.'],
  highlight: ['Highlight', 'The whole line shows; words turn the highlight colour as they are said.'],
  boxed: ['Boxed', 'One or two words at a time on a dark box.'],
};

PANES.captions = async (el, campaignId) => {
  const cid = campaignId ?? (el.dataset.capCampaign || '');
  el.dataset.capCampaign = cid;
  const st = await get('/api/captions/brand' + (cid ? `?campaign_id=${encodeURIComponent(cid)}` : ''));
  const b = st.brand;
  const opt = (v, cur, text) => `<option value="${esc(v)}" ${String(v) === String(cur) ? 'selected' : ''}>${esc(text ?? v)}</option>`;
  const posLabel = { lower: 'Lower middle', middle: 'Centre', upper: 'Upper (below the name)' };
  el.innerHTML = `<h2 style="margin-top:0">Captions</h2>
    <p class="sub">How the burned-in captions look. Styles change the look and timing only; the words are always exactly what was said.</p>
    <div class="panel">
      <div class="field" style="max-width:420px"><label>Applies to</label>
        <select id="cap-camp">${opt('', cid, 'Default for all campaigns')}${S.campaigns.map((c) => opt(c.id, cid, c.name)).join('')}</select></div>
      ${cid ? `<div class="small muted">${st.custom ? 'This campaign has its own caption look.' : 'This campaign follows the default. Saving here gives it its own look.'}</div>` : ''}
    </div>
    <div class="panel"><b>Style</b>
      <div id="cap-styles" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-top:10px">
        ${st.styles.map((s) => `<label style="display:flex;flex-direction:column;gap:6px;cursor:pointer;border:1px solid var(--line-2);border-radius:12px;padding:8px" data-style-card="${s}">
          <img data-preview="${s}" alt="${esc((CAPTION_STYLE_INFO[s] || [s])[0])} preview" style="width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:8px;background:#111">
          <span><input type="radio" name="cap-style" value="${s}" ${s === st.style ? 'checked' : ''}> <b>${esc((CAPTION_STYLE_INFO[s] || [s])[0])}</b></span>
          <span class="small muted">${esc((CAPTION_STYLE_INFO[s] || ['', ''])[1])}</span></label>`).join('')}
      </div>
    </div>
    <div class="panel"><b>Brand kit</b>
      <div class="form-grid" style="margin-top:10px">
        <div class="field"><label>Font</label><select data-b="font">${st.fonts.map((f) => opt(f, b.font)).join('')}</select></div>
        <div class="field"><label>Size (${st.size_range[0]}–${st.size_range[1]})</label><input type="number" data-b="size" min="${st.size_range[0]}" max="${st.size_range[1]}" value="${esc(b.size)}"></div>
        <div class="field"><label>Text colour</label><input type="color" data-b="colour" value="${esc(b.colour.toLowerCase())}"></div>
        <div class="field"><label>Highlight colour (highlighted words and the name)</label><input type="color" data-b="highlight" value="${esc(b.highlight.toLowerCase())}"></div>
        <div class="field"><label>Outline (${st.outline_range[0]}–${st.outline_range[1]})</label><input type="number" data-b="outline" min="${st.outline_range[0]}" max="${st.outline_range[1]}" value="${esc(b.outline)}"></div>
        <div class="field"><label>Position</label><select data-b="position">${st.positions.map((p) => opt(p, b.position, posLabel[p] || p)).join('')}</select></div>
      </div>
      <label class="check"><input type="checkbox" data-b="name_badge" ${b.name_badge ? 'checked' : ''}> Show the creator's name at the top (plain text, no logo)</label>
      <label class="check"><input type="checkbox" data-b="uppercase" ${b.uppercase ? 'checked' : ''}> Uppercase captions</label>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="cap-save">Save</button>
        ${cid && st.custom ? '<button class="btn" id="cap-inherit">Use the default</button>' : ''}
        <span class="small muted">Applies to clips rendered from now on; Re-render a clip to restyle it.</span></div>
    </div>`;

  const form = () => {
    const o = {};
    $$('[data-b]', el).forEach((i) => { o[i.dataset.b] = i.type === 'checkbox' ? i.checked : (i.type === 'number' ? Number(i.value) : i.value); });
    o.style = ($('input[name=cap-style]:checked', el) || {}).value || st.style;
    return o;
  };
  const markCards = () => $$('[data-style-card]', el).forEach((c) => { c.style.borderColor = $('input', c).checked ? 'var(--teal)' : 'var(--line-2)'; });
  const refreshPreviews = () => {
    const f = form();
    const q = new URLSearchParams();
    if (cid) q.set('campaign_id', cid);
    ['font', 'size', 'colour', 'highlight', 'outline', 'position', 'name_badge', 'uppercase'].forEach((k) => q.set(k, String(f[k])));
    $$('img[data-preview]', el).forEach((img) => { const p = new URLSearchParams(q); p.set('style', img.dataset.preview); img.src = '/api/captions/preview.png?' + p.toString(); });
  };
  let timer = null;
  const later = () => { clearTimeout(timer); timer = setTimeout(refreshPreviews, 350); };
  $$('[data-b]', el).forEach((i) => { i.addEventListener('input', later); i.addEventListener('change', later); });
  $$('input[name=cap-style]', el).forEach((r) => r.addEventListener('change', markCards));
  markCards();
  refreshPreviews();

  $('#cap-camp', el).onchange = (e) => PANES.captions(el, e.target.value);
  $('#cap-save', el).onclick = async () => {
    try {
      await put('/api/captions/brand' + (cid ? `?campaign_id=${encodeURIComponent(cid)}` : ''), form());
      toast(cid ? 'Saved the caption look for this campaign' : 'Saved the default caption look');
      PANES.captions(el, cid);
    } catch (err) { toastErr(err); }
  };
  const inh = $('#cap-inherit', el);
  if (inh) inh.onclick = async () => {
    try { await put(`/api/captions/brand?campaign_id=${encodeURIComponent(cid)}`, { inherit: true }); toast('This campaign now follows the default caption look'); PANES.captions(el, cid); } catch (err) { toastErr(err); }
  };
};
