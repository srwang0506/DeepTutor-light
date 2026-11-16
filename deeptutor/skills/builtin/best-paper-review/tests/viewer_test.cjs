// Browser regression test for the viewer (served + packaged offline modes).
// Usage: node tests/viewer_test.cjs [outDir]
// Needs: Node, playwright (+ Chromium; set CHROME_PATH to use a system Chrome), python3.
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs'); const path = require('path'); const os = require('os');

const ROOT = path.resolve(__dirname, '..');
const OUT = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'bpr-viewer-'));
const results = []; const ok = (name, cond, extra = '') => { results.push({ name, pass: !!cond, extra }); console.log((cond ? 'PASS ' : 'FAIL ') + name + (extra ? ' :: ' + extra : '')); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // work on a copy of the demo so that saving does not modify the repo example
  const work = path.join(OUT, 'demo'); fs.cpSync(path.join(ROOT, 'examples/demo'), work, { recursive: true });
  for (const f of ['review.md', 'review.html', 'form.md']) fs.rmSync(path.join(work, f), { force: true });
  const port = 8791;
  const srv = spawn('python3', [path.join(ROOT, 'scripts/serve.py'), '--pdf', path.join(work, 'paper.pdf'), '--review', path.join(work, 'review.json'), '--port', String(port)], { stdio: 'ignore' });
  await sleep(1200);
  const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {});
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, acceptDownloads: true });
  const page = await ctx.newPage(); const errors = [];
  page.on('pageerror', (e) => errors.push(String(e))); page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  try {
    await page.goto(`http://127.0.0.1:${port}/`); await page.waitForSelector('#list .card', { timeout: 20000, state: 'attached' }); await page.waitForSelector('.page canvas');
    await sleep(800);
    ok('served: 4 pages', (await page.$$('.page')).length === 4);
    ok('served: 10 comment cards', (await page.$$('#list .card')).length === 10);
    ok('served: 8 region overlays', (await page.$$('.region')).length === 8, String((await page.$$('.region')).length));
    ok('served: save button visible', await page.isVisible('#btnSave'));
    ok('served: injection banner', (await page.textContent('#banner')).includes('提示注入'));
    const evs = await page.evaluate(() => { const S = BPR.state; let n = 0, f = 0; S.review.comments.forEach((c) => (c.evidence || []).forEach((e) => { n++; if (BPR.evState(e).state === 'found') f++; })); return [n, f]; });
    ok('served: all evidence quotes verified in browser', evs[0] > 0 && evs[0] === evs[1], evs.join('/'));
    ok('groups: collapsed by default', (await page.$$('#list .group-sec.shut')).length === (await page.$$('#list .group-sec')).length && !(await page.isVisible('.card[data-id="W01"]')));
    for (const h of await page.$$('#list .group-sec.shut .ghead')) await h.click();
    await page.click('.card[data-id="W01"]'); await sleep(900);
    ok('select: reading view opens with full content', await page.isVisible('#detail .card.sel .more') && !(await page.isVisible('#list')));
    ok('select: list cards stay compact', (await page.$$('#list .card .more')).length === 0);
    ok('select: region highlighted', !!(await page.$('.region.sel[data-id="W01"]')));
    ok('select: quote highlighted in text layer', (await page.$$('.textLayer span.hl')).length > 0);
    ok('select: evidence boxes drawn on PDF', (await page.$$('.evbox')).length > 0);
    const pageCmt = await page.evaluate(() => (BPR.state.review.comments.find((c) => c.scope === 'page' && (c.evidence || []).length) || {}).id);
    if (pageCmt) { await page.evaluate((id) => BPR.select(id), pageCmt); await sleep(1500);
      const vis = await page.evaluate(() => { const v = document.querySelector('#viewer').getBoundingClientRect(); return [...document.querySelectorAll('.evbox,.pflag')].some((b) => { const r = b.getBoundingClientRect(); return r.bottom > v.top && r.top < v.bottom; }); });
      ok('page-level comment: PDF scrolls to a visible evidence mark', vis, pageCmt); await page.evaluate(() => BPR.select('W01')); await sleep(600); }
    await page.screenshot({ path: path.join(OUT, 'viewer-selected.png') });
    const pos1 = await page.textContent('#detail .pos'); await page.click('#btnNext'); await sleep(500);
    ok('reading: next comment', (await page.textContent('#detail .pos')) !== pos1, pos1);
    await page.screenshot({ path: path.join(OUT, 'viewer-reading.png') });
    const w0 = await page.evaluate(() => document.querySelector('#side').getBoundingClientRect().width);
    await page.click('#btnWide'); await sleep(400);
    const w1 = await page.evaluate(() => document.querySelector('#side').getBoundingClientRect().width);
    ok('wide: side panel widens', w1 > w0 + 100, `${w0}->${w1}`);
    await page.click('#btnWide'); await sleep(300);
    await page.click('#btnBack'); await sleep(200);
    ok('reading: back to list', await page.isVisible('#list') && !(await page.isVisible('#detail')));
    ok('filters: extra selects collapsed by default', !(await page.isVisible('#fSev')));
    await page.click('#btnFilters');
    await page.selectOption('#fSev', 'major'); await sleep(200);
    ok('filter: major only', (await page.$$('#list .card')).length === 5, String((await page.$$('#list .card')).length));
    await page.selectOption('#fSev', ''); await page.fill('#q', 'ThreshSkip'); await sleep(200);
    ok('search: ThreshSkip', (await page.$$('#list .card')).length >= 1);
    await page.fill('#q', '');
    // draw a new region on page 3
    await page.click('#btnRegion'); const p3 = await page.$('.page[data-page="3"]'); await p3.scrollIntoViewIfNeeded(); await sleep(400);
    const bb = await p3.boundingBox();
    await page.mouse.move(bb.x + bb.width * 0.2, bb.y + bb.height * 0.5); await page.mouse.down();
    await page.mouse.move(bb.x + bb.width * 0.6, bb.y + bb.height * 0.6, { steps: 6 }); await page.mouse.up();
    await page.waitForSelector('#editor[open]');
    await page.fill('#editForm [name=title]', '测试：新框选意见'); await page.fill('#editForm [name=body]', '浏览器回归测试创建的区域意见。');
    await page.selectOption('#editForm [name=status]', 'question');
    await page.click('#edOk'); await sleep(300);
    const nw = await page.evaluate(() => BPR.state.review.comments.find((c) => c.title === '测试：新框选意见'));
    ok('draw: new region comment created', nw && nw.scope === 'region' && nw.page === 3 && nw.rect[2] > 0.3, JSON.stringify(nw && nw.rect));
    ok('draw: dirty state', (await page.textContent('#saveState')).includes('未保存'));
    // validation in editor: confirmed without evidence must be refused
    await page.click('#btnAddGlobal'); await page.waitForSelector('#editor[open]');
    await page.fill('#editForm [name=title]', 'x'); await page.fill('#editForm [name=body]', 'y'); await page.selectOption('#editForm [name=status]', 'confirmed');
    await page.click('#edOk'); await sleep(150);
    ok('editor: refuses confirmed without evidence', (await page.textContent('#edErr')).includes('引文'));
    await page.click('#editForm button[value=cancel]');
    // undo then redo-by-reapply
    const before = await page.evaluate(() => BPR.state.review.comments.length);
    await page.click('#btnUndo'); await sleep(150);
    ok('undo removes new comment', (await page.evaluate(() => BPR.state.review.comments.length)) === before - 1);
    await page.click('#btnUndo').catch(() => {});
    // re-add and save to disk
    await page.click('#btnAddPage'); await page.waitForSelector('#editor[open]');
    await page.fill('#editForm [name=title]', '测试：保存到磁盘'); await page.fill('#editForm [name=body]', '页级意见'); await page.selectOption('#editForm [name=status]', 'suggestion');
    await page.click('#edOk'); await sleep(200);
    await page.click('#btnSave'); await sleep(800);
    const disk = JSON.parse(fs.readFileSync(path.join(work, 'review.json'), 'utf8'));
    ok('save: written to disk', disk.comments.some((c) => c.title === '测试：保存到磁盘'));
    ok('save: backup created', fs.existsSync(path.join(work, '.backups')) && fs.readdirSync(path.join(work, '.backups')).length >= 1);
    ok('save: validation shown', (await page.textContent('#banner')).includes('已保存'));
    // exports
    const [dl] = await Promise.all([page.waitForEvent('download'), page.evaluate(() => BPR.exportPdf())]);
    const pdfOut = path.join(OUT, 'annotated.pdf'); await dl.saveAs(pdfOut);
    const annots = execFileSync('python3', ['-c', `import pypdf,sys;r=pypdf.PdfReader(sys.argv[1]);print(sum(len(p.get('/Annots') or []) for p in r.pages));print([str(a.get_object().get('/Contents'))[:30] for a in (r.pages[0].get('/Annots') or [])][:2])`, pdfOut]).toString();
    ok('export: annotated PDF has one annotation per comment', +annots.split('\n')[0] === disk.comments.length, annots.trim().replace(/\n/g, ' | '));
    const [dj] = await Promise.all([page.waitForEvent('download'), page.evaluate(() => BPR.exportJson())]);
    const jOut = path.join(OUT, 'export.json'); await dj.saveAs(jOut);
    ok('export: JSON parses', JSON.parse(fs.readFileSync(jOut, 'utf8')).schema_version === 2);
    const [dm] = await Promise.all([page.waitForEvent('download'), page.evaluate(() => BPR.exportMd())]);
    const mOut = path.join(OUT, 'export.md'); await dm.saveAs(mOut);
    ok('export: Markdown', fs.readFileSync(mOut, 'utf8').includes('## 主要问题'));
    // tabs
    await page.click('#tabs button[data-tab=overall]'); ok('tab: overall', (await page.textContent('#overallPanel')).includes('决定评分'));
    await page.click('#tabs button[data-tab=dims]'); ok('tab: dims has 9 rows', (await page.$$('#dimsPanel tr')).length === 10);
    await page.click('#tabs button[data-tab=sources]'); ok('tab: sources', (await page.textContent('#sourcesPanel')).includes('threshskip'));
    await page.screenshot({ path: path.join(OUT, 'viewer-tabs.png') });
    // packaged offline viewer (file://)
    const html = path.join(OUT, 'packaged.html');
    execFileSync('python3', [path.join(ROOT, 'scripts/package_viewer.py'), path.join(ROOT, 'examples/demo/paper.pdf'), path.join(ROOT, 'examples/demo/review.json'), html, '--editor']);
    const p2 = await ctx.newPage(); const err2 = []; p2.on('pageerror', (e) => err2.push(String(e)));
    await p2.goto('file://' + html); await p2.waitForSelector('#list .card', { timeout: 20000, state: 'attached' }); await p2.waitForSelector('.page canvas'); await sleep(800);
    ok('packaged: loads offline with 10 cards', (await p2.$$('#list .card')).length === 10);
    ok('packaged: 4 pages rendered', (await p2.$$('.page')).length === 4);
    const ev2 = await p2.evaluate(() => { let n = 0, f = 0; BPR.state.review.comments.forEach((c) => (c.evidence || []).forEach((e) => { n++; if (BPR.evState(e).state === 'found') f++; })); return [n, f]; });
    ok('packaged: quotes verified', ev2[0] === ev2[1] && ev2[0] > 0, ev2.join('/'));
    ok('packaged: no save button', !(await p2.isVisible('#btnSave')));
    await p2.screenshot({ path: path.join(OUT, 'viewer-packaged.png') });
    ok('packaged: no page errors', err2.length === 0, err2.join(' | '));
    // PDF identity mismatch blocks editing
    const bad = JSON.parse(fs.readFileSync(path.join(ROOT, 'examples/demo/review.json'), 'utf8')); bad.paper.pdf_sha256 = '0'.repeat(64);
    fs.writeFileSync(path.join(OUT, 'bad.json'), JSON.stringify(bad));
    const p3b = await ctx.newPage(); await p3b.goto(`http://127.0.0.1:${port}/`); await p3b.waitForSelector('#list .card', { state: 'attached' });
    await p3b.setInputFiles('#fileJson', path.join(OUT, 'bad.json')); await sleep(500);
    ok('identity: mismatch banner + read-only', (await p3b.textContent('#banner')).includes('不一致') && (await p3b.evaluate(() => BPR.state.readOnly)));
    ok('served: no page errors', errors.length === 0, errors.join(' | '));
  } catch (e) { ok('unexpected exception', false, String(e.stack || e)); }
  finally {
    await browser.close(); srv.kill();
    fs.writeFileSync(path.join(OUT, 'viewer-results.json'), JSON.stringify({ when: new Date().toISOString(), results }, null, 2));
    const failed = results.filter((r) => !r.pass).length; console.log(`\n${results.length - failed}/${results.length} passed; artifacts in ${OUT}`); process.exit(failed ? 1 : 0);
  }
})();
