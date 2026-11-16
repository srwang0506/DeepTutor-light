/* Best-Paper-Review reader (default review-viewer.html, read-only; editing: serve.py / package_viewer.py --editor).
 * Data: window.__BPR_EMBED__ = {pdf (base64), review, sources, workerUrl}
 * Quotes are anchored on the pdf.js text layer by normalised text search
 * (letters/digits only), so evidence and contribution quotes highlight the
 * exact sentence instead of a page badge. */
(() => {
  'use strict';
  const EMBED = window.__BPR_EMBED__ || {};
  const R = EMBED.review || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ------------------------------------------------------------ typesetting
  // Chinese/Latin spacing: drop literal spaces next to CJK and insert a fixed
  // 1/4-em gap instead; render $...$ with KaTeX and turn plain-text formulas
  // (Q_target, a′, ≤, τ=0.005, 10×62.5K ...) into KaTeX as well.
  const CJK = '\\u3000-\\u303f\\u3400-\\u9fff\\uf900-\\ufaff\\uff00-\\uffef“”‘’';
  const IDEO = '\\u3400-\\u9fff\\uf900-\\ufaff';
  const GREEK = { α: '\\alpha', β: '\\beta', γ: '\\gamma', δ: '\\delta', ε: '\\epsilon', η: '\\eta', θ: '\\theta', κ: '\\kappa', λ: '\\lambda', μ: '\\mu', π: '\\pi', ρ: '\\rho', σ: '\\sigma', τ: '\\tau', φ: '\\phi', ω: '\\omega' };
  const TRIG = /[_′≤≥→×^∈=αβγδεηθκλμπρστφω]/;
  const isCode = (t) => /^[a-z]{3,}_[a-z_]{3,}$/.test(t.replace(/[(),.;:]/g, ''));
  function toTex(m) {
    let s = m.replace(/[{}]/g, (c) => '\\' + c).replace(/θ̄/g, '\\bar{\\theta}').replace(/[′]/g, "'").replace(/−/g, '-');
    s = s.replace(/[αβγδεηθκλμπρστφω]/g, (c) => GREEK[c] + ' ');
    s = s.replace(/(^|[^A-Za-z\\])(min|max)(?=[_(\s]|$)/g, '$1\\$2');
    s = s.replace(/_(\\bar\{\\[a-z]+\})/g, '_{$1}').replace(/_(\\[a-z]+) ?/g, '_{$1}').replace(/_([A-Za-z]{2,})/g, '_{\\text{$1}}').replace(/_([A-Za-z0-9]'?)/g, '_{$1}');
    s = s.replace(/≤/g, '\\le ').replace(/≥/g, '\\ge ').replace(/→/g, '\\to ').replace(/×/g, '\\times ').replace(/∈/g, '\\in ');
    s = s.replace(/\^(-?\d+)/g, '^{$1}').replace(/(\d)K\b/g, '$1\\text{K}');
    return s;
  }
  function autoMath(text, keep) {
    const parts = text.split(new RegExp(`(\\s+|[${CJK}、])`));
    const tok = parts.map((p, i) => ({ p, sep: i % 2 === 1, ws: i % 2 === 1 && /^\s+$/.test(p) }));
    const math = tok.map((t) => !t.sep && t.p && ((TRIG.test(t.p) && !isCode(t.p)) || /^[αβγδεηθκλμπρστφω]$/.test(t.p)));
    const small = (t) => /^([A-Za-z]|[+\-=·]|\d+(\.\d+)?|[()])$/.test(t.p);
    for (let pass = 0; pass < 3; pass++) for (let i = 0; i < tok.length; i++) {
      if (math[i] || tok[i].sep || !tok[i].p || !small(tok[i])) continue;
      const L = i >= 2 && tok[i - 1].ws && math[i - 2], Rt = i + 2 < tok.length && tok[i + 1].ws && math[i + 2];
      if (L || Rt) math[i] = true;
    }
    let out = '', i = 0;
    while (i < tok.length) {
      if (!math[i]) { out += tok[i].p; i++; continue; }
      let j = i, run = tok[i].p, depth = (run.match(/\(/g) || []).length - (run.match(/\)/g) || []).length;
      while (j + 2 < tok.length && tok[j + 1].ws && (math[j + 2] || (depth > 0 && !tok[j + 2].sep && tok[j + 2].p))) { run += tok[j + 1].p + tok[j + 2].p; depth += (tok[j + 2].p.match(/\(/g) || []).length - (tok[j + 2].p.match(/\)/g) || []).length; j += 2; }
      const trail = run.match(/(\s*[=×→≤≥+·]\s*|[.,;:]+)$/)?.[0] || ''; const core = trail && depth <= 0 ? run.slice(0, -trail.length) : run;
      const numeric = /^[\d.,%\s]+([→×][\d.,%\s]+K?)+$/.test(core); // 0.52→0.73, 50×62.5K stay text like the numbers around them
      out += (/^([\s=×→≤≥+·\-]*|\d+(\.\d+)?)$/.test(core) ? core : numeric ? core.replace(/\s*([→×])\s*/g, '\u202F$1\u202F') : keep(toTex(core))) + (core === run ? '' : trail); i = j + 1;
    }
    return out;
  }
  function tx(text) {
    if (text == null) return '';
    const store = []; const keep = (tex, display) => { store.push([tex, display]); return `${store.length - 1}`; };
    let t = String(text).replace(/\$\$([^$]+)\$\$/g, (_, m) => keep(m, true)).replace(/\$([^$\n]+)\$/g, (_, m) => keep(m, false));
    t = t.split(/(\d+)/).map((seg) => (/^\d+$/.test(seg) ? seg : autoMath(seg, keep))).join('');
    t = t.replace(new RegExp(`([${CJK}])[ \\t]+(?=\\S)`, 'g'), '$1').replace(new RegExp(`(\\S)[ \\t]+(?=[${CJK}])`, 'g'), '$1');
    t = esc(t);
    t = t.replace(new RegExp(`([${IDEO}])(?=[A-Za-z0-9(+\\-−\\uE000])`, 'g'), '$1<span class="as"></span>')
         .replace(new RegExp(`([A-Za-z0-9)%\\uE001])(?=[${IDEO}])`, 'g'), '$1<span class="as"></span>');
    t = t.replace(/[“”‘’]/g, (q) => `<span class="q">${q}</span>`);
    // keep a formula together with the CJK punctuation right after it (no line starting with 、，。)
    t = t.replace(/(\d+)((?:<span class="q">[”’]<\/span>|[，。、；：！？）])+)/g, '<span class="nw">$1$2</span>');
    return t.replace(/(\d+)/g, (_, n) => { const [tex, dsp] = store[+n];
      try { return window.katex ? katex.renderToString(tex, { throwOnError: true, displayMode: !!dsp, strict: 'ignore' }) : esc(tex); } catch { return `<code>${esc(tex)}</code>`; } });
  }
  const store = { get(k, d) { try { const v = localStorage.getItem('bpr2:' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem('bpr2:' + k, JSON.stringify(v)); } catch { /* ignore */ } } };

  const STATUS = {
    major: { label: '主要问题', color: 'var(--major)', hex: '#d1495b' },
    minor: { label: '次要问题', color: 'var(--minor)', hex: '#e0882b' },
    question: { label: '待澄清', color: 'var(--question)', hex: '#7a5bd6' },
    suggestion: { label: '改进建议', color: 'var(--suggestion)', hex: '#2f80c2' },
    strength: { label: '优点', color: 'var(--strength)', hex: '#2a9d6f' },
  };
  const STATUS_ZH = { confirmed: '确认问题', limitation: '证据局限', question: '澄清问题', suggestion: '改进建议', strength: '优点' };
  const DIM_ZH = { problem: '问题与动机', novelty: '创新性', soundness: '方法正确性', theory: '理论', experiments: '实验设计', baselines: '基线与公平性', reproducibility: '可复现性', clarity: '写作与图表', limitations: '局限与伦理' };
  const NOV_ZH = { distinct: '有明确区别', incremental: '增量但有用', overlap: '与前作实质重叠', unresolved: '无法判定', na: '未做前作检索' };
  const DIGEST = { contrib: { label: '贡献原话', hex: '#2f6fdb' }, exp: { label: '实验与数字', hex: '#0e9488' } };
  const MANU = (R.sources || []).find((s) => s.role === 'manuscript')?.id || 'paper';

  const S = { pdf: null, base: [], scale: 1, zoom: 'fit', mode: 'review', current: 1, selected: null, filter: new Set(), q: '', pending: {}, anchors: {} };

  // ------------------------------------------------------------ comments model
  const kindOf = (c) => (c.status === 'strength' ? 'strength' : c.status === 'question' ? 'question' : c.status === 'suggestion' ? 'suggestion' : c.severity === 'major' ? 'major' : 'minor');
  const comments = (R.comments || []).map((c, i) => ({ ...c, _k: kindOf(c), _i: i }));
  const ORDER = ['major', 'minor', 'question', 'suggestion', 'strength'];
  const ordered = () => ORDER.flatMap((k) => comments.filter((c) => c._k === k));
  const byId = (id) => comments.find((c) => c.id === id);
  const visible = () => ordered().filter((c) => !S.filter.has(c._k) && (!S.q || JSON.stringify([c.id, c.title, c.body, c.evidence, c.request]).toLowerCase().includes(S.q)));

  // quotes to anchor, per mode
  function anchorSpecs() {
    const out = [];
    if (S.mode === 'review') {
      comments.forEach((c) => {
        const ev = (c.evidence || []).filter((e) => e.source_id === MANU && e.quote);
        ev.forEach((e, j) => out.push({ key: c.id + '#' + j, cid: c.id, page: e.page, quote: e.quote, hex: STATUS[c._k].hex, title: c.title, label: c.id }));
        if (c.scope === 'region' && c.rect && c.page) out.push({ key: c.id + '#box', cid: c.id, page: c.page, rect: c.rect, hex: STATUS[c._k].hex, title: c.title, label: c.id });
      });
    } else {
      (R.summary?.contributions || []).forEach((k) => { if (k.quote && k.page) out.push({ key: 'D' + k.id, did: 'contrib-' + k.id, page: k.page, quote: k.quote, hex: DIGEST.contrib.hex, title: `${k.id} ${k.claim || ''}`, label: k.id }); });
      (R.numeric_checks || []).forEach((n) => (n.evidence || []).forEach((e, j) => { if (e.source_id === MANU) out.push({ key: 'N' + n.id + j, did: 'nums', page: e.page, quote: e.quote, hex: DIGEST.exp.hex, title: n.claim, label: n.id }); }));
    }
    return out;
  }

  // ------------------------------------------------------------ text anchoring
  function normMap(str) {
    let s = ''; const idx = [];
    for (let i = 0; i < str.length; i++) {
      const n = str[i].normalize('NFKC').toLowerCase();
      for (const ch of n) if (/[\p{L}\p{N}]/u.test(ch)) { s += ch; idx.push(i); }
    }
    return { s, idx };
  }
  function pageIndex(div) {
    if (div._ix) return div._ix;
    const nodes = []; let full = '';
    (div._divs || []).forEach((d) => { const t = d.firstChild; if (!t || t.nodeType !== 3) return; nodes.push({ node: t, start: full.length, len: t.data.length }); full += t.data + ' '; });
    const nm = normMap(full); div._ix = { nodes, nm };
    return div._ix;
  }
  function locate(ix, quote) {
    const q = normMap(quote).s; if (q.length < 4) return null;
    let a = ix.nm.s.indexOf(q), b = a + q.length - 1;
    if (a < 0 && q.length > 50) { // tolerate extraction differences in the middle
      const head = q.slice(0, 28), tail = q.slice(-28);
      const h = ix.nm.s.indexOf(head);
      if (h >= 0) { const t = ix.nm.s.indexOf(tail, h); if (t >= 0 && t - h < q.length * 1.6 + 40) { a = h; b = t + tail.length - 1; } }
    }
    if (a < 0) return null;
    return [ix.nm.idx[a], ix.nm.idx[b]];
  }
  function nodeAt(ix, off) { for (const n of ix.nodes) if (off >= n.start && off < n.start + n.len) return [n.node, off - n.start]; return null; }
  function rectsFor(div, quote) {
    const ix = pageIndex(div); const span = locate(ix, quote); if (!span) return null;
    const s = nodeAt(ix, span[0]), e = nodeAt(ix, span[1]); if (!s || !e) return null;
    const rg = document.createRange(); rg.setStart(s[0], s[1]); rg.setEnd(e[0], e[1] + 1);
    const pr = div.getBoundingClientRect();
    const rs = [...rg.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5).map((r) => ({ x: r.left - pr.left, y: r.top - pr.top, w: r.width, h: r.height }));
    // merge rects on the same line
    rs.sort((p, q) => p.y - q.y || p.x - q.x);
    const lines = [];
    rs.forEach((r) => { const L = lines.find((l) => Math.abs(l.y - r.y) < r.h * 0.5); if (L) { const x2 = Math.max(L.x + L.w, r.x + r.w); L.x = Math.min(L.x, r.x); L.w = x2 - L.x; L.h = Math.max(L.h, r.h); } else lines.push({ ...r }); });
    return lines.length ? lines : null;
  }

  // ------------------------------------------------------------ pages
  let io = null;
  function computeScale() {
    if (S.zoom !== 'fit') return S.zoom;
    const w = Math.max(...S.base.map((b) => b.vp.width)); const avail = $('#viewer').clientWidth - 120;
    return Math.max(0.4, Math.min(2.6, avail / w));
  }
  function buildPages() {
    const box = $('#pages'); box.innerHTML = ''; S.scale = computeScale();
    if (io) io.disconnect();
    io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && renderPage(+e.target.dataset.page)), { root: $('#viewer'), rootMargin: '800px 0px' });
    S.base.forEach((b, i) => {
      const n = i + 1; const d = document.createElement('div'); d.className = 'page'; d.dataset.page = n;
      d.innerHTML = `<span class="pno">${n}</span><canvas></canvas><div class="textLayer"></div><div class="hl"></div><div class="margin"></div>`;
      size(d, b.vp); box.appendChild(d); io.observe(d);
    });
    $('#zoomLbl').textContent = Math.round(S.scale * 100) + '%';
  }
  function size(d, vp) { d.style.width = vp.width * S.scale + 'px'; d.style.height = vp.height * S.scale + 'px'; d.dataset.rendered = ''; d._ix = null; }
  async function renderPage(n) {
    const d = $(`.page[data-page="${n}"]`); if (!d || d.dataset.rendered === String(S.scale) || S.pending[n]) return;
    S.pending[n] = true;
    try {
      const { page } = S.base[n - 1]; const vp = page.getViewport({ scale: S.scale }); const dpr = window.devicePixelRatio || 1;
      const cv = d.querySelector('canvas'); cv.width = Math.floor(vp.width * dpr); cv.height = Math.floor(vp.height * dpr); cv.style.width = vp.width + 'px'; cv.style.height = vp.height + 'px';
      await page.render({ canvasContext: cv.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
      const tl = d.querySelector('.textLayer'); tl.innerHTML = ''; tl.style.setProperty('--scale-factor', S.scale);
      const divs = []; await pdfjsLib.renderTextLayer({ textContentSource: await page.getTextContent(), container: tl, viewport: vp, textDivs: divs }).promise;
      d._divs = divs; d._ix = null; d.dataset.rendered = String(S.scale);
      paintPage(n);
    } catch (e) { console.error('render', n, e); } finally { delete S.pending[n]; }
  }
  function paintPage(n) {
    const d = $(`.page[data-page="${n}"]`); if (!d || !d.dataset.rendered) return;
    const hl = d.querySelector('.hl'), mg = d.querySelector('.margin'); hl.innerHTML = ''; mg.innerHTML = '';
    const W = d.clientWidth, H = d.clientHeight; const marks = [];
    anchorSpecs().filter((a) => a.page === n).forEach((a) => {
      let top = null;
      if (a.rect) {
        const [x, y, w, h] = a.rect; const b = document.createElement('div'); b.className = 'box'; b.style.cssText = `left:${x * W}px;top:${y * H}px;width:${w * W}px;height:${h * H}px;color:${a.hex}`;
        b.innerHTML = `<em>${esc(a.label)}</em>`; bind(b, a); hl.appendChild(b); top = y * H;
      } else {
        const rs = rectsFor(d, a.quote); S.anchors[a.key] = rs ? 'ok' : 'miss';
        if (!rs) return;
        rs.forEach((r) => { const e = document.createElement('div'); e.className = 'r'; e.dataset.key = a.cid || a.did; e.style.cssText = `left:${r.x - 1}px;top:${r.y - 1}px;width:${r.w + 2}px;height:${r.h + 2}px;background:${a.hex}38;color:${a.hex}`; bind(e, a); hl.appendChild(e); });
        top = rs[0].y;
      }
      marks.push({ a, top });
    });
    marks.sort((p, q) => p.top - q.top); let last = -99;
    const seen = new Set();
    marks.forEach(({ a, top }) => {
      const id = a.cid || a.did; if (seen.has(id)) return; seen.add(id);
      const y = Math.max(top, last + 21); last = y;
      const m = document.createElement('div'); m.className = 'mk'; m.style.top = y + 'px'; m.style.background = a.hex; m.dataset.key = id;
      m.textContent = (a.label || '').replace(/^([A-Z])0?/, '$1'); bind(m, a); mg.appendChild(m);
    });
    // page-scope comments without a located quote: marker at page top
    if (S.mode === 'review') comments.filter((c) => c.page === n && c.scope !== 'global' && !seen.has(c.id)).forEach((c, i) => {
      if (seen.has(c.id)) return; seen.add(c.id);
      const m = document.createElement('div'); m.className = 'mk'; m.style.top = (6 + i * 21) + 'px'; m.style.background = STATUS[c._k].hex; m.style.opacity = '.75'; m.dataset.key = c.id;
      m.textContent = c.id.replace(/^([A-Z])0?/, '$1'); bind(m, { cid: c.id, title: c.title + '（页级意见）', label: c.id }); mg.appendChild(m);
    });
    markSelected();
  }
  function repaintAll() { $$('.page').forEach((d) => d.dataset.rendered && paintPage(+d.dataset.page)); renderThumbDots(); }
  function bind(el, a) {
    el.addEventListener('mouseenter', (ev) => showTip(ev, a)); el.addEventListener('mousemove', moveTip); el.addEventListener('mouseleave', hideTip);
    el.addEventListener('click', () => { if (a.cid) select(a.cid, { scroll: false }); else if (a.did) gotoDigest(a.did); });
  }
  const tip = () => $('#tip');
  function showTip(ev, a) { const c = a.cid && byId(a.cid); tip().innerHTML = `<b>${esc(a.label)} · ${tx(a.title)}</b>${c ? `<small>${esc(STATUS[c._k].label)} · ${esc(DIM_ZH[c.dimension] || '')}</small>` : ''}`; tip().classList.add('on'); moveTip(ev); }
  function moveTip(ev) { const t = tip(); const x = Math.min(ev.clientX + 14, innerWidth - t.offsetWidth - 10); t.style.left = x + 'px'; t.style.top = ev.clientY + 16 + 'px'; }
  function hideTip() { tip().classList.remove('on'); }
  function markSelected() { $$('.hl .r, .mk, .hl .box').forEach((e) => e.classList.toggle('sel', !!S.selected && e.dataset.key === S.selected)); }

  function setZoom(z) {
    const keep = S.current; S.zoom = z; S.scale = computeScale(); $('#zoomLbl').textContent = Math.round(S.scale * 100) + '%';
    $$('.page').forEach((d) => { size(d, S.base[d.dataset.page - 1].vp); d.querySelector('.textLayer').innerHTML = ''; d.querySelector('.hl').innerHTML = ''; d.querySelector('.margin').innerHTML = ''; });
    scrollToPage(keep, 0, false);
  }
  function scrollToPage(n, y = 0, smooth = true) {
    const d = $(`.page[data-page="${n}"]`); if (!d) return;
    // long jumps are instant: a smooth scroll would pass every page in between
    const far = Math.abs(n - S.current) > 3;
    S.jumpTo = n; S.lastScroll = Date.now();
    $('#viewer').scrollTo({ top: Math.max(0, d.offsetTop + y - 80), behavior: smooth && !far ? 'smooth' : 'auto' });
  }
  async function revealQuote(page, quote, hex) {
    scrollToPage(page, 0, false); await renderPage(page);
    const d = $(`.page[data-page="${page}"]`); const rs = d && rectsFor(d, quote);
    if (rs) { scrollToPage(page, rs[0].y - 120); flash(d, rs, hex); } else scrollToPage(page);
    return !!rs;
  }
  function flash(d, rs, hex) {
    rs.forEach((r) => { const e = document.createElement('div'); e.className = 'r flash'; e.style.cssText = `left:${r.x - 2}px;top:${r.y - 2}px;width:${r.w + 4}px;height:${r.h + 4}px;background:${hex || '#6d4ee0'}55;pointer-events:none`; d.querySelector('.hl').appendChild(e); setTimeout(() => e.remove(), 1300); });
  }
  function trackCurrent() {
    const v = $('#viewer'); const mid = v.scrollTop + 140; let cur = 1;
    $$('.page').forEach((d) => { if (d.offsetTop <= mid) cur = +d.dataset.page; });
    if (cur !== S.current) { S.current = cur; $('#pageInput').value = cur; $$('.thumb').forEach((t) => t.classList.toggle('cur', +t.dataset.page === cur)); }
    clearTimeout(S.settle); S.settle = setTimeout(() => { S.jumpTo = null; followRail(); }, 160);
    if (!S.jumpTo) followRail();
  }
  // keep the current thumbnail in view, but never while the user is browsing the rail
  // or while a programmatic jump is still scrolling (that caused the rail to chase every page)
  function followRail() {
    if (S.jumpTo || Date.now() - (S.railTouched || 0) < 2500) return;
    const box = $('#thumbs'); const t = $(`.thumb[data-page="${S.current}"]`); if (!box || !t) return;
    const top = t.offsetTop - box.offsetTop, bot = top + t.offsetHeight;
    if (top < box.scrollTop || bot > box.scrollTop + box.clientHeight) box.scrollTop = top - box.clientHeight / 2 + t.offsetHeight / 2;
  }

  // ------------------------------------------------------------ thumbnails
  function buildThumbs() {
    const box = $('#thumbs'); box.innerHTML = '';
    const tio = new IntersectionObserver((es) => es.forEach(async (e) => {
      if (!e.isIntersecting || e.target.dataset.done) return; e.target.dataset.done = '1';
      const n = +e.target.dataset.page; const { page } = S.base[n - 1]; const vp = page.getViewport({ scale: 96 / S.base[n - 1].vp.width });
      const c = document.createElement('canvas'); c.width = vp.width * 2; c.height = vp.height * 2; c.style.height = vp.height + 'px';
      await page.render({ canvasContext: c.getContext('2d'), viewport: vp, transform: [2, 0, 0, 2, 0, 0] }).promise;
      e.target.querySelector('.ph').replaceWith(c);
    }), { root: box, rootMargin: '300px 0px' });
    S.base.forEach((b, i) => {
      const t = document.createElement('div'); t.className = 'thumb' + (i === 0 ? ' cur' : ''); t.dataset.page = i + 1;
      t.innerHTML = `<div class="ph" style="height:${96 * b.vp.height / b.vp.width}px"></div><div class="no">${i + 1}</div><div class="dots"></div>`;
      t.onclick = () => { S.railTouched = Date.now(); scrollToPage(i + 1); }; box.appendChild(t); tio.observe(t);
    });
    ['wheel', 'touchmove', 'pointerdown', 'keydown'].forEach((ev) => box.addEventListener(ev, () => { S.railTouched = Date.now(); }, { passive: true }));
    box.addEventListener('scroll', () => { if (Date.now() - (S.railTouched || 0) < 2500) S.railTouched = Date.now(); }, { passive: true });
    renderThumbDots();
  }
  function renderThumbDots() {
    $$('.thumb').forEach((t) => {
      const n = +t.dataset.page; const ks = new Set();
      if (S.mode === 'review') comments.forEach((c) => { if (c.page === n || (c.evidence || []).some((e) => e.source_id === MANU && e.page === n)) ks.add(STATUS[c._k].hex); });
      else anchorSpecs().forEach((a) => a.page === n && ks.add(a.hex));
      t.querySelector('.dots').innerHTML = [...ks].slice(0, 4).map((h) => `<i style="background:${h}"></i>`).join('');
    });
  }

  // ------------------------------------------------------------ side: review
  function renderChips() {
    $('#statusChips').innerHTML = ORDER.map((k) => { const n = comments.filter((c) => c._k === k).length; return n ? `<button class="chip ${S.filter.has(k) ? 'off' : ''}" data-k="${k}"><i style="background:${STATUS[k].hex}"></i>${STATUS[k].label}<b>${n}</b></button>` : ''; }).join('');
    $$('#statusChips .chip').forEach((b) => b.onclick = () => { const k = b.dataset.k; S.filter.has(k) ? S.filter.delete(k) : S.filter.add(k); renderChips(); renderList(); });
  }
  function pagesOf(c) { const ps = [...new Set([c.page, ...(c.evidence || []).filter((e) => e.source_id === MANU).map((e) => e.page)].filter(Boolean))].sort((a, b) => a - b); return ps; }
  function renderList() {
    const vs = visible(); $('#nComments').textContent = comments.length;
    $('#list').innerHTML = ORDER.map((k) => {
      const g = vs.filter((c) => c._k === k); if (!g.length) return '';
      return `<div class="grp"><h4><i style="background:${STATUS[k].hex}"></i>${STATUS[k].label} · ${g.length}</h4>${g.map((c) => {
        const ps = pagesOf(c);
        return `<button class="card ${S.selected === c.id ? 'sel' : ''}" data-id="${esc(c.id)}" style="--c:${STATUS[k].hex}"><div class="t">${tx(c.title)}</div><div class="x">${tx(c.body)}</div><div class="m"><span>${esc(DIM_ZH[c.dimension] || c.dimension || '')}</span>${c.status !== 'strength' ? `<span>${esc(STATUS_ZH[c.status] || '')}</span>` : ''}${ps.length ? `<span class="p">p.${ps.join(', ')}</span>` : '<span>全文</span>'}</div></button>`;
      }).join('')}</div>`;
    }).join('') || '<p class="note" style="padding:14px">没有匹配的意见。</p>';
    $$('#list .card').forEach((b) => b.onclick = () => select(b.dataset.id));
  }
  function evHtml(e, hex) {
    const src = (R.sources || []).find((s) => s.id === e.source_id); const manu = e.source_id === MANU;
    return `<button class="quote" style="--c:${hex}" data-src="${esc(e.source_id)}" data-page="${e.page || ''}" data-quote="${esc(e.quote)}">“${esc(e.quote)}”<span class="src">${manu ? '稿件' : esc(src?.title || e.source_id)} · p.${esc(e.page)}${manu ? ' · 点击定位原文' : ''}</span></button>`;
  }
  function select(id, { scroll = true } = {}) {
    const c = byId(id); if (!c) return;
    if (S.mode !== 'review') setMode('review');
    S.selected = id; tab('list');
    const vs = visible(); const pos = vs.findIndex((x) => x.id === id);
    const hex = STATUS[c._k].hex;
    const novelty = (c.comparator_ids || []).length ? `<div class="sec"><h5>与前作比较</h5><div class="box2">${rich(c.comparison || '')}</div></div>` : '';
    $('#detail').innerHTML = `<div class="dnav"><button class="back" id="back"><svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>全部意见</button><span class="sp"></span><span class="pos">${pos + 1} / ${vs.length}</span><button class="ic sm" id="prev" title="上一条 (K)"><svg viewBox="0 0 24 24"><path d="M6 15l6-6 6 6"/></svg></button><button class="ic sm" id="next" title="下一条 (J)"><svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg></button></div>
      <div class="dbody" style="--c:${hex}"><div class="badges"><span class="bd c">${esc(c.id)} · ${esc(STATUS[c._k].label)}</span><span class="bd">${esc(STATUS_ZH[c.status] || '')}</span><span class="bd">${esc(DIM_ZH[c.dimension] || '')}</span>${c.confidence ? `<span class="bd">置信度 ${esc({ high: '高', medium: '中', low: '低' }[c.confidence] || c.confidence)}</span>` : ''}</div>
      <h2>${tx(c.title)}</h2>${paras(c.body)}
      ${(c.evidence || []).length ? `<div class="sec"><h5>原文证据</h5>${c.evidence.map((e) => evHtml(e, hex)).join('')}</div>` : ''}
      ${novelty}
      ${c.countercheck ? `<div class="sec"><h5>反证检查</h5><div class="box2">${rich(c.countercheck)}</div></div>` : ''}
      ${c.request ? `<div class="sec"><h5>给作者的请求</h5><div class="box2">${rich(c.request)}</div></div>` : ''}</div>`;
    $('#list').hidden = true; $('.filterbar').hidden = true; $('#detail').hidden = false; $('#tab-list').scrollTop = 0;
    $('#back').onclick = back; $('#prev').onclick = () => step(-1); $('#next').onclick = () => step(1);
    $$('#detail .quote').forEach((q) => q.onclick = () => clickQuote(q, hex));
    markSelected(); renderThumbDots();
    if (scroll) { const e = (c.evidence || []).find((x) => x.source_id === MANU && x.page); if (e) revealQuote(e.page, e.quote, hex); else if (c.page) scrollToPage(c.page); }
  }
  async function clickQuote(q, hex) {
    if (q.dataset.src !== MANU) return;
    const ok = await revealQuote(+q.dataset.page, q.dataset.quote, hex);
    if (!ok && !q.querySelector('.miss')) q.querySelector('.src').insertAdjacentHTML('beforeend', ' · <span class="miss">未在文字层精确定位，已跳到该页</span>');
  }
  // Display-only paragraphing of long review prose (review.json is not changed):
  // explicit blank lines, (1)(2)… enumerations as lists, and long paragraphs
  // split at sentences that open a new step of the argument.
  const OPENERS = /^(因此|所以|但|然而|不过|另外|此外|同样|这说明|也就是说|换言之|总之|结果|论文|作者|现有|附录|正文|表\s*\d|图\s*\d|第\s*[\d一二三四五六七八九十]|一是|二是|三是|最后|其次|首先|若|如果)/;
  function rich(text) {
    const out = [];
    const sentences = (t) => t.match(/[^。！？]+[。！？]?[”’」）)]*/g) || [t];
    const para = (t) => {
      t = t.trim(); if (!t) return;
      if (t.length <= 110) { out.push(`<p>${tx(t)}</p>`); return; }
      let buf = '';
      sentences(t).forEach((x) => {
        const sx = x.trim();
        if (buf && ((OPENERS.test(sx) && buf.length >= 50) || (buf + sx).length > 220)) { out.push(`<p>${tx(buf)}</p>`); buf = ''; }
        buf += sx;
      });
      if (buf) { if (buf.length < 30 && out.length) out[out.length - 1] = out[out.length - 1].replace(/<\/p>$/, tx(buf) + '</p>'); else out.push(`<p>${tx(buf)}</p>`); }
    };
    String(text || '').split(/\n\s*\n|\n(?=[（(]\d+[)）])/).forEach((block) => {
      const marks = [...block.matchAll(/[（(](\d{1,2})[)）]\s*/g)];
      const seq = marks.length >= 2 && marks.every((m, i) => +m[1] === i + 1);
      if (!seq) { block.split(/\n/).forEach(para); return; }
      para(block.slice(0, marks[0].index));
      const items = marks.map((m, i) => block.slice(m.index + m[0].length, i + 1 < marks.length ? marks[i + 1].index : block.length).trim());
      let tail = ''; const last = items[items.length - 1]; const cut = last.search(/[。！？]/);
      if (cut >= 0 && cut < last.length - 1) { tail = last.slice(cut + 1); items[items.length - 1] = last.slice(0, cut + 1); }
      out.push('<ol>' + items.map((x) => `<li>${tx(x.replace(/[；;，,]\s*$/, ''))}</li>`).join('') + '</ol>');
      para(tail);
    });
    return out.join('');
  }
  const paras = rich;
  function back() { S.selected = null; $('#detail').hidden = true; $('#list').hidden = false; $('.filterbar').hidden = false; renderList(); markSelected(); }
  function step(d) { const vs = visible(); if (!vs.length) return; const i = vs.findIndex((x) => x.id === S.selected); const n = vs[(i + d + vs.length) % vs.length] || vs[0]; select(n.id); }
  function tab(name) { $$('#reviewTabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name)); $$('#pane-review .tab').forEach((t) => t.classList.toggle('on', t.id === 'tab-' + name)); }

  function renderOverall() {
    const o = R.overall || {};
    const subs = Object.entries(o.subscores || {}).map(([k, v]) => `<span>${esc({ soundness: '可靠性', presentation: '表达', contribution: '贡献' }[k] || k)} <b>${esc(v)}</b></span>`).join('');
    const dec = (o.decisive_ids || []).map((id) => { const c = byId(id); return c ? `<div><span class="link" data-id="${esc(id)}">${esc(id)}</span> ${tx(c.title)}</div>` : ''; }).join('');
    const dims = Object.entries(R.dimensions || {}).map(([k, v]) => `<tr><td>${esc(DIM_ZH[k] || k)}</td><td>${v.status === 'ok' ? '<span class="ok">通过</span>' : v.status === 'issues' ? (v.comment_ids || []).map((id) => `<span class="link" data-id="${esc(id)}">${esc(id)}</span>`).join(' ') : esc(v.status)}</td></tr>`).join('');
    $('#tab-overall').innerHTML = `<div class="ov"><div class="big"><div class="num">${esc(o.score ?? '–')}</div><div><div class="lab">${esc(o.recommendation || '')}</div><div class="sub">置信度 ${esc(o.confidence ?? '–')}/5 · ${esc(R.venue?.id || '')} ${esc(R.venue?.year || '')}</div></div></div>
      <div class="subs">${subs}</div>${paras(o.justification)}
      ${dec ? `<div class="sec"><h5>决定评分的意见</h5><div class="dec">${dec}</div></div>` : ''}
      ${o.would_change ? `<div class="sec"><h5>什么情况下评分会变</h5><div class="box2">${rich(o.would_change)}</div></div>` : ''}
      <div class="sec"><h5>九个维度</h5><table class="nums">${dims}</table></div></div>`;
    $$('#tab-overall .link').forEach((l) => l.onclick = () => select(l.dataset.id));
  }
  function renderCritique() {
    const f = R.critique?.findings || []; $('#nCritique').textContent = f.length;
    const zh = { accepted: '采纳', partial: '部分采纳', rejected: '未采纳' };
    $('#tab-critique').innerHTML = `<div class="crit">${R.critique?.summary ? `<div class="box2" style="margin:6px 0 10px">${tx(R.critique.summary)}</div>` : ''}${f.map((x) => `<div class="it"><div class="h"><b>${esc(x.id)}</b><span class="res ${esc(x.resolution)}">${esc(zh[x.resolution] || x.resolution || '未处理')}</span><span style="color:var(--ink3)">${esc(x.target || '')} · ${esc(x.source === 'lint' ? '确定性检查' : '独立复核')}</span></div><div class="d">${tx(x.detail)}</div>${x.response ? `<div class="r2">${tx(x.response)}</div>` : ''}</div>`).join('')}</div>`;
  }

  // ------------------------------------------------------------ side: digest
  function renderDigest() {
    const sm = R.summary || {}; const nov = R.novelty || []; const srcs = R.sources || [];
    $('#digestLegend').innerHTML = Object.values(DIGEST).map((d) => `<span><i style="background:${d.hex}66;border:1px solid ${d.hex}"></i>${d.label}</span>`).join('') + '<span style="color:var(--ink3)">正文中的高亮可点击</span>';
    const sec = (n, title, body, tag = '') => `<section class="dg" id="dg-${n}"><h3><span class="n">${n}</span>${esc(title)}${tag ? `<span class="tag">${esc(tag)}</span>` : ''}</h3>${body}</section>`;
    const empty = (t) => `<div class="empty">${esc(t)}</div>`;
    const abs = R.critique?.abstractions || [];
    // prior work: comparisons across contributions, unique by comparator
    const priors = []; const seen = new Set();
    nov.forEach((nv) => (nv.comparisons || []).forEach((cp) => { if (seen.has(cp.comparator_id)) return; seen.add(cp.comparator_id); priors.push({ ...cp, cid: nv.contribution_id }); }));
    const priorHtml = priors.map((p) => { const s = srcs.find((x) => x.id === p.comparator_id) || {};
      return `<details class="prior"><summary><span class="tt">${esc(s.title || p.comparator_id)}</span><span class="yr">${esc(s.arxiv_id ? 'arXiv ' + s.arxiv_id : '')} ${esc(s.version || '')} · ${esc(s.public_date || '')}</span></summary><dl class="pb">
        ${p.common_mechanism ? `<dt>与本文的共同点</dt><dd>${tx(p.common_mechanism)}</dd>` : ''}${p.differences ? `<dt>本文的不同之处</dt><dd>${tx(p.differences)}</dd>` : ''}
        ${(p.comparator_quotes || []).slice(0, 2).map((q) => `<div class="pq">“${esc(q.quote)}” <small>p.${esc(q.page)}</small></div>`).join('')}<dt>相关贡献</dt><dd>${esc(p.cid)}</dd></dl></details>`; }).join('');
    const contribs = (sm.contributions || []).map((k) => `<div class="contrib" id="contrib-${esc(k.id)}"><div class="hd"><span class="id">${esc(k.id)}</span><span class="cl">${tx(k.claim)}</span>${k.novelty_assessment ? `<span class="nov ${esc(k.novelty_assessment)}" title="审稿判断">${esc(NOV_ZH[k.novelty_assessment] || k.novelty_assessment)}</span>` : ''}</div>${k.quote ? `<button class="quote" style="--c:${DIGEST.contrib.hex}" data-src="${esc(MANU)}" data-page="${k.page}" data-quote="${esc(k.quote)}">“${esc(k.quote)}”<span class="src">作者原话 · p.${esc(k.page)} · 点击定位</span></button>` : ''}</div>`).join('');
    const nums = (R.numeric_checks || []).length ? `<table class="nums"><tr><th>核对项</th><th>复算</th><th>论文</th><th>出处</th></tr>${R.numeric_checks.map((n) => { const ok = n.claimed == null || Math.abs(n.claimed - n.value) <= Math.max(0.05, Math.abs(n.value) * 0.005); const e = (n.evidence || [])[0];
      return `<tr><td>${tx(n.claim)}</td><td>${esc(n.value)}</td><td class="${n.claimed == null ? '' : ok ? 'ok' : 'bad'}">${n.claimed == null ? '—' : esc(n.claimed)}</td><td>${e ? `<span class="link" data-q="${esc(e.quote)}" data-page="${e.page}">p.${esc(e.page)}</span>` : ''}</td></tr>`; }).join('')}</table>` : '';
    const expStrengths = comments.filter((c) => c._k === 'strength' && ['experiments', 'baselines'].includes(c.dimension));
    const verifyRows = [
      ...(sm.contributions || []).filter((k) => k.novelty_assessment).map((k) => `<div class="vrow"><span class="st" style="background:${k.novelty_assessment === 'overlap' ? 'var(--major)' : k.novelty_assessment === 'distinct' ? 'var(--strength)' : k.novelty_assessment === 'incremental' ? 'var(--minor)' : 'var(--ink3)'}">${esc(k.id)}</span><span>创新性：${esc(NOV_ZH[k.novelty_assessment] || k.novelty_assessment)}</span></div>`),
      ...comments.filter((c) => c._k === 'major').map((c) => `<div class="vrow"><span class="st" style="background:var(--major)">${esc(c.id)}</span><span><span class="link" data-id="${esc(c.id)}">${tx(c.title)}</span></span></div>`),
    ].join('');
    $('#digest').innerHTML = [
      sec(1, '一句话概括', sm.one_line ? `<div class="lead">${tx(sm.one_line)}</div>` : empty('未记录')),
      sec(2, '针对什么问题', sm.problem ? paras(sm.problem) : empty('未记录')),
      sec(3, '问题的意义与研究脉络', (abs.length ? `<p class="note" style="margin-top:0">这篇论文所属的经典问题（独立复核时归纳）：</p><div class="tagline">${abs.map((a) => `<span>${tx(a)}</span>`).join('')}</div>` : '') + `<div class="empty" style="margin-top:8px">v1.3 审稿流程没有单独记录“问题的意义”，这里只列出所属研究脉络。</div>`, '审稿归纳'),
      sec(4, '现有方法与相关前作', priors.length ? `<p class="note" style="margin-top:0">创新性检索中读过全文的截止日期前前作。这是审稿检索的结果，不是作者在相关工作中的归纳。</p>${priorHtml}` : empty('没有创新性比较记录'), '审稿检索'),
      sec(5, '提出的方法', sm.method ? paras(sm.method) : empty('未记录')),
      sec(6, '贡献与作者原话', contribs || empty('未记录'), '可定位原文'),
      sec(7, '实验做了什么', (sm.setup ? paras(sm.setup) : empty('未记录')) + (nums ? `<div class="sec"><h5>核对过的数字</h5>${nums}</div>` : '') + (expStrengths.length ? `<div class="sec"><h5>审稿认可的实验设计</h5>${expStrengths.map((c) => `<div class="vrow"><span class="st" style="background:var(--strength)">${esc(c.id)}</span><span><span class="link" data-id="${esc(c.id)}">${tx(c.title)}</span></span></div>`).join('')}</div>` : '')),
      sec(8, '审稿核实', `<p class="note" style="margin-top:0">以下是审稿判断，与上面作者的说法分开列出；点击编号查看完整意见。</p><div class="verify">${verifyRows}</div><div class="sec"><h5>总评</h5><div class="box2">${esc(R.overall?.score ?? '')} 分 · ${esc(R.overall?.recommendation || '')}</div></div>`, '审稿判断'),
    ].join('');
    $$('#digest .quote').forEach((q) => q.onclick = () => clickQuote(q, q.style.getPropertyValue('--c')));
    $$('#digest .link[data-id]').forEach((l) => l.onclick = () => select(l.dataset.id));
    $$('#digest .link[data-q]').forEach((l) => l.onclick = () => revealQuote(+l.dataset.page, l.dataset.q, DIGEST.exp.hex));
  }
  function gotoDigest(did) { if (S.mode !== 'digest') setMode('digest'); const el = did === 'nums' ? $('#dg-7') : $('#' + did); if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.animate([{ background: 'var(--brandSoft)' }, { background: 'transparent' }], { duration: 1400 }); } }

  // ------------------------------------------------------------ mode, chrome
  function setMode(m) {
    S.mode = m; $$('.mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === m));
    $('#pane-review').classList.toggle('on', m === 'review'); $('#pane-digest').classList.toggle('on', m === 'digest');
    if (m === 'digest') S.selected = null;
    repaintAll();
  }
  function header() {
    const p = R.paper || {}; const o = R.overall || {};
    $('#docTitle').textContent = p.title || '审稿阅读器'; document.title = (p.title || '审稿') + ' · 审稿阅读器';
    $('#docMeta').textContent = [R.venue ? `${R.venue.id} ${R.venue.year || ''}` : '', p.version, `${p.pages || ''} 页`, R.review_date ? `审稿 ${R.review_date}` : '', R.provenance?.rules_official ? '官方规则' : ''].filter(Boolean).join(' · ');
    $('#scoreChip').innerHTML = o.score != null ? `<b>${esc(o.score)}</b><span>${esc(o.recommendation || '')} · 置信度 ${esc(o.confidence)}</span>` : '';
  }
  function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); }
  function exportMd() {
    const o = R.overall || {}; const L = [`# ${R.paper?.title || ''}`, '', `**总评**：${o.score ?? ''}（${o.recommendation || ''}），置信度 ${o.confidence ?? ''}`, '', o.justification || '', ''];
    ORDER.forEach((k) => { const g = comments.filter((c) => c._k === k); if (!g.length) return; L.push(`## ${STATUS[k].label}`, ''); g.forEach((c) => { L.push(`### ${c.id} ${c.title}`, '', c.body || ''); (c.evidence || []).forEach((e) => L.push(`> “${e.quote}”（p.${e.page}）`)); if (c.request) L.push('', `**请求**：${c.request}`); L.push(''); }); });
    download('review.md', L.join('\n'), 'text/markdown');
  }
  function exportDigest() {
    const sm = R.summary || {}; const L = [`# 论文速览：${R.paper?.title || ''}`, '', `> ${sm.one_line || ''}`, '', '## 针对什么问题', '', sm.problem || '', '', '## 提出的方法', '', sm.method || '', '', '## 贡献', ''];
    (sm.contributions || []).forEach((k) => L.push(`- **${k.id}** ${k.claim}（创新性：${NOV_ZH[k.novelty_assessment] || '—'}）`, k.quote ? `  > “${k.quote}”（p.${k.page}）` : ''));
    L.push('', '## 实验', '', sm.setup || '', ''); (R.numeric_checks || []).forEach((n) => L.push(`- ${n.claim}：复算 ${n.value}${n.claimed != null ? `，论文 ${n.claimed}` : ''}`));
    download('paper_digest.md', L.filter((x) => x !== undefined).join('\n'), 'text/markdown');
  }
  function wire() {
    $$('.mode button').forEach((b) => b.onclick = () => setMode(b.dataset.mode));
    $$('#reviewTabs button').forEach((b) => b.onclick = () => tab(b.dataset.tab));
    $('#q').oninput = (e) => { S.q = e.target.value.trim().toLowerCase(); renderList(); };
    $('#btnSearch').onclick = () => { setMode('review'); tab('list'); back(); $('#q').focus(); };
    const dark = store.get('dark', matchMedia('(prefers-color-scheme: dark)').matches); document.documentElement.classList.toggle('dark', dark);
    $('#btnTheme').onclick = () => { const d = !document.documentElement.classList.contains('dark'); document.documentElement.classList.toggle('dark', d); store.set('dark', d); };
    $('#btnMenu').onclick = (e) => { e.stopPropagation(); $('#menu').classList.toggle('open'); };
    document.addEventListener('click', () => $('#menu').classList.remove('open'));
    $$('#menu .items button').forEach((b) => b.onclick = () => { const a = b.dataset.act; if (a === 'md') exportMd(); if (a === 'json') download('review.json', JSON.stringify(R, null, 1), 'application/json'); if (a === 'digest') exportDigest(); if (a === 'help') $('#help').hidden = false; });
    $('#helpClose').onclick = () => $('#help').hidden = true;
    $('#zoomIn').onclick = () => setZoom(Math.min(3, +(S.scale * 1.15).toFixed(2)));
    $('#zoomOut').onclick = () => setZoom(Math.max(0.4, +(S.scale / 1.15).toFixed(2)));
    $('#zoomLbl').onclick = () => setZoom('fit');
    $('#pageInput').onchange = (e) => scrollToPage(Math.max(1, Math.min(S.base.length, parseInt(e.target.value, 10) || 1)));
    $('#hlToggle').onchange = (e) => $('#viewer').classList.toggle('hidehl', !e.target.checked);
    $('#viewer').addEventListener('scroll', () => requestAnimationFrame(trackCurrent), { passive: true });
    $('#btnRail').onclick = () => $('#rail').classList.add('closed'); $('#railOpen').onclick = () => $('#rail').classList.remove('closed');
    let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => S.zoom === 'fit' && setZoom('fit'), 200); });
    // gutter drag
    const g = $('#gutter'); const w0 = store.get('sideW', null); if (w0) document.documentElement.style.setProperty('--side-w', w0 + 'px');
    g.onmousedown = (e) => { e.preventDefault(); const mv = (ev) => { const w = Math.max(360, Math.min(innerWidth * 0.7, innerWidth - ev.clientX)); document.documentElement.style.setProperty('--side-w', w + 'px'); store.set('sideW', w); }; const up = () => { removeEventListener('mousemove', mv); removeEventListener('mouseup', up); if (S.zoom === 'fit') setZoom('fit'); }; addEventListener('mousemove', mv); addEventListener('mouseup', up); };
    addEventListener('keydown', (e) => {
      if (e.target.matches('input,textarea')) { if (e.key === 'Escape') e.target.blur(); return; }
      const k = e.key;
      if (k === 'm' || k === 'M') setMode(S.mode === 'review' ? 'digest' : 'review');
      else if (k === 'j' || k === 'J') step(1); else if (k === 'k' || k === 'K') step(-1);
      else if (k === 'Escape') { if (!$('#help').hidden) $('#help').hidden = true; else back(); }
      else if (k === '/') { e.preventDefault(); $('#btnSearch').click(); }
      else if (k === 'h' || k === 'H') { const c = $('#hlToggle'); c.checked = !c.checked; c.onchange({ target: c }); }
      else if (k === '[') $('#rail').classList.toggle('closed'); else if (k === ']') { $('#side').classList.toggle('closed'); $('#gutter').hidden = $('#side').classList.contains('closed'); setTimeout(() => S.zoom === 'fit' && setZoom('fit'), 50); }
      else if (k === '+' || k === '=') $('#zoomIn').click(); else if (k === '-') $('#zoomOut').click(); else if (k === '0') setZoom('fit');
      else if (k === '?') $('#help').hidden = false;
    });
  }

  async function init() {
    header(); wire(); renderChips(); renderList(); renderOverall(); renderCritique(); renderDigest();
    if (!EMBED.pdf) { $('#pages').innerHTML = '<p class="note" style="padding:40px">没有内嵌 PDF。请用 scripts/package_viewer.py 生成单文件版本。</p>'; return; }
    pdfjsLib.GlobalWorkerOptions.workerSrc = EMBED.workerUrl;
    const bin = atob(EMBED.pdf); const bytes = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    S.pdf = await pdfjsLib.getDocument({ data: bytes }).promise;
    for (let i = 1; i <= S.pdf.numPages; i++) { const p = await S.pdf.getPage(i); S.base.push({ page: p, vp: p.getViewport({ scale: 1 }) }); }
    $('#pageTotal').textContent = '/ ' + S.pdf.numPages;
    buildPages(); buildThumbs();
    window.__BPR2__ = { S, rectsFor, anchorSpecs, select, setMode };
  }
  init().catch((e) => { console.error(e); $('#pages').innerHTML = `<p class="note" style="padding:40px">加载失败：${esc(e.message)}</p>`; });
})();
