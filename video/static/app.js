/* Dịch vụ Video: danh sách video, thêm, xóa; xem video cạnh lời. Lời chạy theo video (tô chữ đang đọc), bấm mốc giờ hoặc bấm một chữ là tua. */
const S = { videos: [], cur: null };

async function loadList() {
  const d = await api('/api/videos');
  S.videos = d.videos;
  drawList();
  const busy = d.videos.some((v) => v.job && !['done', 'error'].includes(v.job.state));
  $('#topHint').textContent = busy ? 'Đang chép lời ở nền, từng video một.' : '';
  clearTimeout(S.poll);
  if (busy) S.poll = setTimeout(loadList, 2500);
  if (S.cur && !S.curText && S.videos.find((v) => v.name === S.cur)?.text) openVideo(S.cur); // vừa chép lời xong: mở lại
}

function drawList() {
  const f = $('#filter').value.trim().toLowerCase();
  const rows = S.videos.filter((v) => !f || v.name.toLowerCase().includes(f));
  $('#list').innerHTML = rows.map((v) => `<div class="item ${v.name === S.cur ? 'on' : ''}" data-name="${esc(v.name)}" role="button" tabindex="0">
      <span class="t"><b>${esc(v.name)}</b>
        <small>${(v.size / 1048576).toFixed(0)} MB${v.text ? (v.words ? ' · có lời, chữ chạy theo video' : ' · có lời') : !v.job ? ' · chưa có lời' : ''}</small>
        ${jobLine(v.job)}</span>
      <button type="button" class="x" data-del="${esc(v.name)}" title="Xóa video này" aria-label="Xóa video">✕</button></div>`).join('')
    || `<p class="hint">${S.videos.length ? 'Không có video nào khớp.' : 'Chưa có video. Bấm “＋ Thêm video”.'}</p>`;
}

async function openVideo(name) {
  S.cur = name; store.set('cur', name);
  history.replaceState(null, '', '#' + encodeURIComponent(name));
  drawList();
  let d;
  try { d = await api('/api/video?name=' + encodeURIComponent(name)); } catch (e) { return toast(e.message); }
  S.doc = d; S.curText = !!d.text;
  const v = S.videos.find((x) => x.name === name);
  const textHtml = d.text ? `<article class="text" id="text">${renderText(d.text)}</article>`
    : `<div class="empty">${v?.job?.state === 'error' ? `<p class="bad">Chép lời bị lỗi.</p><pre class="hint">${esc(v.job.msg)}</pre><button type="button" class="btn" id="retry">Thử lại</button>`
      : '<p>Đang chép lời, xong sẽ tự hiện ở đây. Trong lúc chờ vẫn xem video được.</p>'}</div>`;
  $('#main').className = 'main has-video';
  $('#main').innerHTML = `<section class="player">
      <video id="vid" controls preload="metadata" src="/media/${encodeURIComponent(d.file)}"></video>
      ${d.sound ? `<audio id="snd" preload="auto" src="/media/_ti%E1%BA%BFng/${encodeURIComponent(d.sound)}"></audio>` : ''}</section>
    <section class="pane"><div class="pane-head"><span class="tag">Lời</span><b>${esc(d.name)}</b></div>
      <div class="scroll" id="scroll">${textHtml}</div></section>`;
  const r = $('#retry');
  if (r) r.onclick = async () => { await api('/api/retry', { name }); loadList(); };
  if (d.sound) syncSound($('#vid'), $('#snd'));
  if (d.text) wireWords();
}

/* Lời: mỗi đoạn mở đầu bằng **[mm:ss]** thành nút tua; chữ của đoạn tách thành <span class="w"> để tô và bấm.
   Dòng "# tên", "- **Nguồn** …" hiện nhỏ ở đầu. Mọi chữ qua esc(), nội dung không chạy được mã. */
function renderText(src) {
  const toSec = (s) => s.split(':').reduce((a, x) => a * 60 + +x, 0);
  const out = [];
  for (const raw of src.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || /^#\s/.test(line)) continue;
    const m = /^\*\*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*\*\s*(.*)$/.exec(line);
    if (m) {
      const words = m[2].split(/\s+/).filter(Boolean).map((w) => `<span class="w">${esc(w)}</span>`).join(' ');
      out.push(`<p data-t="${toSec(m[1])}"><button type="button" class="ts" data-t="${toSec(m[1])}" title="Tua tới ${m[1]}">${m[1]}</button>${words}</p>`);
    } else {
      out.push(`<p class="meta">${esc(line.replace(/\*\*/g, ''))}</p>`);
    }
  }
  return out.join('\n');
}

/* Giờ của từng chữ: từ data/word-times (tools/word_times.py: nghe lại video, khớp từng chữ với lời).
   Chưa có thì ước theo độ dài chữ, rải đều từ đầu đoạn tới đầu đoạn sau. */
function wireWords() {
  const vid = $('#vid'), paras = $$('#text p[data-t]'), words = [];
  paras.forEach((p, i) => {
    const spans = $$('.w', p), t0 = +p.dataset.t, next = paras[i + 1] ? +paras[i + 1].dataset.t : null;
    const tt = S.doc.words?.[String(t0)];
    if (tt?.length) {
      const f = spans.length === tt.length ? (j) => tt[j] : (j) => tt[Math.round(j * (tt.length - 1) / Math.max(1, spans.length - 1))];
      spans.forEach((s, j) => { s._t = f(j); });
    } else {
      const len = spans.reduce((a, s) => a + s.textContent.length + 1, 0), span = (next ?? t0 + len / 14) - t0;
      let acc = 0;
      spans.forEach((s) => { s._t = t0 + span * acc / len; acc += s.textContent.length + 1; });
    }
    for (const s of spans) s.title = 'Bấm để video chạy từ chữ này';
    words.push(...spans);
  });
  const seek = (t) => { vid.currentTime = Math.max(0, t); vid.play().catch(() => {}); };
  $('#text').addEventListener('click', (e) => {
    const b = e.target.closest('.ts');
    if (b) return seek(+b.dataset.t);
    const w = e.target.closest('.w');
    if (w && !String(getSelection()).trim()) seek(w._t + 0.02); // đúng từ chữ được bấm, không lùi (lùi thì đọc cả chữ trước)
  });
  const scroller = $('#scroll');
  let lastP = null, lastW = null, raf = 0, userScroll = 0;
  scroller.addEventListener('wheel', () => { userScroll = Date.now(); }, { passive: true }); // tự cuộn thì 6 giây không cuộn theo
  const tick = () => {
    const now = vid.currentTime;
    let p = null;
    for (const x of paras) { if (+x.dataset.t <= now + 0.3) p = x; else break; }
    if (p !== lastP) { lastP?.classList.remove('speaking'); p?.classList.add('speaking'); lastP = p; }
    let lo = 0, hi = words.length - 1, k = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (words[m]._t <= now + 0.05) { k = m; lo = m + 1; } else hi = m - 1; }
    const w = k >= 0 ? words[k] : null;
    if (w !== lastW) {
      lastW?.classList.remove('said'); w?.classList.add('said'); lastW = w;
      if (w && !vid.paused && Date.now() - userScroll > 6000) {
        const r = w.getBoundingClientRect(), box = scroller.getBoundingClientRect();
        if (r.top < box.top + 60 || r.bottom > box.bottom - 60) w.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    }
  };
  const loop = () => { tick(); raf = vid.paused ? 0 : requestAnimationFrame(loop); };
  vid.addEventListener('play', () => { if (!raf) loop(); });
  vid.addEventListener('timeupdate', tick);
}

// Video tải về chỉ có hình: phần tiếng riêng chạy theo video (phát, dừng, tua, tốc độ, âm lượng).
function syncSound(v, a) {
  const near = () => { if (Math.abs(a.currentTime - v.currentTime) > 0.25) a.currentTime = v.currentTime; };
  v.addEventListener('play', () => { near(); a.play().catch(() => {}); });
  v.addEventListener('pause', () => a.pause());
  v.addEventListener('seeking', () => { a.currentTime = v.currentTime; });
  v.addEventListener('waiting', () => a.pause());
  v.addEventListener('playing', () => { near(); a.play().catch(() => {}); });
  v.addEventListener('ratechange', () => { a.playbackRate = v.playbackRate; });
  v.addEventListener('volumechange', () => { a.volume = v.volume; a.muted = v.muted; });
  v.addEventListener('timeupdate', near);
}

async function delVideo(name) {
  if (!confirm(`Xóa video “${name}”?\nVideo, lời và giờ từng chữ được chuyển vào data/_da-xoa (xóa hẳn thì xóa thư mục đó).`)) return;
  if (S.cur === name && $('#vid')) { // video đang mở: nhả file ra trước, Windows không cho chuyển file đang đọc
    $('#vid').pause(); $('#vid').removeAttribute('src'); $('#vid').load();
    $('#snd')?.removeAttribute('src');
    await new Promise((r) => setTimeout(r, 400));
  }
  try { await api('/api/delete', { name }); } catch (e) { return toast(e.message, 6000); }
  toast(`Đã xóa “${name}”.`);
  if (S.cur === name) { S.cur = null; S.doc = null; store.set('cur', null); history.replaceState(null, '', location.pathname); $('#main').className = 'main'; $('#main').innerHTML = '<div class="empty"><p>Chọn một video ở cột trái.</p></div>'; }
  loadList();
}

$('#list').addEventListener('click', (e) => {
  const del = e.target.closest('[data-del]');
  if (del) { e.stopPropagation(); return delVideo(del.dataset.del); }
  const it = e.target.closest('.item');
  if (it) openVideo(it.dataset.name);
});
$('#list').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.classList.contains('item')) openVideo(e.target.dataset.name); });
$('#filter').oninput = drawList;
wireUpload((d) => { loadList().then(() => openVideo(d.name)); });

loadList().then(() => {
  const c = decodeURIComponent(location.hash.slice(1)) || store.get('cur', null);
  if (c && S.videos.some((v) => v.name === c)) openVideo(c);
}).catch((e) => toast(e.message, 8000));
