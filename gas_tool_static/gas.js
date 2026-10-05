'use strict';

const $ = (id) => document.getElementById(id);

const img = $('frameImg');
const canvas = $('draw');
const ctx = canvas.getContext('2d');

const state = {
  frames: [],      // {name, gas}
  labels: {},      // name -> [regions]
  index: 0,
  shortlist: true,
  drawing: null,   // {kind, points, erase}
  dirty: false,
};

/* ------------------------------------------------------------------ */
/* dataset                                                             */
/* ------------------------------------------------------------------ */

async function loadDataset() {
  const res = await fetch('/api/dataset');
  const data = await res.json();
  $('devName').textContent = data.device || '';
  state.all = data.frames || [];
  applyFilter();
}

function applyFilter() {
  // Frames with a first pass of gas in them come first, so the useful shots
  // are at the top instead of buried in 200 near-identical ones.
  const sorted = [...(state.all || [])].sort((a, b) => (b.gas || 0) - (a.gas || 0));
  state.frames = state.shortlist ? sorted.filter((f) => f.gas > 0) : sorted;
  if (!state.frames.length) state.frames = sorted;
  state.index = 0;
  renderThumbs();
  showFrame(0);
}

function visible() {
  return state.frames.map((f) => f.name);
}

function regions() {
  const name = visible()[state.index];
  if (!name) return [];
  if (!state.labels[name]) state.labels[name] = [];
  return state.labels[name];
}

/* ------------------------------------------------------------------ */
/* rendering                                                           */
/* ------------------------------------------------------------------ */

function showFrame(index) {
  const list = visible();
  if (!list.length) return;
  state.index = Math.max(0, Math.min(index, list.length - 1));
  const name = list[state.index];

  img.onload = () => {
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    draw();
  };
  img.src = `/frame/${name}?t=${Date.now()}`;

  $('posLabel').textContent = `${state.index + 1} / ${list.length}`;
  $('frameCount').textContent = `${list.length} кадров, размечено: ${Object.keys(state.labels).length}`;
  markActiveThumb();
}

function draw() {
  if (!canvas.width) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  for (const region of regions()) {
    if (region.kind === 'rect' && region.points.length === 2) {
      const [a, b] = region.points;
      ctx.fillStyle = 'rgba(62, 207, 142, 0.22)';
      ctx.strokeStyle = '#3ecf8e';
      ctx.lineWidth = Math.max(2, canvas.width / 400);
      ctx.fillRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
    } else if (region.points.length > 1) {
      ctx.fillStyle = 'rgba(62, 207, 142, 0.22)';
      ctx.strokeStyle = '#3ecf8e';
      ctx.lineWidth = Math.max(2, canvas.width / 400);
      ctx.beginPath();
      ctx.moveTo(region.points[0][0], region.points[0][1]);
      for (const [x, y] of region.points.slice(1)) ctx.lineTo(x, y);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }
  }

  if (state.drawing && state.drawing.points.length > 1) {
    ctx.strokeStyle = state.drawing.erase ? '#ff6b6b' : '#ffd166';
    ctx.lineWidth = Math.max(2, canvas.width / 400);
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    const pts = state.drawing.points;
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (const [x, y] of pts.slice(1)) ctx.lineTo(x, y);
    if (state.drawing.kind === 'rect' && pts.length === 2) {
      const [a, b] = pts;
      ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
    } else {
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
}

function renderThumbs() {
  const box = $('thumbs');
  box.innerHTML = '';
  state.frames.forEach((frame, index) => {
    const wrap = document.createElement('div');
    wrap.className = 'thumb' + (index === state.index ? ' active' : '');
    wrap.dataset.index = index;

    const pic = document.createElement('img');
    pic.src = `/frame/${frame.name}`;
    pic.loading = 'lazy';
    wrap.appendChild(pic);

    if (frame.gas) {
      const badge = document.createElement('span');
      badge.className = 'badge-gas';
      badge.textContent = frame.gas;
      wrap.appendChild(badge);
    }
    if (state.labels[frame.name] && state.labels[frame.name].length) {
      const badge = document.createElement('span');
      badge.className = 'badge-lab';
      badge.textContent = '✓';
      wrap.appendChild(badge);
    }
    wrap.onclick = () => showFrame(index);
    box.appendChild(wrap);
  });
  markActiveThumb();
}

function markActiveThumb() {
  document.querySelectorAll('.thumb').forEach((el) => {
    el.classList.toggle('active', Number(el.dataset.index) === state.index);
  });
  const active = document.querySelector('.thumb.active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

/* ------------------------------------------------------------------ */
/* drawing                                                             */
/* ------------------------------------------------------------------ */

// The canvas is laid out at CSS size but stored at image size, so pointer
// positions have to be mapped back through the displayed scale.
function toImage(event) {
  const box = canvas.getBoundingClientRect();
  return [
    (event.clientX - box.left) * (canvas.width / box.width),
    (event.clientY - box.top) * (canvas.height / box.height),
  ];
}

function hitRegion(point) {
  const list = regions();
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const region = list[i];
    if (region.kind === 'rect' && region.points.length === 2) {
      const [a, b] = region.points;
      if (point[0] >= Math.min(a[0], b[0]) && point[0] <= Math.max(a[0], b[0])
        && point[1] >= Math.min(a[1], b[1]) && point[1] <= Math.max(a[1], b[1])) {
        return i;
      }
    } else {
      if (pointInPolygon(point, region.points)) return i;
    }
  }
  return -1;
}

function pointInPolygon([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

canvas.addEventListener('mousedown', (event) => {
  if (event.button !== 0) return;
  event.preventDefault();
  const point = toImage(event);
  const erase = event.shiftKey;

  if (!erase) {
    // A plain click on an existing region removes it, which is how you fix a
    // sloppy mark without restarting the frame.
    const hit = hitRegion(point);
    if (hit >= 0 && !state.moved) {
      regions().splice(hit, 1);
      state.dirty = true;
      draw();
      return;
    }
  }

  state.moved = false;
  state.drawing = { kind: 'rect', points: [point, point], erase };
  draw();
});

window.addEventListener('mousemove', (event) => {
  if (!state.drawing) return;
  state.moved = true;
  state.drawing.points[1] = toImage(event);
  draw();
});

window.addEventListener('mouseup', () => {
  const drawing = state.drawing;
  if (!drawing) return;
  state.drawing = null;

  const [a, b] = drawing.points;
  const area = Math.abs(b[0] - a[0]) * Math.abs(b[1] - a[1]);
  const minSide = Math.max(6, canvas.width / 300);

  if (area < minSide * minSide) {
    draw();
    return;
  }

  if (drawing.erase) {
    const hit = hitRegion([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
    if (hit >= 0) regions().splice(hit, 1);
  } else {
    regions().push({ kind: 'rect', points: [a, b] });
  }
  state.dirty = true;
  draw();
});

/* ------------------------------------------------------------------ */
/* actions                                                             */
/* ------------------------------------------------------------------ */

function toast(message, isError) {
  const box = $('toast');
  box.textContent = message;
  box.classList.toggle('err', Boolean(isError));
  box.classList.remove('hidden');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => box.classList.add('hidden'), 2600);
}

async function save(silent) {
  const name = visible()[state.index];
  if (!name) return;
  const payload = { frame: name, gas: regions() };
  const res = await fetch('/api/labels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!data.ok) {
    toast(data.message || 'Не сохранилось', true);
    return;
  }
  state.dirty = false;
  const frame = state.frames[state.index];
  frame.gas = regions().length;
  renderThumbs();
  if (!silent) toast('Сохранено');
}

async function fit() {
  const out = $('fitOut');
  out.textContent = 'Считаю...';
  const res = await fetch('/api/fit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ apply: true, clip: 1.0 }),
  });
  const data = await res.json();

  if (!data.ok) {
    out.textContent = data.message || 'Не хватило данных.';
    return;
  }

  const pct = (data.false_positive_rate * 100).toFixed(2);
  out.innerHTML = `
    Газ: <b>${data.gas_pixels.toLocaleString()}</b> px из <b>${data.frames_used}</b> кадров<br>
    Фон: <b>${data.background_pixels.toLocaleString()}</b> px<br>
    Ложных срабатываний на фоне: <span class="num">${pct}%</span><br>
    Диапазон H <span class="num">${data.low[0]}</span>–<span class="num">${data.high[0]}</span>,
    S <span class="num">${data.low[1]}</span>–<span class="num">${data.high[1]}</span>,
    V <span class="num">${data.low[2]}</span>–<span class="num">${data.high[2]}</span><br>
    Записано в <b>${data.applied_to || '—'}</b>`;
  toast('Цвет подобран и применён');
}

function clearCurrent() {
  const name = visible()[state.index];
  if (!name) return;
  state.labels[name] = [];
  state.dirty = true;
  draw();
  renderThumbs();
}

$('prevBtn').onclick = () => showFrame(state.index - 1);
$('nextBtn').onclick = () => showFrame(state.index + 1);
$('clearBtn').onclick = clearCurrent;
$('saveBtn').onclick = () => save();
$('fitBtn').onclick = fit;
$('shortlistChk').onchange = (event) => {
  state.shortlist = event.target.checked;
  applyFilter();
};

document.addEventListener('keydown', (event) => {
  if (event.ctrlKey || event.metaKey) {
    if (event.key === 's') {
      event.preventDefault();
      save();
    }
    return;
  }
  if (event.key === 'Enter' || event.key === 'ArrowRight' || event.key === ' ') {
    event.preventDefault();
    if (state.dirty) save(true);
    showFrame(state.index + 1);
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault();
    showFrame(state.index - 1);
  } else if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault();
    clearCurrent();
  }
});

loadDataset();
