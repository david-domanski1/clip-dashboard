/* Feature: keyboard-first review and hook compare.
   Adds shortcuts to the Review modal (seek, loop the trimmed range, mute, tick checklist items, set trim in/out,
   focus the hook, ? for the list), a "Compare hooks" row that plays 4 s from where each candidate hook is said,
   and a playback-speed control remembered in localStorage. UI only; nothing here approves a clip: A/Approve
   stay with the core and its gate. */
(() => {
  'use strict';
  if (typeof REVIEW_EXTRAS === 'undefined' || typeof REVIEW_KEYS === 'undefined') return;

  const RATE_KEY = 'clipdash.review.rate';
  const RATES = [0.75, 1, 1.5, 2];
  const PREVIEW_SECONDS = 4;
  const states = new WeakMap();          // modal element → {loop, preview, help}
  const st = (ctx) => { let s = states.get(ctx.m); if (!s) { s = { loop: false, preview: null, help: null }; states.set(ctx.m, s); } return s; };

  // Seek offset: while the narrated file plays, clip times sit after the spoken intro.
  const introOff = (ctx) => {
    const cl = ctx.cl, src = ctx.v.getAttribute('src');
    return cl.narration_preview_url && src === cl.narration_preview_url ? Number((cl.narration || {}).intro_duration || 0) : 0;
  };
  const clipTime = (ctx) => Math.max(0, ctx.v.currentTime - introOff(ctx));
  const play = (v) => { const p = v.play(); if (p && p.catch) p.catch(() => {}); };   // autoplay/no-media rejections are not errors here
  const seekBy = (ctx, d) => { const v = ctx.v; v.currentTime = Math.max(0, Math.min(v.duration || Infinity, v.currentTime + d)); };
  const tenth = (s) => fmtTime(s) + '.' + Math.round(s % 1 * 10);
  const setSlider = (ctx, id, value) => {       // the core's oninput handler redraws the trim labels and re-runs the gate
    const el = $(id, ctx.m); if (!el) return false;
    el.value = Math.max(Number(el.min) || 0, Math.min(Number(el.max) || Infinity, value)).toFixed(1);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  };

  const css = `
.rk-row{display:flex;align-items:center;gap:4px;flex-wrap:wrap;margin-top:6px;font-size:12px;color:var(--muted)}
.rk-row.center{justify-content:center}
.rk-row .btn.ghost{padding:3px 8px;font-size:12px;color:var(--text-2)}
.rk-row .btn.ghost.on{background:rgba(255,255,255,.09);color:var(--text)}
.rk-row .btn.ghost.playing{color:var(--text)}
.rk-cmp-btn span{display:inline-block;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom;margin-left:4px}
.rk-help{position:fixed;right:18px;bottom:18px;z-index:60;min-width:270px;max-width:360px;padding:12px 14px;background:var(--surface-2);border:1px solid var(--line-2);border-radius:var(--r);box-shadow:var(--shadow);font-size:12px;color:var(--text-2)}
.rk-help h3{display:flex;justify-content:space-between;align-items:center;margin:0 0 8px;font-size:13px;color:var(--text)}
.rk-help dl{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;margin:0}
.rk-help dt{text-align:right;white-space:nowrap}
.rk-help dd{margin:0;color:var(--muted)}
.rk-help kbd{display:inline-block;min-width:16px;padding:0 5px;border:1px solid var(--line-2);border-radius:5px;background:var(--surface-3);font-family:var(--mono);font-size:11px;color:var(--text);text-align:center}
.rk-help .sep{grid-column:1/-1;height:1px;background:var(--line);margin:4px 0}`;
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  // ---------------------------------------------------------------- shortcuts
  const SHORTCUTS = [        // [keys, what it does]; the core's own keys are listed first so the ? overlay is complete
    ['Space', 'Play / pause'], ['A', 'Approve (only when the checks pass)'], ['R', 'Reject'], ['→', 'Next clip'], ['Esc', 'Close'],
    null,
    ['J / K', 'Back / forward 5 s'], [', / .', 'Back / forward 0.1 s'], ['L', 'Loop the trimmed range'], ['M', 'Mute / unmute'],
    ['1 – 4', 'Tick / untick checklist item'], ['I / O', 'Trim start / end = here'], ['H', 'Edit the hook'], ['?', 'This list'],
  ];
  const toggleHelp = (ctx) => {
    const s = st(ctx);
    if (s.help) { s.help.remove(); s.help = null; return; }
    const box = document.createElement('div');
    box.className = 'rk-help';
    box.innerHTML = `<h3>Keyboard shortcuts <button class="btn sm ghost" title="Close">✕</button></h3><dl>${SHORTCUTS.map((x) => x ? `<dt>${x[0].split(' / ').map((k) => `<kbd>${esc(k)}</kbd>`).join(' / ')}</dt><dd>${esc(x[1])}</dd>` : '<div class="sep"></div>').join('')}</dl>`;
    $('h3 button', box).onclick = () => toggleHelp(ctx);
    ctx.m.appendChild(box);                  // inside the modal so it goes away with it
    s.help = box;
  };
  const toggleLoop = (ctx) => {
    const s = st(ctx);
    s.loop = !s.loop;
    if (!s.loop) { toast('Loop off'); return; }
    const a = Number($('#ts', ctx.m).value), b = Number($('#te', ctx.m).value);
    const t = clipTime(ctx);
    if (t < a || t >= b) ctx.v.currentTime = a + introOff(ctx);
    if (ctx.v.paused) play(ctx.v);
    toast(`Looping ${tenth(a)} – ${tenth(b)}`);
  };
  const tick = (ctx, n) => {
    const box = $$('#checks input', ctx.m)[n - 1];
    if (!box) return;
    box.checked = !box.checked;
    box.dispatchEvent(new Event('change', { bubbles: true }));   // the core's onchange re-runs the Approve gate
  };
  const keys = {
    j: (ctx) => seekBy(ctx, -5), k: (ctx) => seekBy(ctx, 5),
    ',': (ctx) => seekBy(ctx, -0.1), '.': (ctx) => seekBy(ctx, 0.1),
    l: toggleLoop,
    m: (ctx) => { ctx.v.muted = !ctx.v.muted; },
    i: (ctx) => { const t = clipTime(ctx); if (setSlider(ctx, '#ts', t)) toast(`Trim start = ${tenth(t)}`); },
    o: (ctx) => { const t = clipTime(ctx); if (setSlider(ctx, '#te', t)) toast(`Trim end = ${tenth(t)}`); },
    h: (ctx) => { const h = $('#hook', ctx.m); if (h) { h.focus(); h.select(); } },
    '?': toggleHelp,
  };
  for (let n = 1; n <= 4; n++) keys[String(n)] = (ctx) => tick(ctx, n);
  Object.keys(keys).forEach((k) => { if (!REVIEW_KEYS[k]) REVIEW_KEYS[k] = keys[k]; });

  // ---------------------------------------------------------------- speed
  const savedRate = () => { try { const r = Number(localStorage.getItem(RATE_KEY)); return RATES.includes(r) ? r : 1; } catch (e) { return 1; } };
  const applyRate = (v, r) => { v.defaultPlaybackRate = r; v.playbackRate = r; };   // a new src resets playbackRate to the default
  const speedRow = (ctx) => {
    const row = document.createElement('div');
    row.className = 'rk-row center';
    row.id = 'rk-speed';
    const draw = (cur) => {
      row.innerHTML = `<span>Speed</span>${RATES.map((r) => `<button class="btn sm ghost ${r === cur ? 'on' : ''}" data-rate="${r}">${r}×</button>`).join('')}<button class="btn sm ghost" id="rk-help-btn" title="Keyboard shortcuts (?)">?</button>`;
      $$('[data-rate]', row).forEach((b) => b.onclick = () => { const r = Number(b.dataset.rate); applyRate(ctx.v, r); try { localStorage.setItem(RATE_KEY, String(r)); } catch (e) { /* private mode */ } draw(r); });
      $('#rk-help-btn', row).onclick = () => toggleHelp(ctx);
    };
    draw(savedRate());
    applyRate(ctx.v, savedRate());
    return row;
  };

  // ---------------------------------------------------------------- compare hooks: hear each candidate where it is said
  const compareRow = (ctx, cands) => {
    const row = document.createElement('div');
    row.className = 'rk-row';
    row.id = 'rk-compare';
    row.innerHTML = `<span title="Plays ${PREVIEW_SECONDS} s from where each candidate is said">Compare hooks</span>` +
      cands.map((h, i) => `<button class="btn sm ghost rk-cmp-btn" data-i="${i}" title="${esc(h.text)} — said at ${fmtTime(h.t)}; plays ${PREVIEW_SECONDS} s">▶<span>${esc(h.text)}</span></button>`).join('');
    $$('.rk-cmp-btn', row).forEach((b) => b.onclick = () => {
      const h = cands[Number(b.dataset.i)], v = ctx.v, s = st(ctx);
      const from = Number(h.t) + introOff(ctx);
      $$('.rk-cmp-btn', row).forEach((x) => x.classList.toggle('playing', x === b));
      s.preview = { until: from + PREVIEW_SECONDS, btn: b };
      v.currentTime = from;
      play(v);
    });
    return row;
  };

  REVIEW_EXTRAS.push((ctx) => {
    const { m, v } = ctx, s = st(ctx);
    const toggle = $('#play-toggle', m);
    if (toggle) toggle.insertAdjacentElement('afterend', speedRow(ctx));
    const cands = $('#hook-cands', m);
    if (cands) {
      get(`/api/clips/${ctx.cl.id}/hooks`).then((all) => {     // the same three alternatives the core shows as chips
        const alts = (all || []).filter((h) => h.text !== ctx.cl.hook).slice(0, 3);
        if (alts.length && document.body.contains(cands)) cands.insertAdjacentElement('afterend', compareRow(ctx, alts));
      }).catch(() => {});
    }
    v.addEventListener('timeupdate', () => {
      if (s.preview && v.currentTime >= s.preview.until) { v.pause(); s.preview.btn.classList.remove('playing'); s.preview = null; }
      if (s.loop) {
        const a = Number($('#ts', m).value), b = Number($('#te', m).value), off = introOff(ctx);
        if (v.currentTime - off >= b) { v.currentTime = a + off; if (v.paused) play(v); }
      }
    });
    v.addEventListener('pause', () => { if (s.preview && v.currentTime < s.preview.until) { s.preview.btn.classList.remove('playing'); s.preview = null; } });
  });
})();
