/* Phần dùng chung của hai dịch vụ Sách và Video: chọn phần tử, gọi server, thông báo, cỡ chữ A− A+, tải file lên. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const store = {
  get: (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
};

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({ error: 'Server trả về dữ liệu lỗi.' }));
  if (!res.ok || data.error) throw new Error(data.error || res.statusText);
  return data;
}

let toastTimer = 0;
function toast(msg, ms = 3500) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

/* Cỡ chữ: một hệ số --fs cho cả bản chữ, lưu trong trình duyệt. */
const FS = { def: 1, min: 0.8, max: 2 };
function setFs(v) {
  const fs = Math.round(Math.max(FS.min, Math.min(FS.max, v)) * 20) / 20;
  document.documentElement.style.setProperty('--fs', fs);
  store.set('fs', fs);
  $('[data-fs="-1"]').disabled = fs <= FS.min;
  $('[data-fs="1"]').disabled = fs >= FS.max;
  return fs;
}
setFs(store.get('fs', FS.def));
$('.fs').onclick = (e) => {
  const b = e.target.closest('[data-fs]');
  if (b) toast(`Cỡ chữ ${Math.round(setFs(store.get('fs', FS.def) + 0.1 * b.dataset.fs) * 100)}%`, 1200);
};

/* Tải file lên: thân yêu cầu là chính file, tên ở header X-Name. Bấm nút hoặc thả file vào cửa sổ. */
async function uploadFiles(files, onDone) {
  for (const f of files) {
    toast(`Đang tải lên “${f.name}”…`, 60000);
    try {
      const r = await fetch('/api/upload', { method: 'POST', headers: { 'X-Name': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' }, body: f });
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || r.statusText);
      toast(`Đã nhận “${f.name}”. Đang xử lý ở nền.`);
      onDone?.(d);
    } catch (e) { toast(`Không tải lên được “${f.name}”: ${e.message}`, 8000); }
  }
}
function wireUpload(onDone) {
  $('#addBtn').onclick = () => $('#fileIn').click();
  $('#fileIn').onchange = (e) => { uploadFiles([...e.target.files], onDone); e.target.value = ''; };
  let depth = 0;
  addEventListener('dragenter', (e) => { if (e.dataTransfer?.types.includes('Files')) { depth++; $('#drop').hidden = false; } });
  addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; $('#drop').hidden = true; } });
  addEventListener('dragover', (e) => e.preventDefault());
  addEventListener('drop', (e) => { e.preventDefault(); depth = 0; $('#drop').hidden = true; if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files], onDone); });
}

/* Danh sách bên trái: lọc theo tên, dòng trạng thái của việc đang chạy nền. */
function jobLine(j) {
  if (!j) return '';
  const what = { queued: 'Chờ tới lượt', text: 'Đang tạo bản chữ', ocr: 'Đang OCR (PDF quét)', transcribe: 'Đang chép lời', done: '', error: 'Lỗi' }[j.state] || '';
  if (j.state === 'error') return `<small class="bad">Lỗi: ${esc(j.msg.split('\n').pop())}</small>`;
  if (!what) return '';
  return `<small>${what}${j.pct ? ` · ${j.pct}%` : ''}</small><div class="bar"><i style="width:${j.pct || 2}%"></i></div>`;
}
