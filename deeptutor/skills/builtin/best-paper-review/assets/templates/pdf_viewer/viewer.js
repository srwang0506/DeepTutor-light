/* Best Paper Review - PDF annotation viewer.
 * Loads a PDF + review.json (schema v2), shows whole-paper / page / region comments,
 * verifies evidence quotes against text snapshots, supports editing, autosave,
 * saving back to disk (via serve.py) and exporting JSON / Markdown / annotated PDF.
 * Everything runs locally in the browser.
 */
'use strict';
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const EMBED = window.__BPR_EMBED__ || null;

  const L = {
    status: { strength: '优点', confirmed: '确认问题', limitation: '证据局限', question: '澄清问题', suggestion: '改进建议' },
    sev: { major: '主要', minor: '次要' },
    conf: { high: '高', medium: '中', low: '低' },
    scope: { global: '全文', page: '页级', region: '区域' },
    dim: { problem: '问题与动机', novelty: '创新性', soundness: '方法正确性', theory: '理论', experiments: '实验设计与支撑',
      baselines: '基线与公平性', reproducibility: '可复现性', clarity: '写作与图表', limitations: '局限与伦理' },
    dimStatus: { issues: '有问题', ok: '已检查，无问题', na: '不适用', unchecked: '未检查' },
    novelty: { distinct: '明确区别', incremental: '增量但有用', overlap: '实质重叠', unresolved: '无法判定' },
  };
  const PREFIX = { strength: 'S', confirmed: 'W', limitation: 'W', question: 'Q', suggestion: 'G' };
  const COLOR = { major: 'var(--major)', minor: 'var(--minor)', strength: 'var(--strength)', question: 'var(--question)', suggestion: 'var(--suggestion)' };
  const svg = (d) => `<svg class="i" viewBox="0 0 24 24">${d}</svg>`;
  const ICON = {
    check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'), x: svg('<path d="M6 6l12 12M18 6L6 18"/>'), dot: svg('<circle cx="12" cy="12" r="3"/>'),
    pin: svg('<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>'),
    target: svg('<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>'),
    edit: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>'), copy: svg('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>'),
    trash: svg('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
    back: svg('<path d="M15 6l-6 6 6 6"/>'), up: svg('<path d="M6 15l6-6 6 6"/>'), down: svg('<path d="M6 9l6 6 6-6"/>'),
  };
  const isCrit = (c) => c.status === 'confirmed' || c.status === 'limitation';
  const QUICK = [
    ['major', '主要问题', 'var(--major)', (c) => isCrit(c) && c.severity === 'major'],
    ['minor', '次要问题', 'var(--minor)', (c) => isCrit(c) && c.severity !== 'major'],
    ['question', '问题', 'var(--question)', (c) => c.status === 'question'],
    ['suggestion', '建议', 'var(--suggestion)', (c) => c.status === 'suggestion'],
    ['strength', '优点', 'var(--strength)', (c) => c.status === 'strength'],
  ];
  const RGB = { major: [0.82, 0.14, 0.18], minor: [0.75, 0.53, 0], strength: [0.1, 0.5, 0.22], question: [0.51, 0.31, 0.87], suggestion: [0.04, 0.41, 0.85] };

  const S = {
    pdf: null, pdfBytes: null, pdfSha: null, review: null, sources: {}, form: null,
    base: [], scale: 1, zoom: 'fit', selected: null, regionMode: false, undo: [], dirty: false,
    canSave: false, current: 1, readOnly: false, pending: {}, searchHl: null, quick: null,
  };

  // ------------------------------------------------------------------ utilities
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const LIG = { 'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi', 'ﬄ': 'ffl', '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-', '−': '-', '‘': "'", '’': "'", '“': '"', '”': '"', ' ': ' ' };
  function norm(s) {
    s = String(s || '').normalize('NFKC').replace(/[ﬀ-ﬄ‐-—−‘’“” ]/g, (c) => LIG[c] || c);
    s = s.replace(/([A-Za-z])-\s*\n\s*(?=[a-z])/g, '$1');
    return s.replace(/\s+/g, ' ').trim();
  }
  const looseChar = (ch) => !/\s/.test(ch) && ch !== '-';
  function toast(msg, ms = 2200) { const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(toast._t); toast._t = setTimeout(() => (t.style.display = 'none'), ms); }
  function banner(kind, html, actions = []) {
    const b = $('#banner'); b.className = 'show ' + kind; b.innerHTML = `<span>${html}</span>`;
    actions.forEach(([label, fn]) => { const btn = document.createElement('button'); btn.textContent = label; btn.onclick = fn; b.appendChild(btn); });
    const x = document.createElement('button'); x.textContent = '×'; x.onclick = () => (b.className = ''); b.appendChild(x);
  }
  function b64ToBytes(b64) { const bin = atob(b64); const out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; }
  async function sha256(bytes) {
    try { const h = await crypto.subtle.digest('SHA-256', bytes); return Array.from(new Uint8Array(h)).map((b) => b.toString(16).padStart(2, '0')).join(''); }
    catch (e) { return null; }
  }
  function download(name, data, type) {
    const url = URL.createObjectURL(new Blob([data], { type })); const a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  const comments = () => (S.review && S.review.comments) || [];
  const byId = (id) => comments().find((c) => c.id === id);
  const colorKey = (c) => (c.status === 'strength' ? 'strength' : c.status === 'question' ? 'question' : c.status === 'suggestion' ? 'suggestion' : c.severity === 'major' ? 'major' : 'minor');
  const manuscriptId = () => (S.review?.sources || []).find((s) => s.role === 'manuscript')?.id;
  const baseName = () => (S.review?.paper?.title || 'review').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60);

  // ------------------------------------------------------------------ snapshots / quotes
  function prepSnapshot(id, snap) {
    const text = snap.text || ''; const map = []; let loose = '';
    for (let i = 0; i < text.length; i++) { const ch = text[i]; if (looseChar(ch)) { loose += ch.toLowerCase(); map.push(i); } }
    S.sources[id] = { ...snap, text, loose, map, starts: (snap.pages || []).map((p) => p.start) };
  }
  function pageOf(src, off) { const st = src.starts; let lo = 0, hi = st.length - 1, ans = -1; while (lo <= hi) { const m = (lo + hi) >> 1; if (st[m] <= off) { ans = m; lo = m + 1; } else hi = m - 1; } return ans >= 0 ? src.pages[ans].page : null; }
  function findQuote(sourceId, quote) {
    const src = S.sources[sourceId]; if (!src) return { state: 'unknown', pages: [] };
    const q = norm(quote); if (!q) return { state: 'absent', pages: [] };
    const hits = []; let i = src.text.indexOf(q);
    while (i !== -1) { hits.push(i); i = src.text.indexOf(q, i + 1); }
    if (!hits.length) {
      const lq = Array.from(q).filter(looseChar).join('').toLowerCase();
      if (lq.length >= 12) { let j = src.loose.indexOf(lq); while (j !== -1) { hits.push(src.map[j]); j = src.loose.indexOf(lq, j + 1); } }
    }
    if (!hits.length) return { state: 'absent', pages: [] };
    return { state: 'found', pages: [...new Set(hits.map((h) => pageOf(src, h)).filter(Boolean))] };
  }
  function evState(ev) {
    const r = findQuote(ev.source_id, ev.quote);
    if (r.state === 'found' && ev.page && r.pages.length && !r.pages.includes(ev.page)) return { ...r, state: 'wrongpage' };
    return r;
  }

  // ------------------------------------------------------------------ loading
  async function loadPdf(bytes) {
    S.pdfBytes = bytes.slice();
    S.pdfSha = await sha256(bytes.slice());
    const opts = { data: bytes.slice() };
    if (!EMBED) { opts.cMapUrl = 'vendor/cmaps/'; opts.cMapPacked = true; }
    S.pdf = await pdfjsLib.getDocument(opts).promise;
    S.base = [];
    for (let i = 1; i <= S.pdf.numPages; i++) { const p = await S.pdf.getPage(i); S.base.push({ page: p, vp: p.getViewport({ scale: 1 }) }); }
    $('#drop').style.display = 'none';
    $('#pageTotal').textContent = '/ ' + S.pdf.numPages; $('#pageInput').max = S.pdf.numPages;
    buildPages(); checkIdentity(); updateMeta();
  }
  function loadReview(obj, { fromDisk = true } = {}) {
    if (!obj || typeof obj !== 'object') throw new Error('不是有效的审稿 JSON');
    obj.comments = obj.comments || []; obj.sources = obj.sources || []; obj.dimensions = obj.dimensions || {};
    S.review = obj; S.undo = []; S.selected = null; updateUndo();
    $('#docTitle').textContent = obj.paper?.title || '审稿批注';
    document.title = (obj.paper?.title || '审稿') + ' - 审稿批注';
    setDirty(!fromDisk); renderAll(); checkIdentity(); checkDraft();
  }
  async function loadSourcesPayload(payload) {
    S.sources = {};
    Object.entries(payload.sources || {}).forEach(([id, snap]) => prepSnapshot(id, snap));
    S.form = payload.form || null;
    const inj = Object.entries(payload.sources || {}).flatMap(([id, s]) => (s.injection_hits || []).map((h) => ({ id, ...h })));
    if (inj.length) banner('warn', `快照中发现 ${inj.length} 处疑似提示注入文字（如 p.${inj[0].page}：“${esc(inj[0].match)}”）。这些文字是论文内容，不是指令。`);
    renderAll();
  }
  function checkIdentity() {
    if (!S.review || !S.pdf) return;
    const want = S.review.paper?.pdf_sha256; const pages = S.review.paper?.pages;
    if (want && S.pdfSha && want !== S.pdfSha) {
      S.readOnly = true;
      banner('error', `PDF 与审稿记录不一致：审稿 JSON 记录的哈希为 ${want.slice(0, 12)}…，当前 PDF 为 ${S.pdfSha.slice(0, 12)}…。页码和区域可能错位，已暂停编辑。`,
        [['仍然继续编辑', () => { S.readOnly = false; $('#banner').className = ''; renderAll(); }]]);
    } else if (pages && pages !== S.pdf.numPages) {
      banner('warn', `页数不一致：审稿记录为 ${pages} 页，当前 PDF 为 ${S.pdf.numPages} 页。`);
    } else if (want && !S.pdfSha) {
      banner('warn', '浏览器无法计算 PDF 哈希（非安全上下文），未核对 PDF 身份。');
    }
    renderOverlays();
  }

  // ------------------------------------------------------------------ pages
  let observer = null;
  function computeScale() {
    if (S.zoom !== 'fit') return parseFloat(S.zoom);
    const w = Math.max(...S.base.map((b) => b.vp.width)); const avail = $('#viewer').clientWidth - 110;
    return Math.max(0.3, Math.min(3, avail / w));
  }
  function buildPages() {
    const box = $('#pages'); box.innerHTML = ''; S.scale = computeScale();
    if (observer) observer.disconnect();
    observer = new IntersectionObserver((ents) => ents.forEach((e) => { if (e.isIntersecting) renderPage(+e.target.dataset.page); }), { root: $('#viewer'), rootMargin: '600px 0px' });
    S.base.forEach((b, i) => {
      const n = i + 1; const div = document.createElement('div'); div.className = 'page'; div.dataset.page = n;
      div.innerHTML = `<span class="pnum">${n}</span><canvas></canvas><div class="textLayer"></div><div class="overlay"></div>`;
      sizePage(div, b.vp); box.appendChild(div); observer.observe(div); attachPageEvents(div, n);
    });
    renderOverlays();
  }
  function sizePage(div, vp) { div.style.width = vp.width * S.scale + 'px'; div.style.height = vp.height * S.scale + 'px'; div.dataset.rendered = ''; }
  function setZoom(z) {
    const keep = S.current; S.zoom = z; $('#zoomSel').value = ['fit', '0.75', '1', '1.25', '1.5', '2'].includes(String(z)) ? String(z) : $('#zoomSel').value;
    S.scale = computeScale(); $$('.page').forEach((d) => { sizePage(d, S.base[d.dataset.page - 1].vp); d.querySelector('.textLayer').innerHTML = ''; });
    scrollToPage(keep, 0, false); $$('.page').forEach((d) => { const r = d.getBoundingClientRect(); if (r.bottom > -600 && r.top < innerHeight + 600) renderPage(+d.dataset.page); });
  }
  async function renderPage(n) {
    const div = $(`.page[data-page="${n}"]`); if (!div || div.dataset.rendered === String(S.scale) || S.pending[n]) return;
    S.pending[n] = true;
    try {
      const { page } = S.base[n - 1]; const vp = page.getViewport({ scale: S.scale }); const dpr = window.devicePixelRatio || 1;
      const canvas = div.querySelector('canvas'); canvas.width = Math.floor(vp.width * dpr); canvas.height = Math.floor(vp.height * dpr);
      await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
      const tl = div.querySelector('.textLayer'); tl.innerHTML = ''; tl.style.setProperty('--scale-factor', S.scale);
      const textDivs = [];
      const task = pdfjsLib.renderTextLayer({ textContentSource: await page.getTextContent(), container: tl, viewport: vp, textDivs });
      await task.promise; tl._divs = textDivs;
      div.dataset.rendered = String(S.scale);
      if (S.searchHl && S.searchHl.page === n) highlightQuote(n, S.searchHl.quote, false);
      if (S.hl && S.hl.some((h) => h.page === n) || (S.selected && byId(S.selected)?.page === n)) markQuotes(n);
    } catch (e) { console.error('render page', n, e); }
    finally { delete S.pending[n]; }
  }
  function scrollToPage(n, yFrac = 0, smooth = true) {
    const div = $(`.page[data-page="${n}"]`); if (!div) return;
    const v = $('#viewer'); const top = div.offsetTop + yFrac * div.offsetHeight - 70;
    v.scrollTo({ top: Math.max(0, top), behavior: smooth ? 'smooth' : 'auto' });
  }
  function trackCurrent() {
    const v = $('#viewer'); const mid = v.scrollTop + 120; let cur = 1;
    $$('.page').forEach((d) => { if (d.offsetTop <= mid) cur = +d.dataset.page; });
    if (cur !== S.current) { S.current = cur; $('#pageInput').value = cur; if ($('#fScope').value === 'current') renderList(); }
  }

  // ------------------------------------------------------------------ overlays & drawing
  function renderOverlays() {
    $$('.page').forEach((d) => { d.querySelector('.overlay').innerHTML = ''; d.querySelector('.pbadge')?.remove(); });
    if (!S.review) return;
    const vis = new Set(filtered().map((c) => c.id));
    comments().forEach((c) => {
      if (!vis.has(c.id) || c.scope !== 'region' || !Array.isArray(c.rect)) return;
      const div = $(`.page[data-page="${c.page}"]`); if (!div) return;
      const [x, y, w, h] = c.rect; const r = document.createElement('div');
      r.className = 'region' + (S.selected === c.id ? ' sel' : '') + (S.readOnly ? '' : ' editable');
      r.dataset.id = c.id; r.style.cssText = `left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${h * 100}%;--c:${COLOR[colorKey(c)]}`;
      r.innerHTML = `<span class="tag">${esc(c.id)}<span class="t">${esc(c.title)}</span></span><span class="h"></span>`;
      r.title = `${c.id} ${c.title}`; div.querySelector('.overlay').appendChild(r);
      r.addEventListener('mousedown', (ev) => regionMouseDown(ev, c, r, div));
    });
    const counts = {};
    comments().forEach((c) => { if (vis.has(c.id) && c.scope === 'page') counts[c.page] = (counts[c.page] || 0) + 1; });
    Object.entries(counts).forEach(([p, n]) => {
      const div = $(`.page[data-page="${p}"]`); if (!div) return;
      const b = document.createElement('span'); b.className = 'pbadge'; b.textContent = `${n} 条`; b.title = '本页的页级意见';
      b.onclick = () => { $('#fScope').value = 'current'; S.current = +p; renderList(); }; div.appendChild(b);
    });
  }
  function regionMouseDown(ev, c, el, pageDiv) {
    ev.stopPropagation();
    if (S.selected !== c.id) { select(c.id, { scrollPdf: false }); return; }
    if (S.readOnly) return;
    const resize = ev.target.classList.contains('h'); const pr = pageDiv.getBoundingClientRect();
    const start = [...c.rect]; const sx = ev.clientX, sy = ev.clientY; let moved = false;
    const mm = (e) => {
      const dx = (e.clientX - sx) / pr.width, dy = (e.clientY - sy) / pr.height; if (Math.abs(dx) + Math.abs(dy) > 0.002) moved = true;
      let [x, y, w, h] = start;
      if (resize) { w = Math.max(0.01, Math.min(1 - x, w + dx)); h = Math.max(0.01, Math.min(1 - y, h + dy)); }
      else { x = Math.max(0, Math.min(1 - w, x + dx)); y = Math.max(0, Math.min(1 - h, y + dy)); }
      el.style.left = x * 100 + '%'; el.style.top = y * 100 + '%'; el.style.width = w * 100 + '%'; el.style.height = h * 100 + '%'; el._rect = [x, y, w, h];
    };
    const mu = () => {
      removeEventListener('mousemove', mm); removeEventListener('mouseup', mu);
      if (moved && el._rect) { pushUndo(); c.rect = el._rect.map((v) => Math.round(v * 10000) / 10000); setDirty(true); renderList(); renderOverlays(); }
    };
    addEventListener('mousemove', mm); addEventListener('mouseup', mu);
  }
  function attachPageEvents(div, n) {
    div.addEventListener('mousedown', (ev) => {
      if (!S.regionMode || ev.button !== 0) return; ev.preventDefault();
      const pr = div.getBoundingClientRect(); const x0 = (ev.clientX - pr.left) / pr.width, y0 = (ev.clientY - pr.top) / pr.height;
      const d = document.createElement('div'); d.className = 'draft'; div.querySelector('.overlay').appendChild(d);
      let rect = null;
      const mm = (e) => {
        const x1 = Math.max(0, Math.min(1, (e.clientX - pr.left) / pr.width)), y1 = Math.max(0, Math.min(1, (e.clientY - pr.top) / pr.height));
        rect = [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
        d.style.cssText = `left:${rect[0] * 100}%;top:${rect[1] * 100}%;width:${rect[2] * 100}%;height:${rect[3] * 100}%`;
      };
      const mu = () => {
        removeEventListener('mousemove', mm); removeEventListener('mouseup', mu); d.remove();
        if (rect && rect[2] > 0.01 && rect[3] > 0.006) { setRegionMode(false); openEditor(null, { scope: 'region', page: n, rect: rect.map((v) => Math.round(v * 10000) / 10000) }); }
      };
      addEventListener('mousemove', mm); addEventListener('mouseup', mu);
    });
  }
  function setRegionMode(on) {
    if (on && !guardEdit()) return;
    S.regionMode = on; $('#btnRegion').classList.toggle('on', on); $$('.page').forEach((d) => d.classList.toggle('drawing', on));
    if (on) toast('在页面上拖动鼠标框选区域；Esc 取消');
  }

  // ------------------------------------------------------------------ quote highlight in text layer
  function clearHl() { $$('.textLayer span.hl').forEach((s) => s.classList.remove('hl')); $$('.evbox,.pflag').forEach((b) => b.remove()); }
  // Evidence marks for the selected comment: every manuscript quote is highlighted and boxed (one box per text line)
  function quoteSpans(n, quote) {
    const div = $(`.page[data-page="${n}"]`); const divs = div?.querySelector('.textLayer')._divs; if (!divs) return null;
    let loose = ''; const owner = [];
    divs.forEach((d, i) => { for (const ch of norm(d.textContent)) if (looseChar(ch)) { loose += ch.toLowerCase(); owner.push(i); } });
    const lq = Array.from(norm(quote)).filter(looseChar).join('').toLowerCase(); if (!lq) return null;
    let at = loose.indexOf(lq), len = lq.length;
    if (at < 0 && lq.length > 40) { at = loose.indexOf(lq.slice(0, 40)); len = 40; }
    if (at < 0 && lq.length > 40) { at = loose.indexOf(lq.slice(-40)); len = 40; }
    if (at < 0) return null;
    return [...new Set(owner.slice(at, at + len))].map((i) => divs[i]);
  }
  function markQuotes(n) {
    const div = $(`.page[data-page="${n}"]`); if (!div) return;
    div.querySelectorAll('.evbox,.pflag').forEach((b) => b.remove());
    const list = (S.hl || []).filter((h) => h.page === n); const pr = div.getBoundingClientRect();
    list.forEach((h, k) => {
      const spans = quoteSpans(n, h.quote); h.found = !!spans; if (!spans) return;
      spans.forEach((sp) => sp.classList.add('hl'));
      const lines = [];
      spans.forEach((sp) => { const r = sp.getBoundingClientRect(); if (!r.width) return;
        const L = lines.find((l) => Math.abs(l.top - r.top) < r.height * 0.6 && r.left < l.right + 40 && r.right > l.left - 40);
        if (L) { L.left = Math.min(L.left, r.left); L.right = Math.max(L.right, r.right); L.top = Math.min(L.top, r.top); L.bottom = Math.max(L.bottom, r.bottom); }
        else lines.push({ left: r.left, right: r.right, top: r.top, bottom: r.bottom }); });
      lines.sort((a, b) => a.top - b.top || a.left - b.left).forEach((l, j) => {
        const b = document.createElement('div'); b.className = 'evbox'; b.dataset.q = h.idx;
        b.style.cssText = `left:${(l.left - pr.left - 3) / pr.width * 100}%;top:${(l.top - pr.top - 2) / pr.height * 100}%;width:${(l.right - l.left + 6) / pr.width * 100}%;height:${(l.bottom - l.top + 4) / pr.height * 100}%;--c:${h.color}`;
        if (j === 0) b.innerHTML = `<span class="tag">${esc(h.id)} · 引文 ${h.idx + 1}</span>`;
        div.appendChild(b);
      });
    });
    const c = S.selected && byId(S.selected);
    if (c && c.scope === 'page' && c.page === n) {
      const f = document.createElement('div'); f.className = 'pflag'; f.style.setProperty('--c', COLOR[colorKey(c)]);
      f.innerHTML = `<span>${esc(c.id)} · 页级意见</span>`; f.title = c.title; div.appendChild(f);
    }
  }
  async function showEvidence(c, focus = null) {
    const ms = manuscriptId(); const color = COLOR[colorKey(c)];
    S.hl = (c.evidence || []).map((e, idx) => ({ ...e, idx, id: c.id, color, page: e.page || (evState(e).pages || [])[0] }))
      .filter((e) => e.source_id === ms && e.page);
    S.searchHl = null; clearHl();
    const pages = [...new Set(S.hl.map((h) => h.page).concat(c.scope !== 'global' ? [c.page] : []))];
    for (const n of pages) { await renderPage(n); markQuotes(n); }
    return S.hl;
  }
  function scrollToMark(h) {
    const b = $(`.evbox[data-q="${h.idx}"]`) ; const v = $('#viewer');
    if (b) { const r = b.getBoundingClientRect(), vr = v.getBoundingClientRect(); v.scrollBy({ top: r.top - vr.top - vr.height / 3, behavior: 'smooth' }); b.classList.remove('flash'); void b.offsetWidth; b.classList.add('flash'); return true; }
    return false;
  }
  function highlightQuote(n, quote, scroll = true) {
    const div = $(`.page[data-page="${n}"]`); const divs = div?.querySelector('.textLayer')._divs; if (!divs) return false;
    let loose = ''; const owner = [];
    divs.forEach((d, i) => { for (const ch of norm(d.textContent)) if (looseChar(ch)) { loose += ch.toLowerCase(); owner.push(i); } });
    const lq = Array.from(norm(quote)).filter(looseChar).join('').toLowerCase(); if (!lq) return false;
    let at = loose.indexOf(lq), len = lq.length;
    if (at < 0 && lq.length > 40) { at = loose.indexOf(lq.slice(0, 40)); len = 40; }
    if (at < 0 && lq.length > 40) { const tail = lq.slice(-40); at = loose.indexOf(tail); len = 40; }
    if (at < 0) return false;
    clearHl(); const set = new Set(owner.slice(at, at + len)); set.forEach((i) => divs[i].classList.add('hl'));
    if (scroll) { const first = divs[Math.min(...set)]; const v = $('#viewer'); const r = first.getBoundingClientRect(), vr = v.getBoundingClientRect(); v.scrollBy({ top: r.top - vr.top - vr.height / 3, behavior: 'smooth' }); }
    return true;
  }
  function goToEvidence(ev) {
    const st = evState(ev); const page = ev.page || st.pages[0];
    const src = (S.review.sources || []).find((s) => s.id === ev.source_id);
    if (src && src.role !== 'manuscript') { toast(`该引文来自前作 ${src.title || src.id}，不在本 PDF 中`); return; }
    if (!page) { toast('未找到该引文所在页'); return; }
    scrollToPage(page, 0, false);
    const h = (S.hl || []).find((x) => x.quote === ev.quote && x.page === page);
    renderPage(page).then(() => { if (h) { markQuotes(page); if (scrollToMark(h)) return; }
      S.searchHl = { page, quote: ev.quote }; if (!highlightQuote(page, ev.quote)) toast('文字层中未能定位该引文（可能在图表中）'); });
  }

  // ------------------------------------------------------------------ filters & list
  const GROUPS = [['major', '主要问题'], ['minor', '次要问题'], ['question', '待澄清的问题'], ['suggestion', '改进建议'], ['strength', '优点']];
  function groupOf(c) { const i = GROUPS.findIndex(([k]) => QUICK.find((x) => x[0] === k)[3](c)); return i < 0 ? GROUPS.length : i; }
  function filtered() {
    const q = norm($('#q').value).toLowerCase(), sc = $('#fScope').value, st = $('#fStatus').value, sv = $('#fSev').value, dm = $('#fDim').value;
    const qf = S.quick ? QUICK.find((x) => x[0] === S.quick)[3] : null;
    return comments().filter((c) => {
      if (qf && !qf(c)) return false;
      if (sc === 'current') { if (!(c.page === S.current && c.scope !== 'global')) return false; } else if (sc && c.scope !== sc) return false;
      if (st && c.status !== st) return false; if (sv && c.severity !== sv) return false; if (dm && c.dimension !== dm) return false;
      if (q) { const hay = norm([c.id, c.title, c.body, c.countercheck, c.request, ...(c.evidence || []).map((e) => e.quote)].join(' ')).toLowerCase(); if (!hay.includes(q)) return false; }
      return true;
    }).sort((a, b) => {
      const ga = groupOf(a), gb = groupOf(b); if (ga !== gb) return ga - gb;
      const ka = a.scope === 'global' ? -1 : a.page, kb = b.scope === 'global' ? -1 : b.page;
      if (ka !== kb) return ka - kb; const ya = a.rect ? a.rect[1] : 0, yb = b.rect ? b.rect[1] : 0; return ya - yb || a.id.localeCompare(b.id);
    });
  }
  function renderList() {
    const list = $('#list'); if (!S.review) { list.innerHTML = '<p class="panel hint">尚未加载审稿 JSON。</p>'; return; }
    const items = filtered(); const all = comments();
    const n = (f) => all.filter(f).length;
    $('#counts').textContent = `显示 ${items.length} / ${all.length} 条` + (S.quick ? ' · 已按“' + QUICK.find((x) => x[0] === S.quick)[1] + '”筛选（点击上方标签取消）' : '');
    // groups start collapsed; searching/filtering or selecting a comment opens the relevant groups
    S.collapsed = S.collapsed || Object.fromEntries(GROUPS.map(([k]) => [k, true]).concat([['other', true]]));
    const filtering = !!(S.quick || $('#q').value.trim() || ['#fScope', '#fStatus', '#fSev', '#fDim'].some((x) => $(x).value));
    const selC = S.selected && byId(S.selected); if (selC) S.collapsed[(GROUPS[groupOf(selC)] || ['other'])[0]] = false;
    const parts = []; let g = -1;
    items.forEach((c) => {
      const gi = groupOf(c);
      if (gi !== g) {
        if (g >= 0) parts.push('</div>');
        g = gi; const key = (GROUPS[gi] || ['other'])[0], label = (GROUPS[gi] || ['other', '其他'])[1];
        const color = (QUICK.find((x) => x[0] === key) || [0, 0, 'var(--muted)'])[2];
        const cnt = items.filter((x) => groupOf(x) === gi).length; const shut = !filtering && !!S.collapsed[key];
        parts.push(`<div class="group-sec${shut ? ' shut' : ''}" data-g="${key}" style="--c:${color}"><button class="ghead" data-g="${key}" title="折叠/展开">${ICON.down}<i></i><span>${label}</span><b>${cnt}</b></button>`);
      }
      parts.push(cardHtml(c));
    });
    if (g >= 0) parts.push('</div>');
    list.innerHTML = parts.join('') || '<p class="panel hint">没有符合筛选条件的意见。</p>';
    $$('.ghead', list).forEach((h) => (h.onclick = () => { S.collapsed[h.dataset.g] = !S.collapsed[h.dataset.g]; h.parentElement.classList.toggle('shut'); }));
    $$('.card', list).forEach((el) => {
      el.addEventListener('click', (e) => { if (e.target.closest('button')) return; select(el.dataset.id); });
      el.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => cardAction(el.dataset.id, b.dataset.act, b.dataset.i)));
    });
    const sel = $('.card.sel', list); if (sel && !S.reading) sel.scrollIntoView({ block: 'nearest' });
    const nf = ['#fScope', '#fStatus', '#fSev', '#fDim'].filter((x) => $(x).value).length;
    $('#fCount').hidden = !nf; $('#fCount').textContent = nf;
    renderDetail();
  }
  function renderDetail() {
    const box = $('#detail'); const tab = $('#tab-comments');
    const c = S.reading && S.selected ? byId(S.selected) : null;
    if (!c) { S.reading = false; box.hidden = true; box.innerHTML = ''; tab.classList.remove('reading'); return; }
    const items = filtered(); const i = items.findIndex((x) => x.id === c.id);
    box.innerHTML = `<div class="dbar"><button id="btnBack" title="返回列表 (Esc)">${ICON.back}全部意见</button><span class="grow"></span>
      <span class="pos">${i >= 0 ? `${i + 1} / ${items.length}` : '不在当前筛选中'}</span>
      <button class="icon" id="btnPrev" title="上一条 (K)" ${i <= 0 ? 'disabled' : ''}>${ICON.up}</button>
      <button class="icon" id="btnNext" title="下一条 (J)" ${i < 0 || i >= items.length - 1 ? 'disabled' : ''}>${ICON.down}</button></div>` + cardHtml(c, true);
    box.hidden = false; tab.classList.add('reading');
    $('#btnBack').onclick = closeDetail;
    $('#btnPrev').onclick = () => step(-1); $('#btnNext').onclick = () => step(1);
    box.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => cardAction(c.id, b.dataset.act, b.dataset.i)));
    if (S._detailFor !== c.id) { tab.scrollTop = 0; S._detailFor = c.id; }
  }
  function closeDetail() {
    S.reading = false; S._detailFor = null; renderDetail();
    const sel = $('#list .card.sel'); if (sel) sel.scrollIntoView({ block: 'center' });
  }
  function step(d) {
    const items = filtered(); if (!items.length) return; const i = items.findIndex((c) => c.id === S.selected);
    const j = i < 0 ? 0 : Math.max(0, Math.min(items.length - 1, i + d)); select(items[j].id);
  }
  function evHtml(e, i) {
    const src = (S.review.sources || []).find((s) => s.id === e.source_id); const st = evState(e);
    const mk = st.state === 'found' ? `<span class="mk ok" title="已在快照中找到（p.${st.pages.join(',')}）">${ICON.check}</span>`
      : st.state === 'wrongpage' ? `<span class="mk bad" title="引文在 p.${st.pages.join(',')}，与标注页不符">${ICON.x}</span>`
        : st.state === 'absent' ? `<span class="mk bad" title="快照中找不到该引文">${ICON.x}</span>` : `<span class="mk unk" title="未加载快照，无法核对">${ICON.dot}</span>`;
    const who = src ? (src.role === 'manuscript' ? '本文' : (src.title || src.id) + (src.version ? ' ' + src.version : '')) : e.source_id;
    const extra = st.state === 'wrongpage' ? ` · 实际在 p.${st.pages.join(',')}` : st.state === 'absent' ? ' · 快照中找不到' : '';
    return `<div class="ev">${mk}<div class="q">“${esc(e.quote)}”<span class="src">${esc(who)}${e.page ? ' · p.' + e.page : ''}${extra}</span></div>
      ${src && src.role === 'manuscript' ? `<button data-act="ev" data-i="${i}" title="在 PDF 中定位并高亮">${ICON.target}定位</button>` : ''}</div>`;
  }
  // Light typesetting for review prose: paragraphs, (1)(2)… enumerations as lists, long paragraphs split by sentence.
  function rich(text) {
    const out = [];
    const sentences = (t) => t.match(/[^。！？]+[。！？]?[”’」）)]*/g) || [t];
    const para = (t) => {
      t = t.trim(); if (!t) return;
      if (t.length <= 240) { out.push(`<p>${esc(t)}</p>`); return; }
      let buf = '';
      const start = out.length;
      let lastS = '';
      sentences(t).forEach((x) => {
        if (buf && (buf + x).length > 200) {
          // a short lead-in sentence ("Figure 5/6 也不能检验…。") belongs to the paragraph it introduces
          let carry = ''; if (lastS && lastS.length < 45 && buf.length > lastS.length) { carry = lastS; buf = buf.slice(0, -lastS.length); }
          out.push(`<p>${esc(buf.trim())}</p>`); buf = carry;
        }
        buf += x; lastS = x;
      });
      if (buf.trim()) { if (out.length > start && buf.trim().length < 40) out[out.length - 1] = out[out.length - 1].replace(/<\/p>$/, esc(buf.trim()) + '</p>'); else out.push(`<p>${esc(buf.trim())}</p>`); }
    };
    String(text || '').split(/\n\s*\n|\n(?=[（(]\d+[)）])/).forEach((block) => {
      const re = /[（(](\d{1,2})[)）]\s*/g; const marks = [...block.matchAll(re)];
      const seq = marks.length >= 2 && marks.every((m, i) => +m[1] === i + 1);
      if (!seq) { block.split(/\n/).forEach(para); return; }
      para(block.slice(0, marks[0].index).replace(/[：:]\s*$/, '：'));
      const items = marks.map((m, i) => block.slice(m.index + m[0].length, i + 1 < marks.length ? marks[i + 1].index : block.length).trim());
      let tail = '';
      const last = items[items.length - 1]; const cut = last.search(/[。！？]/);
      if (cut >= 0 && cut < last.length - 1) { tail = last.slice(cut + 1); items[items.length - 1] = last.slice(0, cut + 1); }
      out.push('<ol>' + items.map((x) => `<li>${esc(x.replace(/[；;，,]\s*$/, ''))}</li>`).join('') + '</ol>');
      para(tail);
    });
    return out.join('');
  }
  function cardHtml(c, full = false) {
    const k = colorKey(c); const loc = c.scope === 'global' ? '全文' : `p.${c.page}${c.scope === 'region' ? ' · 区域' : ''}`;
    const sev = c.status !== 'strength' && c.severity ? `<span class="pill" style="--c:${c.severity === 'major' ? COLOR.major : 'var(--muted)'}">${L.sev[c.severity]}</span>` : '';
    const res = c.resolution ? `<span class="pill neutral">${{ resolved: '已解决', partial: '部分解决', unresolved: '未解决' }[c.resolution]}</span>` : '';
    const n = (c.evidence || []).length;
    const ro = S.readOnly ? 'disabled' : '';
    return `<div class="card${S.selected === c.id ? ' sel' : ''}${full ? ' full' : ''}" data-id="${esc(c.id)}" style="--c:${COLOR[k]}">
      <div class="card-head"><span class="cid">${esc(c.id)}</span><span class="pill">${L.status[c.status] || esc(c.status)}</span>${sev}${res}<span class="loc">${ICON.pin}${loc}</span></div>
      <h4>${esc(c.title)}</h4>
      <div class="body${full ? ' rich' : ''}">${full ? rich(c.body) : esc(c.body)}</div>
      <div class="foot"><span>${L.dim[c.dimension] || esc(c.dimension)}</span>${c.confidence ? `<span>置信度 ${L.conf[c.confidence]}</span>` : ''}${n ? `<span>${n} 条引文</span>` : ''}</div>
      ${full ? `<div class="more">
        ${n ? '<div class="sec">证据</div>' + (c.evidence || []).map(evHtml).join('') : ''}
        ${c.comparison ? `<div class="kv rich"><b>机制比较</b>${rich(c.comparison)}</div>` : ''}
        ${c.countercheck ? `<div class="kv rich"><b>反证检查</b>${rich(c.countercheck)}</div>` : ''}
        ${c.request ? `<div class="kv rich"><b>建议作者</b>${rich(c.request)}</div>` : ''}
        <div class="actions">${c.scope !== 'global' ? `<button data-act="go">${ICON.target}定位</button>` : ''}
          <button data-act="edit" ${ro}>${ICON.edit}编辑</button><button data-act="dup" ${ro}>${ICON.copy}复制</button>
          <button data-act="del" class="danger" ${ro}>${ICON.trash}删除</button></div>
      </div>` : ''}</div>`;
  }
  function cardAction(id, act, i) {
    const c = byId(id); if (!c) return;
    if (act === 'go') navigate(c); else if (act === 'edit') openEditor(c); else if (act === 'ev') goToEvidence(c.evidence[+i]);
    else if (act === 'del') { if (!guardEdit()) return; pushUndo(); S.review.comments = comments().filter((x) => x.id !== id); unlinkDims(id); S.selected = null; setDirty(true); renderAll(); toast(`已删除 ${id}（可撤销）`); }
    else if (act === 'dup') { if (!guardEdit()) return; pushUndo(); const d = JSON.parse(JSON.stringify(c)); d.id = nextId(d.status); d.title += '（副本）'; S.review.comments.push(d); S.selected = d.id; setDirty(true); renderAll(); }
  }
  function select(id, { scrollPdf = true } = {}) {
    S.selected = id; S.reading = true; switchTab('comments'); renderList(); renderOverlays(); const c = byId(id);
    if (c && scrollPdf) navigate(c);
  }
  function navigate(c) {
    if (c.scope === 'region') {
      scrollToPage(c.page, c.rect ? c.rect[1] : 0);
      setTimeout(() => { const r = $(`.region[data-id="${CSS.escape(c.id)}"]`); if (r) { r.classList.remove('flash'); void r.offsetWidth; r.classList.add('flash'); } }, 350);
      showEvidence(c); return;
    }
    // page-level / global: go to the first quote (on the comment's page if possible), otherwise to the page top
    const pre = (c.evidence || []).find((e) => e.source_id === manuscriptId());
    const target = c.scope === 'page' ? c.page : (pre && (pre.page || (evState(pre).pages || [])[0]));
    if (target) scrollToPage(target, 0, false);
    showEvidence(c).then((hl) => {
      const first = hl.find((h) => h.found && (c.scope !== 'page' || h.page === c.page)) || hl.find((h) => h.found);
      if (first && first.page !== target) scrollToPage(first.page, 0, false);
      if (first) setTimeout(() => scrollToMark(first), first.page !== target ? 250 : 30);
      else if (c.scope === 'global') toast('全文意见：没有可在 PDF 中定位的引文');
    });
  }


  // ------------------------------------------------------------------ editing
  function guardEdit() { if (!S.review) { toast('请先打开审稿 JSON 或新建'); return false; } if (S.readOnly) { toast('当前为只读（PDF 与审稿记录不一致）'); return false; } return true; }
  function nextId(status) {
    const p = PREFIX[status] || 'N'; let max = 0;
    comments().forEach((c) => { const m = new RegExp('^' + p + '(\\d+)$').exec(c.id); if (m) max = Math.max(max, +m[1]); });
    return p + String(max + 1).padStart(2, '0');
  }
  function pushUndo() { S.undo.push(JSON.stringify({ comments: S.review.comments, dimensions: S.review.dimensions, overall: S.review.overall })); if (S.undo.length > 60) S.undo.shift(); updateUndo(); }
  function doUndo() { const s = S.undo.pop(); if (!s) return; Object.assign(S.review, JSON.parse(s)); updateUndo(); setDirty(true); renderAll(); toast('已撤销'); }
  function updateUndo() { $('#btnUndo').disabled = !S.undo.length; }
  function unlinkDims(id) { Object.values(S.review.dimensions || {}).forEach((d) => { if (d.comment_ids) d.comment_ids = d.comment_ids.filter((x) => x !== id); }); }
  function ensureReview() {
    if (S.review) return;
    loadReview({ schema_version: 2, language: 'zh-CN', mode: 'full', paper: { title: '未命名论文', pdf_sha256: S.pdfSha, pages: S.pdf?.numPages },
      cutoff: null, policy: { blind: true, read_scope: '', not_done: [] }, summary: {}, dimensions: {}, sources: [], comments: [] }, { fromDisk: false });
  }
  function opt(map, cur) { return Object.entries(map).map(([k, v]) => `<option value="${k}"${k === cur ? ' selected' : ''}>${v}</option>`).join(''); }
  function openEditor(c, preset = {}) {
    if (!S.pdf && !S.review) { toast('请先打开 PDF'); return; }
    ensureReview(); if (!guardEdit()) return;
    const isNew = !c; const d = c ? JSON.parse(JSON.stringify(c)) : { status: 'confirmed', severity: 'minor', confidence: 'medium', dimension: 'experiments', scope: 'page', page: S.current, evidence: [], ...preset };
    const srcOpts = (S.review.sources || []).map((s) => s.id);
    const f = $('#editForm');
    f.innerHTML = `<h3>${isNew ? '新建意见' : '编辑 ' + esc(d.id)}</h3>
      <div class="f"><label>标题</label><input name="title" value="${esc(d.title)}" required placeholder="一句话说清问题"></div>
      <div class="f"><label>正文</label><textarea name="body" rows="5" required placeholder="现象（锚点）→ 为什么重要 → 结论范围">${esc(d.body)}</textarea></div>
      <div class="f half"><label>状态</label><select name="status">${opt(L.status, d.status)}</select></div>
      <div class="f half"><label>维度</label><select name="dimension">${opt(L.dim, d.dimension)}</select></div>
      <div class="f half"><label>严重度</label><select name="severity">${opt(L.sev, d.severity)}</select></div>
      <div class="f half"><label>置信度</label><select name="confidence">${opt({ high: '高', medium: '中', low: '低' }, d.confidence)}</select></div>
      <div class="f"><label>位置</label><div class="row"><select name="scope" style="max-width:120px">${opt(L.scope, d.scope)}</select><input name="page" type="number" min="1" max="${S.pdf?.numPages || 9999}" value="${d.page || S.current}" style="max-width:90px" title="页码">
        <input name="rect" value="${d.rect ? d.rect.join(', ') : ''}" placeholder="区域 x, y, w, h（0–1，框选时自动填写）"></div></div>
      <div class="f"><label>证据引文</label><div id="evRows">${(d.evidence || []).map((e) => evRow(e, srcOpts)).join('')}<button type="button" id="addEv">＋ 添加引文</button>
        <div class="hint" style="margin-top:6px">引文须逐字来自快照；也可在 PDF 上选中文字后点“添加为当前意见的证据”。</div></div></div>
      <div class="f"><label>反证检查</label><textarea name="countercheck" rows="3" placeholder="查过哪里、作者最有力的辩护、为何仍成立">${esc(d.countercheck)}</textarea></div>
      <div class="f"><label>建议作者</label><textarea name="request" rows="2" placeholder="具体可执行的请求">${esc(d.request)}</textarea></div>
      <div class="f half"><label>机制比较（仅创新性意见）</label><textarea name="comparison" rows="2">${esc(d.comparison)}</textarea></div>
      <div class="f half"><label>前作编号（逗号分隔）</label><input name="comparator_ids" value="${esc((d.comparator_ids || []).join(', '))}"></div>
      <div class="foot"><span class="hint" id="edErr" style="flex:1;color:var(--bad)"></span><button value="cancel" formnovalidate>取消</button><button class="primary" value="ok" id="edOk">保存</button></div>`;
    $('#addEv').onclick = () => { $('#addEv').insertAdjacentHTML('beforebegin', evRow({ source_id: manuscriptId(), quote: '', page: S.current }, srcOpts)); bindEvRows(); };
    bindEvRows();
    $('#edOk').onclick = (ev) => {
      ev.preventDefault(); const fd = new FormData(f); const out = { ...d };
      ['title', 'body', 'status', 'severity', 'confidence', 'dimension', 'scope', 'countercheck', 'request', 'comparison'].forEach((k) => (out[k] = String(fd.get(k) || '').trim()));
      out.page = out.scope === 'global' ? undefined : parseInt(fd.get('page'), 10);
      const rect = String(fd.get('rect') || '').split(/[,\s]+/).filter(Boolean).map(Number);
      out.rect = out.scope === 'region' ? rect : undefined;
      out.comparator_ids = String(fd.get('comparator_ids') || '').split(/[,\s]+/).filter(Boolean);
      out.evidence = $$('.evrow', f).map((r) => ({ source_id: r.querySelector('select').value, quote: r.querySelector('.q').value.trim(), page: parseInt(r.querySelector('.p').value, 10) || undefined })).filter((e) => e.quote);
      if (out.status === 'strength') delete out.severity;
      ['countercheck', 'request', 'comparison'].forEach((k) => { if (!out[k]) delete out[k]; }); if (!out.comparator_ids.length) delete out.comparator_ids;
      if (out.page === undefined) delete out.page; if (out.rect === undefined) delete out.rect;
      const err = validateComment(out); if (err) { $('#edErr').textContent = err; return; }
      pushUndo();
      if (isNew) { out.id = nextId(out.status); S.review.comments.push(out); linkDim(out); }
      else { const i = comments().findIndex((x) => x.id === c.id); if (c.dimension !== out.dimension) { unlinkDims(c.id); linkDim(out); } S.review.comments[i] = out; }
      S.selected = out.id; setDirty(true); $('#editor').close(); renderAll();
    };
    $('#editor').showModal();
  }
  function linkDim(c) {
    if (c.status === 'strength') return; const d = (S.review.dimensions[c.dimension] = S.review.dimensions[c.dimension] || { status: 'issues', comment_ids: [], note: '' });
    if (d.status !== 'issues') d.status = 'issues'; d.comment_ids = [...new Set([...(d.comment_ids || []), c.id])];
  }
  function evRow(e, ids) {
    const options = ids.map((id) => `<option${id === e.source_id ? ' selected' : ''}>${esc(id)}</option>`).join('') || `<option>${esc(e.source_id || 'paper')}</option>`;
    return `<div class="evrow"><select>${options}</select><input class="q" value="${esc(e.quote)}" placeholder="逐字引文"><input class="p" type="number" min="1" value="${e.page || ''}" placeholder="页"><button type="button" class="danger rm">×</button><span class="hint st" style="grid-column:2/-1"></span></div>`;
  }
  function bindEvRows() {
    $$('#evRows .evrow').forEach((r) => {
      r.querySelector('.rm').onclick = () => r.remove();
      const upd = () => { const st = evState({ source_id: r.querySelector('select').value, quote: r.querySelector('.q').value, page: parseInt(r.querySelector('.p').value, 10) || undefined });
        r.querySelector('.st').textContent = { found: '✓ 快照中存在' + (st.pages.length ? '（p.' + st.pages.join(',') + '）' : ''), wrongpage: '✗ 页码不符，应为 p.' + st.pages.join(','), absent: '✗ 快照中找不到', unknown: '未加载快照，无法核对' }[st.state]; };
      r.querySelectorAll('input,select').forEach((x) => x.addEventListener('input', upd)); upd();
    });
  }
  function validateComment(c) {
    if (!c.title || !c.body) return '标题和正文必填';
    if (c.scope !== 'global' && !(c.page >= 1 && c.page <= (S.pdf?.numPages || 1e9))) return '页码无效';
    if (c.scope === 'region') { const r = c.rect; if (!(r && r.length === 4 && r.every((v) => v >= 0 && v <= 1) && r[2] > 0 && r[3] > 0 && r[0] + r[2] <= 1.000001 && r[1] + r[3] <= 1.000001)) return '区域坐标无效（[x,y,w,h]，0–1）'; }
    if (c.status === 'confirmed') {
      if (!c.evidence.some((e) => (S.review.sources || []).find((s) => s.id === e.source_id)?.role === 'manuscript' || e.source_id === manuscriptId())) return '确认问题需要至少一条本文引文（否则请改为“澄清问题”或“证据局限”）';
      if (!c.countercheck) return '确认问题需要写反证检查';
    }
    if (c.status === 'confirmed' && c.dimension === 'novelty' && (!c.comparator_ids?.length || !c.comparison)) return '创新性确认意见需要前作编号与机制比较';
    const bad = c.evidence.map(evState).find((s) => s.state === 'absent' || s.state === 'wrongpage');
    if (bad) return bad.state === 'absent' ? '有引文在快照中找不到，请逐字复制' : '有引文页码与快照不符';
    return '';
  }

  // ------------------------------------------------------------------ text selection popup
  function onSelectionEnd(e) {
    const pop = $('#selPop'); const sel = getSelection(); const txt = sel && String(sel).trim();
    const node = sel && sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    const pg = node && node.closest && node.closest('.page');
    if (!txt || !pg || txt.length < 3 || S.regionMode) { pop.classList.remove('show'); return; }
    pop._sel = { quote: norm(txt), page: +pg.dataset.page };
    pop.style.left = Math.min(innerWidth - 330, e.clientX) + 'px'; pop.style.top = e.clientY + 12 + 'px'; pop.classList.add('show');
    $('#popAdd').disabled = !S.selected;
  }

  // ------------------------------------------------------------------ side panels
  function renderOverall() {
    const p = $('#overallPanel'); const r = S.review; if (!r) { p.innerHTML = '<p class="hint">尚未加载审稿 JSON。</p>'; return; }
    const ov = r.overall || {}; const sm = r.summary || {}; const pol = r.policy || {};
    const scale = S.form?.overall_scale; const lab = scale ? (scale.find((x) => x[0] === ov.score) || [])[1] : '';
    p.innerHTML = `<h3>总评</h3>
      <div class="kf"><div>推荐</div><div><b>${esc(ov.recommendation || '—')}</b>${ov.score != null ? `（${esc(ov.score)} 分${lab ? ' · ' + esc(lab) : ''}）` : ''}</div>
        <div>置信度</div><div>${esc(ov.confidence ?? '—')} / 5</div>
        ${ov.subscores ? `<div>分项</div><div>${Object.entries(ov.subscores).map(([k, v]) => esc(k) + ' ' + esc(v)).join(' · ')}</div>` : ''}</div>
      <p style="white-space:pre-wrap">${esc(ov.justification || '')}</p>
      ${ov.decisive_ids?.length ? `<h3>决定评分的意见</h3><div class="decisive">${ov.decisive_ids.map((id) => `<a href="#" data-goto="${esc(id)}" title="${esc(byId(id)?.title || '')}">${esc(id)}</a>`).join('')}</div>` : ''}
      ${ov.would_change ? `<h3>评分可能改变的条件</h3><p>${esc(ov.would_change)}</p>` : ''}
      <div class="actions"><button id="editOverall" ${S.readOnly ? 'disabled' : ''}>${ICON.edit}编辑总评</button></div>
      <h3>论文概述</h3>${['one_line', 'problem', 'method', 'setup'].filter((k) => sm[k]).map((k) => `<p><b>${{ one_line: '一句话', problem: '问题', method: '方法', setup: '实验' }[k]}：</b>${esc(sm[k])}</p>`).join('')}
      ${(sm.contributions || []).length ? `<h3>贡献与创新性</h3><table><tr><th>编号</th><th>贡献</th><th>页</th><th>创新性</th></tr>${sm.contributions.map((c) => `<tr><td>${esc(c.id)}</td><td>${esc(c.claim)}</td><td>${c.page ? `<a href="#" data-page="${c.page}">${c.page}</a>` : ''}</td><td>${L.novelty[c.novelty_assessment] || '—'}</td></tr>`).join('')}</table>` : ''}
      <h3>审稿范围</h3><div class="kf"><div>版本</div><div>${esc(r.paper?.version || '')}</div><div>截止日期</div><div>${esc(r.cutoff || '')}</div>
        <div>目标</div><div>${esc([r.venue?.id, r.venue?.year].filter(Boolean).join(' ') || '未指定')}</div>
        <div>阅读范围</div><div>${esc(pol.read_scope || '')}</div><div>未做的事</div><div>${esc((pol.not_done || []).join('；') || '无')}</div></div>`;
    $$('[data-goto]', p).forEach((a) => (a.onclick = (e) => { e.preventDefault(); switchTab('comments'); select(a.dataset.goto); }));
    $$('[data-page]', p).forEach((a) => (a.onclick = (e) => { e.preventDefault(); scrollToPage(+a.dataset.page); }));
    const eo = $('#editOverall'); if (eo) eo.onclick = editOverall;
  }
  function editOverall() {
    if (!guardEdit()) return; const ov = S.review.overall || {}; const scale = S.form?.overall_scale;
    const f = $('#editForm');
    f.innerHTML = `<h3>编辑总评</h3>
      <div class="f half"><label>推荐</label><input name="recommendation" value="${esc(ov.recommendation)}"></div>
      <div class="f half"><label>分数</label>${scale ? `<select name="score">${scale.map((x) => `<option value="${x[0]}"${x[0] === ov.score ? ' selected' : ''}>${x[0]} = ${esc(x[1])}</option>`).join('')}</select>` : `<input name="score" type="number" step="0.5" value="${esc(ov.score ?? '')}">`}</div>
      <div class="f half"><label>置信度</label><select name="confidence">${[5, 4, 3, 2, 1].map((v) => `<option${v === ov.confidence ? ' selected' : ''}>${v}</option>`).join('')}</select></div>
      <div class="f half"><label>决定性意见（逗号分隔）</label><input name="decisive_ids" value="${esc((ov.decisive_ids || []).join(', '))}"></div>
      <div class="f"><label>理由</label><textarea name="justification" rows="7">${esc(ov.justification)}</textarea></div>
      <div class="f"><label>评分可能改变的条件</label><textarea name="would_change" rows="2">${esc(ov.would_change)}</textarea></div>
      <div class="foot"><button value="cancel">取消</button><button class="primary" id="ovOk">保存</button></div>`;
    $('#ovOk').onclick = (e) => {
      e.preventDefault(); const fd = new FormData(f); pushUndo();
      S.review.overall = { ...ov, recommendation: fd.get('recommendation'), score: Number(fd.get('score')), confidence: Number(fd.get('confidence')),
        justification: fd.get('justification'), decisive_ids: String(fd.get('decisive_ids')).split(/[,\s]+/).filter(Boolean), would_change: fd.get('would_change') };
      setDirty(true); $('#editor').close(); renderAll();
    };
    $('#editor').showModal();
  }
  function renderDims() {
    const p = $('#dimsPanel'); const r = S.review; if (!r) { p.innerHTML = ''; return; }
    const tone = { issues: 'var(--major)', ok: 'var(--ok)', na: 'var(--faint)', unchecked: 'var(--minor)' };
    p.innerHTML = `<p class="hint">九个维度都需要结论：有问题（关联意见）、已检查无问题（写明检查范围）或不适用（写明理由）。</p>
      <table><tr><th style="width:96px">维度</th><th style="width:118px">结论</th><th>意见 / 检查说明</th></tr>${Object.entries(L.dim).map(([k, lab]) => {
        const d = r.dimensions[k] || {}; const ids = d.comment_ids || [];
        return `<tr><td><span class="dot" style="--c:${tone[d.status] || 'var(--line-strong)'}"></span>${lab}</td><td><select data-dim="${k}" ${S.readOnly ? 'disabled' : ''}>${opt({ '': '—', ...L.dimStatus }, d.status || '')}</select></td>
          <td><div class="decisive">${ids.map((id) => `<a href="#" data-goto="${esc(id)}">${esc(id)}</a>`).join('')}</div><textarea data-note="${k}" rows="2" ${S.readOnly ? 'disabled' : ''} placeholder="检查说明">${esc(d.note || '')}</textarea></td></tr>`; }).join('')}</table>`;
    $$('[data-goto]', p).forEach((a) => (a.onclick = (e) => { e.preventDefault(); switchTab('comments'); select(a.dataset.goto); }));
    $$('[data-dim]', p).forEach((s) => (s.onchange = () => { pushUndo(); const d = (r.dimensions[s.dataset.dim] = r.dimensions[s.dataset.dim] || {}); d.status = s.value || undefined; setDirty(true); renderDims(); }));
    $$('[data-note]', p).forEach((t) => (t.onchange = () => { pushUndo(); const d = (r.dimensions[t.dataset.note] = r.dimensions[t.dataset.note] || {}); d.note = t.value; setDirty(true); }));
  }

  function renderCritique() {
    const p = $('#critiquePanel'); const r = S.review; if (!r) { p.innerHTML = ''; return; }
    const cr = r.critique; const fs = (cr && cr.findings) || [];
    if (!fs.length) { p.innerHTML = '<h3>审稿意见复核</h3><p class="hint">尚未复核。运行 <code>critique.py lint</code> 并启动独立复核子代理（见 references/critique.md）。</p>'; return; }
    const T = { misread: '误读原文', already_addressed: '论文已回应', unsupported: '证据不足', severity: '轻重不当', vague: '表述空泛', tone: '语气不当', duplicate: '意见重复',
      missing_issue: '遗漏问题', missing_literature: '遗漏文献线', inconsistent: '总评与意见不一致', process: '流程缺口', other: '其他' };
    const R = { accepted: ['采纳', 'var(--ok)'], partial: ['部分采纳', 'var(--minor)'], rejected: ['不采纳', 'var(--muted)'] };
    const n = (k) => fs.filter((f) => f.resolution === k).length;
    p.innerHTML = `<h3>审稿意见复核</h3>
      <div class="kf"><div>复核者</div><div>${esc(cr.critic || '确定性检查（critique.py lint）')}</div><div>结果</div><div>共 ${fs.length} 条 · 采纳 ${n('accepted')} · 部分采纳 ${n('partial')} · 不采纳 ${n('rejected')}${fs.some((f) => !f.resolution) ? ' · <b style="color:var(--bad)">有未处理项</b>' : ''}</div></div>
      ${cr.summary ? `<p>${esc(cr.summary)}</p>` : ''}${cr.abstractions?.length ? `<h3>上位问题</h3><p>${cr.abstractions.map(esc).join('；')}</p>` : ''}
      <h3>复核意见</h3>${fs.map((f) => { const rr = R[f.resolution] || ['未处理', 'var(--bad)'];
        const tg = String(f.target || '').split(',').map((t) => t.trim()).map((t) => byId(t) ? `<a href="#" data-goto="${esc(t)}">${esc(t)}</a>` : esc(t)).join('、');
        return `<div class="card" style="--c:${f.severity === 'major' ? COLOR.major : COLOR.minor};cursor:default"><div class="card-head"><span class="cid">${esc(f.id)}</span>
          <span class="pill">${T[f.type] || esc(f.type)}</span><span class="pill" style="--c:${rr[1]}">${rr[0]}</span><span class="loc">对象：${tg}</span></div>
          <div class="body" style="-webkit-line-clamp:unset;display:block">${esc(f.detail)}</div>
          ${f.response ? `<div class="kv"><b>主审稿人回应</b>${esc(f.response)}</div>` : ''}</div>`; }).join('')}`;
    $$('[data-goto]', p).forEach((a) => (a.onclick = (e) => { e.preventDefault(); switchTab('comments'); select(a.dataset.goto); }));
  }

  function renderSummary() {
    const el = $('#summary'); const r = S.review;
    if (!r) { el.style.display = 'none'; return; } el.style.display = '';
    const ov = r.overall || {}; const scale = S.form?.overall_scale; const max = scale ? scale[scale.length - 1][0] : null;
    const lab = scale ? (scale.find((x) => x[0] === ov.score) || [])[1] : '';
    const all = comments(); const total = all.length || 1;
    const chips = QUICK.map(([key, label, color, f]) => [key, label, color, all.filter(f).length]);
    el.innerHTML = `<div class="row"><div class="scorebox"><div class="inner"><b>${ov.score ?? '–'}</b><small>${max != null ? '满分 ' + max : '评分'}</small></div></div>
      <div class="verdict"><div class="rec">${esc(ov.recommendation || '尚无总评')}</div>
      <div class="sub">${lab ? `<span>${esc(lab)}</span>` : ''}${ov.confidence ? `<span>置信度 ${esc(ov.confidence)}/5</span>` : ''}<span>${all.length} 条意见</span></div></div></div>
      <div class="statbar">${chips.map(([, , c, n]) => (n ? `<span style="width:${(100 * n) / total}%;background:${c}"></span>` : '')).join('')}</div>
      <div class="chips">${chips.map(([k, label, c, n]) => `<span class="chip${S.quick === k ? ' on' : ''}" data-q="${k}" style="--c:${c}"><i></i>${label}<b>${n}</b></span>`).join('')}</div>`;
    $$('[data-q]', el).forEach((ch) => (ch.onclick = () => { S.quick = S.quick === ch.dataset.q ? null : ch.dataset.q; S.reading = false; switchTab('comments'); renderSummary(); renderList(); renderOverlays(); }));
  }

  function updateMeta() {
    const r = S.review; const bits = [];
    if (r) { if (r.venue?.id) bits.push([r.venue.id, r.venue.year].filter(Boolean).join(' ')); if (r.paper?.version) bits.push(r.paper.version); bits.push({ quick: '快速预审', full: '完整审稿', self: '投稿前自查', rebuttal: '答辩评估' }[r.mode] || ''); }
    if (S.pdf) bits.push(S.pdf.numPages + ' 页');
    $('#docMeta').textContent = bits.filter(Boolean).join(' · ') || '打开 PDF 与审稿 JSON 开始';
    const nC = comments().length, nK = ((r && r.critique && r.critique.findings) || []).length;
    $('#tabs button[data-tab=comments]').innerHTML = `意见${nC ? ` <span class="n">${nC}</span>` : ''}`;
    $('#tabs button[data-tab=critique]').innerHTML = `复核${nK ? ` <span class="n">${nK}</span>` : ''}`;
  }
  function renderSources() {
    const p = $('#sourcesPanel'); const r = S.review; if (!r) { p.innerHTML = ''; return; }
    const stat = {}; comments().forEach((c) => (c.evidence || []).forEach((e) => { const s = (stat[e.source_id] = stat[e.source_id] || { n: 0, ok: 0 }); s.n++; if (evState(e).state === 'found') s.ok++; }));
    p.innerHTML = `<h3>来源与版本</h3><table><tr><th>编号</th><th>标题 / 版本</th><th>公开日期</th><th>引文核对</th></tr>${(r.sources || []).map((s) => {
      const st = stat[s.id] || { n: 0, ok: 0 }; const loaded = !!S.sources[s.id];
      return `<tr><td><span class="cid">${esc(s.id)}</span><div class="hint">${esc(s.role)}</div></td><td>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>` : esc(s.title || '')} <span class="hint">${esc(s.version || '')}</span></td>
        <td>${esc(s.public_date || '未知')}${s.date_verified ? ` <span title="${esc(s.date_verified.method)}" style="color:var(--ok)">✓</span>` : ''}</td><td>${loaded ? `${st.ok}/${st.n}` : '<span class="hint">未加载快照</span>'}</td></tr>`; }).join('')}</table>
      <p class="hint">日期后的 ✓ 表示已用 verify_dates.py / fetch_arxiv.py 核对（悬停查看方式）。引文核对只说明引文存在于快照中。</p>`;
  }
  function toggleWide() {
    const on = $('#app').classList.toggle('wide'); try { localStorage.setItem('bpr-wide', on ? '1' : '0'); } catch (e) { /* ignore */ }
    if (S.pdf && S.zoom === 'fit') setTimeout(() => setZoom('fit'), 230);
  }
  function switchTab(t) { $$('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === t)); $$('.tab').forEach((x) => x.classList.toggle('active', x.id === 'tab-' + t)); }
  function renderAll() { updateMeta(); renderSummary(); renderList(); renderOverlays(); renderOverall(); renderDims(); renderCritique(); renderSources(); }
  // ------------------------------------------------------------------ save / draft
  const draftKey = () => 'bpr-draft:' + (S.review?.paper?.pdf_sha256 || S.pdfSha || S.review?.paper?.title || 'x');
  function setDirty(v) {
    S.dirty = v; const el = $('#saveState'); el.textContent = v ? '有未保存的修改' : (S.review ? '已保存' : ''); el.classList.toggle('dirty', v);
    if (v && S.review) { clearTimeout(setDirty._t); setDirty._t = setTimeout(() => { try { localStorage.setItem(draftKey(), JSON.stringify({ at: new Date().toISOString(), review: S.review })); } catch (e) { /* storage unavailable */ } }, 600); }
  }
  function checkDraft() {
    let d = null; try { d = JSON.parse(localStorage.getItem(draftKey()) || 'null'); } catch (e) { return; }
    if (!d || !d.review || JSON.stringify(d.review.comments) === JSON.stringify(S.review.comments)) return;
    banner('info', `发现浏览器中暂存的草稿（${new Date(d.at).toLocaleString()}），与当前文件不同。`, [
      ['恢复草稿', () => { pushUndo(); S.review = d.review; setDirty(true); renderAll(); $('#banner').className = ''; }],
      ['丢弃草稿', () => { localStorage.removeItem(draftKey()); $('#banner').className = ''; }]]);
  }
  async function save() {
    if (!S.review) return;
    if (!S.canSave) { exportJson(); return; }
    try {
      const res = await fetch('api/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(S.review, null, 2) });
      const out = await res.json(); if (!res.ok || !out.ok) throw new Error(out.error || res.statusText);
      setDirty(false); try { localStorage.removeItem(draftKey()); } catch (e) { /* ignore */ }
      const v = out.validation;
      if (v) {
        if (v.valid) banner('info', `已保存（备份：${esc(out.backup || '无')}）。校验通过：${v.warnings.length} 条警告。`);
        else banner('warn', `已保存，但校验发现 ${v.errors.length} 个错误：${v.errors.slice(0, 3).map(esc).join('；')}${v.errors.length > 3 ? '…' : ''}`);
      } else toast('已保存');
    } catch (e) { banner('error', '保存失败：' + esc(e.message) + '。已改为下载 JSON。'); exportJson(); }
  }

  // ------------------------------------------------------------------ exports
  function exportJson() { download((baseName() || 'review') + '.review.json', JSON.stringify(S.review, null, 2) + '\n', 'application/json'); setDirty(false); }
  function exportMd() {
    const r = S.review; const ov = r.overall || {}; const out = [`# 《${r.paper?.title || ''}》审稿意见`, ''];
    if (ov.recommendation) out.push(`**推荐**：${ov.recommendation}${ov.score != null ? `（${ov.score} 分）` : ''}　**置信度**：${ov.confidence ?? ''}/5`, '', ov.justification || '', '');
    const groups = [['主要优点', (c) => c.status === 'strength'], ['主要问题', (c) => ['confirmed', 'limitation'].includes(c.status) && c.severity === 'major'],
      ['次要问题', (c) => ['confirmed', 'limitation'].includes(c.status) && c.severity !== 'major'], ['给作者的问题', (c) => c.status === 'question'], ['改进建议', (c) => c.status === 'suggestion']];
    groups.forEach(([t, f]) => {
      const xs = comments().filter(f); if (!xs.length) return; out.push(`## ${t}`, '');
      xs.forEach((c) => {
        out.push(`### ${c.id} ${c.title}`, '', `\`${[L.status[c.status], L.sev[c.severity], L.dim[c.dimension], c.scope === 'global' ? '全文' : '第 ' + c.page + ' 页'].filter(Boolean).join(' · ')}\``, '', c.body, '');
        (c.evidence || []).forEach((e) => out.push(`> “${e.quote}”${e.page ? `（p.${e.page}）` : ''}`, ''));
        if (c.countercheck) out.push(`**反证检查**：${c.countercheck}`, ''); if (c.request) out.push(`**建议作者**：${c.request}`, '');
      });
    });
    download((baseName() || 'review') + '.review.md', out.join('\n'), 'text/markdown');
  }
  async function exportPdf() {
    if (!S.pdfBytes || !S.review) { toast('需要先加载 PDF 和审稿 JSON'); return; }
    toast('正在生成带批注的 PDF…', 4000);
    const { PDFDocument, PDFName, PDFHexString, PDFString } = PDFLib;
    const doc = await PDFDocument.load(S.pdfBytes.slice(), { updateMetadata: false, ignoreEncryption: true });
    const pages = doc.getPages();
    const text = (c) => [`【${c.id}｜${L.status[c.status] || ''}${c.severity && c.status !== 'strength' ? '·' + L.sev[c.severity] : ''}｜${L.dim[c.dimension] || ''}】${c.title}`, '', c.body,
      ...(c.evidence || []).map((e) => `证据：“${e.quote}”${e.page ? `（p.${e.page}）` : ''}`), c.countercheck ? '反证检查：' + c.countercheck : '', c.request ? '建议作者：' + c.request : ''].filter((x) => x !== '').join('\n');
    const toPdf = (n, x, y) => S.base[n - 1].vp.convertToPdfPoint(x * S.base[n - 1].vp.width, y * S.base[n - 1].vp.height);
    const bbox = (n, x0, y0, x1, y1) => { const a = toPdf(n, x0, y0), b = toPdf(n, x1, y1); return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]; };
    const add = (n, dict) => { const ref = doc.context.register(doc.context.obj(dict)); pages[n - 1].node.addAnnot(ref); };
    const stamp = PDFString.fromDate(new Date());
    const perPage = {};
    comments().forEach((c) => {
      const color = RGB[colorKey(c)]; const common = { Type: 'Annot', Contents: PDFHexString.fromText(text(c)), T: PDFHexString.fromText(`${c.id} ${c.title}`.slice(0, 120)), NM: PDFHexString.fromText('bpr-' + c.id), M: stamp, C: color, F: 4 };
      if (c.scope === 'region' && c.rect && pages[c.page - 1]) {
        const [x, y, w, h] = c.rect; add(c.page, { ...common, Subtype: 'Square', Rect: bbox(c.page, x, y, x + w, y + h), BS: { W: 2 } });
      } else {
        const n = c.scope === 'page' && pages[c.page - 1] ? c.page : 1; const k = (perPage[n] = (perPage[n] || 0) + 1) - 1;
        const vp = S.base[n - 1].vp; const sz = 20 / vp.width, gap = 26 / vp.height;
        const left = c.scope === 'global'; const x0 = left ? 0.012 : 1 - 0.012 - sz; const y0 = 0.03 + k * gap;
        add(n, { ...common, Subtype: 'Text', Name: 'Comment', Open: false, Rect: bbox(n, x0, y0, x0 + sz, y0 + 20 / vp.height) });
      }
    });
    const bytes = await doc.save({ useObjectStreams: false });
    download((baseName() || 'paper') + '.annotated.pdf', bytes, 'application/pdf');
    toast(`已导出 ${comments().length} 条批注`);
  }

  // ------------------------------------------------------------------ wiring
  async function openFile(file) {
    if (!file) return;
    if (/\.pdf$/i.test(file.name) || file.type === 'application/pdf') { await loadPdf(new Uint8Array(await file.arrayBuffer())); if (!S.review) toast('已打开 PDF；再打开审稿 JSON，或直接新建意见'); }
    else { try { loadReview(JSON.parse(await file.text())); toast('已加载审稿 JSON（未加载快照时无法核对引文）'); } catch (e) { banner('error', '无法读取 JSON：' + esc(e.message)); } }
  }
  function wire() {
    $('#btnOpenPdf').onclick = () => $('#filePdf').click(); $('#btnOpenJson').onclick = () => $('#fileJson').click();
    $('#dropPdf').onclick = () => $('#filePdf').click(); $('#dropJson').onclick = () => $('#fileJson').click();
    const applyTheme = (t) => { document.documentElement.dataset.theme = t; try { localStorage.setItem('bpr-theme', t); } catch (e) { /* ignore */ } };
    let theme = null; try { theme = localStorage.getItem('bpr-theme'); } catch (e) { /* ignore */ }
    applyTheme(theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
    $('#btnTheme').onclick = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    $('#filePdf').onchange = (e) => openFile(e.target.files[0]); $('#fileJson').onchange = (e) => openFile(e.target.files[0]);
    const v = $('#viewer');
    v.addEventListener('dragover', (e) => { e.preventDefault(); $('#drop').classList.add('hover'); });
    v.addEventListener('dragleave', () => $('#drop').classList.remove('hover'));
    v.addEventListener('drop', async (e) => { e.preventDefault(); $('#drop').classList.remove('hover'); const fs = [...e.dataTransfer.files].sort((a) => (/\.pdf$/i.test(a.name) ? -1 : 1)); for (const f of fs) await openFile(f); });
    v.addEventListener('scroll', () => requestAnimationFrame(trackCurrent));
    $('#zoomSel').onchange = (e) => setZoom(e.target.value);
    $('#btnZoomIn').onclick = () => setZoom(Math.min(4, +(S.scale * 1.2).toFixed(2)));
    $('#btnZoomOut').onclick = () => setZoom(Math.max(0.3, +(S.scale / 1.2).toFixed(2)));
    $('#pageInput').onchange = (e) => scrollToPage(Math.max(1, Math.min(S.pdf?.numPages || 1, +e.target.value)));
    $('#btnAddGlobal').onclick = () => openEditor(null, { scope: 'global' });
    $('#btnAddPage').onclick = () => openEditor(null, { scope: 'page', page: S.current });
    $('#btnRegion').onclick = () => setRegionMode(!S.regionMode);
    $('#btnUndo').onclick = doUndo; $('#btnSave').onclick = save; $('#btnHelp').onclick = () => $('#help').showModal();
    $('#btnExport').onclick = (e) => { e.stopPropagation(); $('#exportMenu').classList.toggle('open'); };
    document.addEventListener('click', () => $('#exportMenu').classList.remove('open'));
    $$('[data-export]').forEach((b) => (b.onclick = () => { if (!S.review) return toast('尚未加载审稿 JSON'); ({ json: exportJson, md: exportMd, pdf: exportPdf })[b.dataset.export](); }));
    ['#q', '#fScope', '#fStatus', '#fSev', '#fDim'].forEach((s) => $(s).addEventListener('input', () => { S.reading = false; renderList(); renderOverlays(); }));
    $('#btnFilters').onclick = () => { const m = $('#fMore'); m.hidden = !m.hidden; $('#btnFilters').classList.toggle('on', !m.hidden); };
    $('#btnWide').onclick = toggleWide;
    try { const w = +localStorage.getItem('bpr-side2'); if (w > 300) document.documentElement.style.setProperty('--side', Math.min(w, innerWidth - 360) + 'px'); if (localStorage.getItem('bpr-wide') === '1') $('#app').classList.add('wide'); } catch (e) { /* ignore */ }
    $('#fStatus').innerHTML += Object.entries(L.status).map(([k, x]) => `<option value="${k}">${x}</option>`).join('');
    $('#fDim').innerHTML += Object.entries(L.dim).map(([k, x]) => `<option value="${k}">${x}</option>`).join('');
    $$('#tabs button').forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
    $('#pages').addEventListener('mouseup', (e) => setTimeout(() => onSelectionEnd(e), 0));
    document.addEventListener('mousedown', (e) => { if (!e.target.closest('#selPop')) $('#selPop').classList.remove('show'); });
    $('#popNew').onclick = () => { const s = $('#selPop')._sel; $('#selPop').classList.remove('show'); openEditor(null, { scope: 'page', page: s.page, evidence: [{ source_id: manuscriptId() || 'paper', quote: s.quote, page: s.page }] }); };
    $('#popAdd').onclick = () => {
      const s = $('#selPop')._sel; const c = byId(S.selected); $('#selPop').classList.remove('show'); if (!c || !guardEdit()) return;
      pushUndo(); c.evidence = [...(c.evidence || []), { source_id: manuscriptId() || 'paper', quote: s.quote, page: s.page }]; setDirty(true); renderList(); toast(`已添加到 ${c.id}`);
    };
    const g = $('#gutter');
    g.addEventListener('mousedown', (e) => {
      e.preventDefault(); $('#app').classList.remove('wide'); let w = 0;
      const mm = (ev) => { w = Math.max(300, Math.min(innerWidth - 360, innerWidth - ev.clientX)); document.documentElement.style.setProperty('--side', w + 'px'); };
      const mu = () => { removeEventListener('mousemove', mm); removeEventListener('mouseup', mu); try { if (w) localStorage.setItem('bpr-side2', w); localStorage.setItem('bpr-wide', '0'); } catch (err) { /* ignore */ } if (S.zoom === 'fit') setZoom('fit'); };
      addEventListener('mousemove', mm); addEventListener('mouseup', mu);
    });
    addEventListener('resize', () => { if (S.pdf && S.zoom === 'fit') { clearTimeout(wire._r); wire._r = setTimeout(() => setZoom('fit'), 200); } });
    addEventListener('beforeunload', (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input,textarea,select,dialog')) { if (e.key === 'Escape') e.target.blur(); return; }
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === 's') { e.preventDefault(); save(); return; }
      if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); doUndo(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (k === 'j') step(1);
      else if (k === 'k') step(-1);
      else if (k === 'w') toggleWide();
      else if (k === 'n') openEditor(null, { scope: 'page', page: S.current });
      else if (k === 'r') setRegionMode(!S.regionMode);
      else if (k === 'e' && S.selected) openEditor(byId(S.selected));
      else if ((k === 'delete' || k === 'backspace') && S.selected) cardAction(S.selected, 'del');
      else if (k === '/') { e.preventDefault(); $('#q').focus(); }
      else if (k === '+' || k === '=') $('#btnZoomIn').click(); else if (k === '-') $('#btnZoomOut').click();
      else if (k === 'escape') { if (S.reading) closeDetail(); setRegionMode(false); $('#selPop').classList.remove('show'); }
    });
  }

  async function boot() {
    wire(); renderAll();
    if (EMBED) {
      pdfjsLib.GlobalWorkerOptions.workerSrc = EMBED.workerUrl;
      await loadPdf(b64ToBytes(EMBED.pdf)); loadReview(EMBED.review); await loadSourcesPayload(EMBED);
      return;
    }
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'vendor/pdf.worker.min.js';
    try {
      const cfg = await fetch('api/config').then((r) => (r.ok ? r.json() : null));
      if (cfg) {
        S.canSave = !!cfg.canSave; $('#btnSave').hidden = !S.canSave;
        if (cfg.pdf) await loadPdf(new Uint8Array(await (await fetch('api/pdf')).arrayBuffer()));
        if (cfg.review) loadReview(await (await fetch('api/review')).json());
        if (cfg.review) await loadSourcesPayload(await (await fetch('api/sources')).json());
      }
    } catch (e) { /* static hosting without API: manual open */ }
  }
  window.BPR = { state: S, findQuote, evState, select, exportPdf, exportJson, exportMd, openEditor };
  boot().catch((e) => banner('error', '加载失败：' + esc(e.message)));
})();
