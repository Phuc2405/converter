/* Dịch vụ Sách: danh sách sách, thêm sách, đọc bản chữ một mình hoặc cạnh bản gốc. */
const S = { books: [], cur: null, mode: store.get('mode', 'split') };

async function loadList() {
  const d = await api('/api/books');
  S.books = d.books;
  drawList();
  const busy = d.books.some((b) => b.job && !['done', 'error'].includes(b.job.state));
  clearTimeout(S.poll);
  if (busy) S.poll = setTimeout(loadList, 2000); // còn sách đang chuyển: hỏi lại sau 2 giây
  // sách đang mở vừa có bản chữ: mở lại
  if (S.cur && !S.curLoaded && S.books.find((b) => b.name === S.cur)?.text) openBook(S.cur);
}

function drawList() {
  const f = $('#filter').value.trim().toLowerCase();
  const rows = S.books.filter((b) => !f || b.name.toLowerCase().includes(f));
  $('#list').innerHTML = rows.map((b) => `<button type="button" class="item ${b.name === S.cur ? 'on' : ''}" data-name="${esc(b.name)}">
      <span class="t"><b>${esc(b.name)}</b>
        <small>${esc((b.ext || '').toUpperCase())}${b.ocr ? ' · bản chữ OCR' : ''}${!b.text && !b.job ? ' · chưa có bản chữ' : ''}</small>
        ${jobLine(b.job)}</span></button>`).join('') || `<p class="hint">${S.books.length ? 'Không có sách nào khớp.' : 'Chưa có sách. Bấm “＋ Thêm sách”.'}</p>`;
}

async function openBook(name) {
  S.cur = name; S.curLoaded = false;
  store.set('cur', name);
  history.replaceState(null, '', '#' + encodeURIComponent(name)); // link riêng của từng cuốn
  drawList();
  const b = S.books.find((x) => x.name === name);
  if (!b?.text) {
    $('#main').innerHTML = `<div class="empty"><p><b>${esc(name)}</b></p>${b?.job?.state === 'error'
      ? `<p class="bad">Tạo bản chữ bị lỗi.</p><pre class="hint">${esc(b.job.msg)}</pre><button type="button" class="btn" id="retry">Thử lại</button>`
      : '<p>Đang tạo bản chữ, xong sẽ tự mở.</p>'}</div>`;
    $('#modeSeg').hidden = true; $('#syncBtn').hidden = true;
    const r = $('#retry');
    if (r) r.onclick = async () => { await api('/api/retry', { name }); loadList(); };
    return;
  }
  let d;
  try { d = await api('/api/book?name=' + encodeURIComponent(name)); } catch (e) { return toast(e.message); }
  S.curLoaded = true; S.doc = d;
  draw();
}

// PDF và .txt xem được trong trình duyệt; Word, PowerPoint thì không.
const canShow = (ext) => ['pdf', 'txt', 'md', 'html'].includes(ext);

function draw() {
  const d = S.doc;
  const split = S.mode === 'split' && d.orig;
  $('#modeSeg').hidden = !d.orig;
  $$('#modeSeg button').forEach((b) => b.classList.toggle('on', b.dataset.mode === (split ? 'split' : 'text')));
  $('#syncBtn').hidden = !(split && d.ext === 'pdf');
  const orig = !split ? '' : canShow(d.ext)
    ? `<section class="pane"><div class="pane-head"><span class="tag">Bản gốc</span><b>${esc(d.orig)}</b></div>
       <iframe class="frame" id="frame" src="/goc/${encodeURIComponent(d.orig)}" title="Bản gốc"></iframe></section>`
    : `<section class="pane"><div class="empty"><p>Trình duyệt không hiện được file .${esc(d.ext)}.</p><p class="hint">Mở file ${esc(d.orig)} trong data/goc bằng ứng dụng trên máy.</p></div></section>`;
  $('#main').innerHTML = `${orig}<section class="pane"><div class="pane-head"><span class="tag">Bản chữ</span><b>${esc(d.name)}</b></div>
    <div class="scroll" id="scroll"><article class="text" id="text">${renderText(d.text)}</article></div></section>`;
  const pos = store.get('pos:' + d.name, 0);
  if (pos) $('#scroll').scrollTop = pos;
  $('#scroll').addEventListener('scroll', () => store.set('pos:' + d.name, $('#scroll').scrollTop), { passive: true });
}

/* Bản chữ: chữ thường, đề mục "#", in đậm "**", dấu trang <!-- page N --> thành vạch "Trang N" (bấm để bản gốc tới trang đó).
   Mọi chữ đều qua esc() trước, nên nội dung sách không chạy được mã. */
function renderText(src) {
  const out = [];
  let para = [];
  const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  for (const raw of src.split(/\r?\n/)) {
    const line = raw.trim();
    const page = /^<!--\s*page\s+(\d+)\s*-->$/i.exec(line);
    if (page) { flush(); out.push(`<div class="page" data-p="${page[1]}"><button type="button" data-page="${page[1]}" title="Bản gốc tới trang này">Trang ${page[1]}</button></div>`); continue; }
    const note = /^<!--\s*(.*?)\s*-->$/.exec(line);
    if (note) { flush(); out.push(`<p class="note">${esc(note[1])}</p>`); continue; }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) { flush(); const n = h[1].length; out.push(`<h${n}>${inline(h[2])}</h${n}>`); continue; }
    if (!line) { flush(); continue; }
    para.push(line);
  }
  flush();
  return out.join('\n');
}

function goPage(n) {
  const f = $('#frame');
  if (!f || S.doc.ext !== 'pdf') return;
  f.src = 'about:blank'; // đổi mỗi phần #page thì trình đọc PDF không nhảy: nạp lại
  setTimeout(() => { f.src = `/goc/${encodeURIComponent(S.doc.orig)}#page=${n}`; }, 30);
}
// Trang đang đọc: vạch "Trang N" cuối cùng nằm trên mép trên của khung chữ.
function pageInView() {
  const top = $('#scroll').getBoundingClientRect().top + 40;
  let n = null;
  for (const p of $$('#text .page')) { if (p.getBoundingClientRect().top <= top) n = p.dataset.p; else break; }
  return n || $('#text .page')?.dataset.p;
}

$('#main').addEventListener('click', (e) => {
  const b = e.target.closest('[data-page]');
  if (!b) return;
  if (S.mode !== 'split') { S.mode = 'split'; store.set('mode', 'split'); draw(); }
  goPage(b.dataset.page);
});
$('#syncBtn').onclick = () => { const n = pageInView(); if (n) goPage(n); else toast('Bản chữ này không có dấu trang.'); };
$('#modeSeg').onclick = (e) => {
  const b = e.target.closest('[data-mode]');
  if (!b || !S.doc) return;
  const keep = $('#scroll')?.scrollTop || 0;
  S.mode = b.dataset.mode; store.set('mode', S.mode);
  draw();
  $('#scroll').scrollTop = keep;
};
$('#list').onclick = (e) => { const it = e.target.closest('.item'); if (it) openBook(it.dataset.name); };
$('#filter').oninput = drawList;
wireUpload((d) => { S.cur = d.name; loadList().then(() => openBook(d.name)); });

// Mở lại cuốn đang đọc: theo link (#tên sách, ?mode=text|split) hoặc cuốn mở lần trước.
const qmode = new URLSearchParams(location.search).get('mode');
if (qmode === 'text' || qmode === 'split') S.mode = qmode;
loadList().then(() => {
  const c = decodeURIComponent(location.hash.slice(1)) || store.get('cur', null);
  if (c && S.books.some((b) => b.name === c)) openBook(c);
}).catch((e) => toast(e.message, 8000));
