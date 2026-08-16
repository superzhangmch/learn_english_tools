/* Shared LLM-interpretation widget: floating ✨ popup + right-side drawer with
 * streaming answer and multi-turn follow-up chat. Used by BOTH the news reader
 * and the e-ink dashboard so there is a single implementation.
 *
 * Config (set as globals BEFORE loading this script):
 *   window.INTERPRET_BASE   base URL of the reader API (default '' = same origin)
 *   window.INTERPRET_STREAM set false to render the answer once at the end
 *                           (e-ink friendly: avoids per-chunk refresh flicker)
 *
 * API:
 *   Interpret.popupAt(rect, payload, below)  show the ✨ button near a selection
 *   Interpret.open(payload)                  open the drawer directly
 *   Interpret.hidePopup() / Interpret.close()
 *   payload = { text, title, context, parts:{left,sel,right} }   (parts optional)
 */
(function () {
  const BASE = (window.INTERPRET_BASE || '').replace(/\/$/, '');
  const STREAM = window.INTERPRET_STREAM !== false;
  const FS_KEY = 'nrw_ans_fs', FS_MIN = 13, FS_MAX = 32, FS_DEF = 19;
  const DEFAULT_HINT = '追问… （回车发送）';

  const CSS = `
  .nrw-pop{position:fixed;display:none;z-index:2147483000;transform:translate(-50%,-100%);
    background:var(--panel,#fff);color:var(--ink,#111);border:1.5px solid var(--ink,#111);border-radius:8px;
    font-size:14px;font-weight:700;white-space:nowrap;box-shadow:0 2px 6px rgba(0,0,0,.15);overflow:hidden;
    font-family:var(--sans,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Heiti SC",sans-serif)}
  .nrw-pop .nrw-b{display:inline-block;padding:7px 14px;cursor:pointer;touch-action:manipulation}
  .nrw-pop .nrw-b+.nrw-b{border-left:1.5px solid var(--ink,#111)}
  .nrw-pop.below{transform:translate(-50%,0)}
  .nrw-pop::after{content:"";position:absolute;left:50%;bottom:-6px;transform:translateX(-50%);
    border:5px solid transparent;border-top-color:var(--ink,#111);border-bottom:none}
  .nrw-pop.below::after{top:-6px;bottom:auto;border-bottom-color:var(--ink,#111);border-top:none}
  #nrw-drawer{position:fixed;top:0;right:0;height:100%;width:min(430px,94vw);z-index:2147483001;
    background:var(--panel,#fff);color:var(--ink,#111);border-left:2px solid var(--line,#000);
    box-shadow:-8px 0 30px rgba(0,0,0,.18);transform:translateX(100%);transition:transform .28s ease;
    display:flex;flex-direction:column;--nrw-fs:${FS_DEF}px;
    font-family:var(--sans,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Heiti SC",sans-serif)}
  #nrw-drawer.open{transform:translateX(0)}
  #nrw-drawer .dh{display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line,#ddd)}
  #nrw-drawer .dh .t{font-weight:700;font-size:14px;flex:1}
  #nrw-drawer .dh button{cursor:pointer;font-family:inherit;color:var(--ink,#111);background:none;touch-action:manipulation}
  #nrw-drawer .dh .fs{border:1px solid var(--line,#ccc);border-radius:8px;padding:4px 8px;min-width:30px;font-size:12px;font-weight:700}
  #nrw-drawer .dh .cls{border:none;font-size:24px;line-height:1;color:var(--ink-soft,#888)}
  #nrw-menu{position:fixed;top:50px;right:8px;z-index:2147483002;display:none;flex-direction:column;gap:11px;
    padding:12px 14px;background:var(--panel,#fff);color:var(--ink,#111);border:1px solid var(--line,#ccc);border-radius:11px;
    box-shadow:0 8px 28px rgba(0,0,0,.28);max-width:82vw;
    font-family:var(--sans,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Heiti SC",sans-serif)}
  #nrw-menu.open{display:flex}
  #nrw-menu .grp{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  #nrw-menu .lbl{font-size:12px;color:var(--ink-soft,#888);font-weight:700;min-width:2.4em}
  #nrw-menu .models{display:flex;flex-wrap:wrap;gap:6px}
  #nrw-menu .sb{border:1px solid var(--line,#ccc);border-radius:7px;padding:5px 11px;font-size:12.5px;font-weight:700;
    cursor:pointer;background:var(--panel,#fff);color:var(--ink,#111);text-decoration:none;touch-action:manipulation}
  #nrw-menu .sb.on{background:var(--ink,#111);color:var(--panel,#fff);border-color:var(--ink,#111)}
  #nrw-drawer .db{padding:16px;overflow-y:auto;flex:1}
  #nrw-drawer .sel{border:1px solid var(--line,#ccc);border-radius:8px;padding:10px 13px;margin-bottom:14px;
    font-size:var(--nrw-fs);line-height:1.55;color:var(--ink-soft,#555);max-height:34vh;overflow:auto}
  #nrw-drawer .sel mark{background:var(--accent-soft,#e8e8e8);color:var(--ink,#111);font-weight:600;border-radius:3px;padding:0 2px}
  #nrw-drawer .sel .lbl{display:block;font-size:11px;letter-spacing:.5px;text-transform:uppercase;opacity:.6;margin-bottom:6px}
  #nrw-drawer .turn{margin-bottom:14px;font-size:var(--nrw-fs);line-height:1.65}
  #nrw-drawer .turn.q{background:var(--accent-soft,#eee);border-radius:10px 10px 10px 2px;padding:8px 12px}
  #nrw-drawer .turn.q::before{content:"你：";font-weight:700;color:var(--accent,#000)}
  #nrw-drawer .turn.a b,#nrw-drawer .turn.a strong{font-weight:700}
  #nrw-drawer .turn.a h1,#nrw-drawer .turn.a h2,#nrw-drawer .turn.a h3{font-size:1.08em;margin:.9em 0 .35em}
  #nrw-drawer .turn.a p{margin:0 0 .7em}
  #nrw-drawer .turn.a ul{padding-left:1.3em;margin:.5em 0}
  #nrw-drawer .turn.a code{background:var(--accent-soft,#eee);padding:1px 5px;border-radius:4px;font-size:.9em}
  #nrw-drawer .think{color:var(--ink-soft,#888);font-size:14px}
  #nrw-drawer .nrw-err{color:#c0392b;font-size:var(--nrw-fs)}
  #nrw-drawer .nrw-retry{margin-top:10px;padding:6px 16px;border:1.5px solid var(--ink,#111);border-radius:8px;
    background:var(--panel,#fff);color:var(--ink,#111);font-weight:700;cursor:pointer;font-family:inherit;touch-action:manipulation}
  #nrw-drawer .df{border-top:1px solid var(--line,#ddd);padding:12px 14px;display:flex;gap:8px}
  #nrw-drawer .df input{flex:1;padding:9px 12px;border:1px solid var(--line,#ccc);border-radius:9px;
    background:var(--bg,#fff);color:var(--ink,#111);font-size:16px;outline:none;font-family:inherit}
  /* send button uses --ink/--panel (defined in every theme) so it's high-contrast
     in BOTH light and dark — don't rely on --accent (undefined on some pages) */
  #nrw-drawer .df button{padding:9px 14px;border:1.5px solid var(--ink,#111);border-radius:9px;
    background:var(--ink,#111);color:var(--panel,#fff);font-weight:700;cursor:pointer;font-family:inherit;touch-action:manipulation}
  #nrw-drawer .df button.stop{background:var(--panel,#fff);color:var(--ink,#111)}
  @media (max-width:640px){
    #nrw-drawer{top:auto;bottom:0;left:0;right:0;width:100%;height:74%;border-left:none;
      border-top:2px solid var(--line,#000);border-radius:16px 16px 0 0;transform:translateY(100%);box-shadow:0 -8px 30px rgba(0,0,0,.18)}
    #nrw-drawer.open{transform:translateY(0)}
  }
  .nrw-chip{position:fixed;right:16px;bottom:calc(16px + env(safe-area-inset-bottom));z-index:2147483000;display:none;
    align-items:center;gap:6px;background:var(--panel,#fff);color:var(--ink,#111);border:1.5px solid var(--ink,#111);
    border-radius:22px;padding:9px 15px;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 3px 12px rgba(0,0,0,.2);
    font-family:var(--sans,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Heiti SC",sans-serif);touch-action:manipulation}
  .nrw-chip.show{display:inline-flex}
  .nrw-chip.busy{animation:nrw-blink 1s ease-in-out infinite}
  @keyframes nrw-blink{0%,100%{opacity:1}50%{opacity:.3}}
  /* small status dot, vertically centered on the text line (no glyph = nothing to mis-center) */
  .nrw-mk{display:inline-block;width:.6em;height:.6em;border-radius:50%;vertical-align:middle;
    margin:0 .15em 0 .35em;cursor:pointer;user-select:none;touch-action:manipulation;
    box-shadow:0 0 0 1.5px #fff,0 0 1px rgba(0,0,0,.45)}
  .nrw-mk.busy{background:#16a34a;animation:nrw-blink .9s ease-in-out infinite}   /* green = 解读中 */
  .nrw-mk.ready{background:#dc2626}                                               /* red   = 可点看 */
  `;

  let pop, drawer, thread, selBox, input, sendBtn, menuEl;
  let payload = {}, pendingRange = null, active = null;
  const isMobile = () => window.matchMedia('(max-width:640px)').matches;
  const isOpen = () => drawer && drawer.classList.contains('open');

  const esc = (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  function md(src) {
    const lines = String(src).split('\n'); let out = '', inList = false;
    const inline = (t) => esc(t)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>');
    for (const ln of lines) {
      if (/^\s*[-*]\s+/.test(ln)) { if (!inList) { out += '<ul>'; inList = true; } out += '<li>' + inline(ln.replace(/^\s*[-*]\s+/, '')) + '</li>'; }
      else { if (inList) { out += '</ul>'; inList = false; } const h = ln.match(/^(#{1,3})\s+(.*)/);
        if (h) out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; else if (ln.trim() !== '') out += '<p>' + inline(ln) + '</p>'; }
    }
    if (inList) out += '</ul>';
    return out;
  }

  // font size (persisted)
  const fsGet = () => Math.max(FS_MIN, Math.min(FS_MAX, parseInt(localStorage.getItem(FS_KEY) || FS_DEF, 10)));
  const fsSet = (px) => { px = Math.max(FS_MIN, Math.min(FS_MAX, px)); localStorage.setItem(FS_KEY, px); if (drawer) drawer.style.setProperty('--nrw-fs', px + 'px'); };

  // ---- ⚙ settings: model / font / display mode ----
  const modelName = () => localStorage.getItem('nrw_model') || '';
  let _models = null;
  async function buildModels() {
    const box = menuEl.querySelector('.models');
    if (!_models) {
      box.textContent = '…';
      try {
        const d = await (await fetch(BASE + '/api/models')).json();
        _models = d.models || [];
        if (!modelName() && d.default) localStorage.setItem('nrw_model', d.default);
      } catch (_) { _models = []; }
    }
    box.innerHTML = '';
    const cur = modelName();
    _models.forEach(name => {
      const b = document.createElement('button');
      b.className = 'sb mb' + (name === cur ? ' on' : ''); b.textContent = name;
      b.onclick = () => { localStorage.setItem('nrw_model', name); buildModels(); };
      box.appendChild(b);
    });
  }
  // --fs is the book reader's text size; the rest are the news reader's names.
  // Exposed on the public API so a page can drive it from its own controls
  // instead of duplicating the clamping and persistence.
  function bumpFont(delta) {
    const px = Math.max(12, Math.min(34, (parseInt(localStorage.getItem('nrw_fs') || '18', 10)) + delta));
    localStorage.setItem('nrw_fs', px);
    ['--fs', '--art-fs', '--nfs', '--nrw-fs'].forEach(k => document.documentElement.style.setProperty(k, px + 'px'));
    if (drawer) drawer.style.setProperty('--nrw-fs', px + 'px');
    ['nr_art_fs', 'news_fs', 'nrw_ans_fs'].forEach(k => localStorage.setItem(k, px));  // persist for page-owned controls
    return px;
  }
  function setTheme(t) {
    document.documentElement.dataset.theme = t;
    ['nrw_theme', 'news_theme'].forEach(k => localStorage.setItem(k, t));
    const m = document.querySelector('meta[name=theme-color]'); if (m) m.content = t === 'dark' ? '#121212' : '#ffffff';
    markTheme();
  }
  function markTheme() {
    const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    if (menuEl) menuEl.querySelectorAll('.tb').forEach(b => b.classList.toggle('on', b.dataset.t === cur));
  }

  function ensureDOM() {
    if (drawer) return;
    const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);

    pop = document.createElement('div'); pop.className = 'nrw-pop';
    pop.innerHTML = '<span class="nrw-b" data-m="interpret">✨ 解读</span>' +
                    '<span class="nrw-b" data-m="grammar">语法</span>' +
                    '<span class="nrw-b" data-m="background">背景</span>';
    pop.addEventListener('click', (e) => {
      const b = e.target.closest('.nrw-b'); if (!b) return;
      hidePopup();
      // mobile: always background (marker), tap to view. desktop: open immediately,
      // UNLESS a drawer is already showing — then background so it isn't disrupted.
      open(payload, isMobile() || isOpen(), b.dataset.m);
    });
    document.body.appendChild(pop);

    drawer = document.createElement('aside'); drawer.id = 'nrw-drawer';
    drawer.innerHTML =
      '<div class="dh"><div class="t">✨ 解读</div>' +
      '<button class="cls" title="关闭">×</button></div>' +
      '<div class="db"><div class="sel"></div><div class="thread"></div></div>' +
      '<div class="df"><input type="text" placeholder="追问… （回车发送）" autocomplete="off"><button class="send">发送</button></div>';
    document.body.appendChild(drawer);

    // page-level settings dropdown (opened by each page's ⚙ button via Interpret.settings())
    menuEl = document.createElement('div'); menuEl.id = 'nrw-menu';
    menuEl.innerHTML =
      '<div class="grp"><span class="lbl">模型</span><span class="models"></span></div>' +
      '<div class="grp"><span class="lbl">字号</span><button class="sb" data-d="-1">A−</button><button class="sb" data-d="1">A+</button></div>' +
      '<div class="grp"><span class="lbl">显示</span><button class="sb tb" data-t="light">☀︎ 浅色</button><button class="sb tb" data-t="dark">☾ 深色</button></div>' +
      '<div class="grp"><a class="sb" href="/vocab" target="_blank" rel="noopener">📓 生词本</a></div>';
    document.body.appendChild(menuEl);

    selBox = drawer.querySelector('.sel');
    thread = drawer.querySelector('.thread');
    input = drawer.querySelector('.df input');
    sendBtn = drawer.querySelector('.df .send');
    drawer.querySelector('.cls').onclick = close;
    menuEl.querySelectorAll('.sb[data-d]').forEach(b => b.onclick = () => bumpFont(+b.dataset.d));
    menuEl.querySelectorAll('.tb').forEach(b => b.onclick = () => setTheme(b.dataset.t));
    document.addEventListener('mousedown', (e) => { if (menuEl.classList.contains('open') && !menuEl.contains(e.target) && !(e.target.closest && e.target.closest('.nrw-gear'))) menuEl.classList.remove('open'); });
    sendBtn.onclick = () => { if (active && active.streaming) { active.abort && active.abort.abort(); } else followup(); };
    input.onkeydown = (e) => {
      // ignore Enter while an IME (e.g. Chinese pinyin) is still composing —
      // that Enter is meant to commit the candidate, not send the message
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === 'Enter' && !(active && active.streaming)) followup();
    };
    fsSet(fsGet());

    // click outside the drawer & popup closes it
    document.addEventListener('mousedown', (e) => {
      if (!drawer.classList.contains('open')) return;
      // desktop side-by-side (drawer sits beside the content): don't close when the
      // user clicks the article — let them read the left while the drawer stays open
      if (window.matchMedia('(min-width: 900px)').matches) return;
      if (drawer.contains(e.target) || pop.contains(e.target) || (e.target.closest && e.target.closest('.nrw-mk'))) return;
      close();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

    // swipe to dismiss: right-swipe (panel) or down-swipe from the top (bottom sheet)
    let sx = 0, sy = 0, sw = false;
    drawer.addEventListener('touchstart', (e) => { const t = e.touches[0]; sx = t.clientX; sy = t.clientY; sw = true; }, { passive: true });
    drawer.addEventListener('touchend', (e) => {
      if (!sw) return; sw = false;
      const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
      const db = drawer.querySelector('.db');
      if (dx > 80 && Math.abs(dx) > Math.abs(dy) * 1.4) close();
      else if (dy > 70 && Math.abs(dy) > Math.abs(dx) * 1.4 && (!db || db.scrollTop <= 0)) close();
    }, { passive: true });
  }

  function hidePopup() { if (pop) pop.style.display = 'none'; }
  function popupAt(rect, pl, below) {
    ensureDOM(); payload = pl || {};
    try { const g = window.getSelection(); pendingRange = g.rangeCount ? g.getRangeAt(0).cloneRange() : null; } catch (_) { pendingRange = null; }
    pop.classList.toggle('below', !!below);
    pop.style.display = 'block';   // must be visible before we can measure it
    // Clamp into the viewport. The bubble is centre-anchored (translate(-50%,…)),
    // so a word at the very start/end of a line used to place half of it
    // off-screen — it looked like the button simply never appeared.
    const M = 6, w = pop.offsetWidth, h = pop.offsetHeight, half = w / 2;
    const cx = Math.min(Math.max(rect.left + rect.width / 2, half + M),
                        window.innerWidth - half - M);
    // Keep a deliberate gap from the selection so our button clears the OS's own
    // selection callout / handles — the SAME distance whether we end up below the
    // text (top = bubble's top edge) or above it (top = bubble's bottom edge, since
    // the base .nrw-pop is translate(-50%,-100%)).
    const GAP = 60;
    let top = below ? rect.bottom + GAP : rect.top - GAP;
    if (below && top + h > window.innerHeight - M) {     // no room below -> flip above
      pop.classList.remove('below');
      top = Math.max(rect.top - GAP, h + M);
    } else if (!below && top - h < M) {                  // no room above -> flip below
      pop.classList.add('below');
      top = rect.bottom + GAP;
    }
    pop.style.left = cx + 'px';
    pop.style.top = top + 'px';
  }

  function renderContext(s) {
    const p = s.payload.parts;
    // ctxLabel describes what was sent instead of reproducing it. For a run of
    // pages that is the point: you just read them, and echoing 29 KB back at you
    // is noise.
    if (s.payload.ctxLabel) {
      selBox.innerHTML = '<span class="lbl">' + esc(s.payload.ctxLabel) + '</span>';
      return;
    }
    if (p && (p.left || p.right || p.sel)) {
      selBox.innerHTML = '<span class="lbl">原句 · 已选中高亮</span>' + esc(p.left) + '<mark>' + esc(p.sel) + '</mark>' + esc(p.right);
    } else {
      const ctx = s.payload.context || s.payload.text || '', t = s.payload.text || '', i = ctx.indexOf(t);
      selBox.innerHTML = '<span class="lbl">原句 · 已选中高亮</span>' +
        (i >= 0 ? esc(ctx.slice(0, i)) + '<mark>' + esc(t) + '</mark>' + esc(ctx.slice(i + t.length)) : '<mark>' + esc(t) + '</mark>');
    }
  }
  function addTurn(cls) { const el = document.createElement('div'); el.className = 'turn ' + cls; thread.appendChild(el); return el; }
  function renderThread(s) {
    thread.innerHTML = '';
    s.turns.forEach(t => {
      if ('q' in t) { addTurn('q').textContent = t.q; return; }
      const el = addTurn('a');
      if (t.err) {                       // failed turn → error + a Retry button
        el.innerHTML = '<div class="nrw-err">出错：' + esc(t.err) + '</div>';
        const b = document.createElement('button');
        b.className = 'nrw-retry'; b.textContent = '重试';
        b.onclick = () => runTurn(s, t);
        el.appendChild(b);
      } else {
        el.innerHTML = t.a ? md(t.a) : '<div class="think">正在解读…</div>';
      }
    });
    const last = thread.lastElementChild; if (last) last.scrollIntoView({ block: 'end' });
  }
  function setBusy(b) { sendBtn.textContent = b ? '停止' : '发送'; sendBtn.classList.toggle('stop', b); }
  function markMarker(s, state) { if (s.marker) s.marker.className = 'nrw-mk ' + (state === 'busy' ? 'busy' : 'ready'); }
  function close() { if (drawer) drawer.classList.remove('open'); }   // background sessions keep running

  // inline ✨ marker dropped right where the user selected (mobile deferred mode)
  function makeMarker(s) {
    // sessions not started from a text selection have nowhere to put a marker;
    // without this the dot lands at the end of <body>
    if (s.payload && s.payload.noMarker) return null;
    const mk = document.createElement('span'); mk.className = 'nrw-mk busy';  // plain dot, no glyph
    mk.onclick = (e) => { e.stopPropagation(); openSession(s); };
    try {
      if (pendingRange) { const r = pendingRange.cloneRange(); r.collapse(false); r.insertNode(mk); }
      else document.body.appendChild(mk);
    } catch (_) { document.body.appendChild(mk); }
    return mk;
  }
  const TITLES = { grammar: '语法分析', background: '背景', ask: '问这几页', interpret: '✨ 解读' };
  function setTitle(s) {
    const t = drawer.querySelector('.dh .t');
    if (t) t.textContent = TITLES[s.mode] || TITLES.interpret;
    // lets a page show mode-specific controls inside the drawer via CSS
    drawer.dataset.mode = s.mode;
  }
  function openSession(s) {
    active = s; markMarker(s, 'ready'); setTitle(s);
    renderContext(s); renderThread(s); setBusy(!!s.streaming);
    drawer.classList.add('open');
  }

  // mode: 'interpret' (default) or 'grammar'. deferred=true (mobile): drop an inline
  // marker + run in the background (reading isn't interrupted); tap it to view.
  function open(pl, deferred, mode) {
    ensureDOM();
    const s = { payload: pl || {}, mode: mode || 'interpret', convo: [], turns: [], marker: null, abort: null, streaming: false };
    s.marker = makeMarker(s);   // always drop a marker so you can revisit this spot
    input.placeholder = pl.inputHint || DEFAULT_HINT;
    // awaitQuestion: open with the context shown but send nothing — the user's
    // own first question comes through the same input as every follow-up, so
    // there is one place to type rather than one per turn number
    if (pl.awaitQuestion) {
      active = s; setTitle(s); renderContext(s); thread.innerHTML = ''; input.value = '';
      setBusy(false); drawer.classList.add('open'); input.focus();
      return;
    }
    if (deferred) {
      runTurn(s);               // background — click the marker to view it
    } else {
      active = s; setTitle(s); renderContext(s); thread.innerHTML = ''; input.value = '';
      drawer.classList.add('open'); runTurn(s);
    }
  }

  /** Patch the active session's payload — e.g. a page range widened mid-chat. */
  function update(patch) {
    if (!active) return;
    Object.assign(active.payload, patch || {});
    if (isOpen()) renderContext(active);
  }
  function followup() {
    if (!active) return;
    const q = input.value.trim(); if (!q || active.streaming) return;
    input.value = '';
    active.turns.push({ q }); active.convo.push({ role: 'user', content: q });
    renderThread(active); runTurn(active);
  }

  // `existing` = re-run this turn in place (the Retry button); else append a new turn
  async function runTurn(s, existing) {
    if (s.abort) s.abort.abort();
    s.abort = new AbortController(); s.streaming = true; markMarker(s, 'busy');
    const turn = existing || { a: '' };
    if (!existing) s.turns.push(turn);
    turn.err = null; turn.a = '';        // reset (clears a prior error on retry)
    if (s === active && isOpen()) { renderThread(s); setBusy(true); }
    let acc = '';
    const paint = () => { if (s === active && isOpen()) { const el = thread.querySelector('.turn.a:last-child'); if (el) { el.innerHTML = md(acc); el.scrollIntoView({ block: 'end' }); } } };
    try {
      const r = await fetch(BASE + '/api/interpret', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        // where-in-the-book fields go with every request: the same word means
        // different things in different books, and the model needs the chapter
        // to resolve "the Republic", "censor", "province", …
        //
        // payload.extra is merged in verbatim, which is how a page can name a
        // passage the server can read for itself instead of uploading it.
        body: JSON.stringify({
          ...(s.payload.extra || {}),
          text: s.payload.text || '',
          context: s.payload.context || '',
          mode: s.mode || 'interpret', model: modelName(), history: s.convo,
          book: s.payload.book || '', author: s.payload.author || '',
          chapter: s.payload.chapter != null ? s.payload.chapter : -1,
          chapter_title: s.payload.chapter_title || '', page: s.payload.page || '',
        }),
        signal: s.abort.signal,
      });
      if (!r.ok) throw new Error('服务返回 HTTP ' + r.status);
      const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await rd.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n'); buf = parts.pop();
        for (const pt of parts) {
          const line = pt.split('\n').find(l => l.startsWith('data:')); if (!line) continue;
          let o; try { o = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
          if (o.error) { turn.err = o.error; break; }
          if (o.delta) { acc += o.delta; turn.a = acc; if (STREAM) paint(); }
        }
        if (turn.err) break;
      }
      if (!turn.err) {
        if (acc.trim()) { turn.a = acc; s.convo.push({ role: 'assistant', content: acc }); }
        else turn.a = '（无内容）';
      }
    } catch (e) {
      if (e.name === 'AbortError') { turn.a = acc + (acc.trim() ? '  （已停止）' : '已停止'); if (acc.trim()) s.convo.push({ role: 'assistant', content: acc }); }
      else turn.err = e.message || String(e);   // → renderThread shows error + Retry
    } finally {
      s.streaming = false; markMarker(s, 'ready');
      if (s === active && isOpen()) { renderThread(s); setBusy(false); }
    }
  }

  function settings() { ensureDOM(); const o = menuEl.classList.toggle('open'); if (o) { buildModels(); markTheme(); } }
  window.Interpret = { popupAt, open, hidePopup, close, settings, bumpFont, update };
  if (document.body) ensureDOM(); else document.addEventListener('DOMContentLoaded', ensureDOM);
})();
