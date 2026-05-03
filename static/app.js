'use strict';

const API = {
  stats: '/api/stats',
  next: '/api/next',
  history: '/api/history?limit=24',
  queue: '/api/queue?limit=10',
  stream: '/api/stream',
  trigger: '/api/trigger',
  config: '/api/config',
};

let _currentPost = null;

let _nextRunAt = null;
let _totalInterval = 0;
let _countdownInterval = null;
let _sse = null;

/* ─── Utils ─── */
function qs(sel) { return document.querySelector(sel); }
function fmt2(n) { return String(n).padStart(2, '0'); }

function fmtTime(isoStr) {
  if (!isoStr) return '—';
  try {
    return new Date(isoStr).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  } catch { return '—'; }
}

function extBadge(ext) {
  const map = { jpg: 'JPG', jpeg: 'JPG', png: 'PNG', gif: 'GIF', webm: 'VIDEO', mp4: 'VIDEO' };
  return map[ext] || ext?.toUpperCase() || '';
}

/* ─── Toast ─── */
function toast(msg, type = 'info') {
  const icon = { success: '✅', error: '❌', info: '🔔' }[type] || '🔔';
  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  el.innerHTML = `<span>${icon}</span><span>${msg}</span>`;
  qs('#toast-container').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

/* ─── Status Indicator ─── */
function setStatus(online) {
  const dot = qs('#status-dot');
  const label = qs('#status-label');
  dot.className = 'status-dot ' + (online ? 'online' : 'offline');
  label.textContent = online ? 'Online' : 'Offline';
}

/* ─── Stats ─── */
async function loadStats() {
  try {
    const data = await fetch(API.stats).then(r => r.json());
    qs('#stat-sent').textContent = data.total_sent ?? 0;
    qs('#stat-queued').textContent = data.total_queued ?? 0;
    qs('#stat-failed').textContent = data.total_failed ?? 0;
    if (data.queue_composition) {
      renderComposition(data.queue_composition);
    }
  } catch (e) {
    console.warn('Stats fetch failed', e);
  }
}

function renderComposition(comp) {
  if (!comp || comp.total === 0) return;
  const imgPct = (comp.image_ratio * 100).toFixed(0);
  const gifPct = (comp.gif_ratio * 100).toFixed(0);
  const vidPct = (comp.video_ratio * 100).toFixed(0);

  qs('#comp-seg-img').style.width = `${imgPct}%`;
  qs('#comp-seg-gif').style.width = `${gifPct}%`;
  qs('#comp-seg-vid').style.width = `${vidPct}%`;

  qs('#comp-label-img').textContent = `🖼 Fotos ${imgPct}%`;
  qs('#comp-label-gif').textContent = `🎞 GIFs ${gifPct}%`;
  qs('#comp-label-vid').textContent = `🎬 Vídeos ${vidPct}%`;

  const modeEl = qs('#comp-mode');
  if (comp.mode === 'animated_boost') {
    modeEl.textContent = '⚡ Buscando animados';
    modeEl.className = 'composition-bar__mode mode-boost';
  } else {
    modeEl.textContent = '✔ Balanceado';
    modeEl.className = 'composition-bar__mode mode-normal';
  }
}

/* ─── Countdown ─── */
function startCountdown() {
  if (_countdownInterval) clearInterval(_countdownInterval);
  _countdownInterval = setInterval(tickCountdown, 1000);
}

function tickCountdown() {
  if (!_nextRunAt) return;
  const now = Date.now();
  const diff = Math.max(0, Math.floor((_nextRunAt - now) / 1000));

  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  const s = diff % 60;

  const elH = qs('#cd-hours');
  const elM = qs('#cd-minutes');
  const elS = qs('#cd-seconds');

  const newH = fmt2(h);
  const newM = fmt2(m);
  const newS = fmt2(s);

  if (elS.textContent !== newS) { elS.textContent = newS; pulse(elS); }
  if (elM.textContent !== newM) { elM.textContent = newM; pulse(elM); }
  if (elH.textContent !== newH) { elH.textContent = newH; pulse(elH); }

  if (_totalInterval > 0) {
    const pct = (diff / _totalInterval) * 100;
    qs('#countdown-bar').style.width = `${Math.min(100, pct)}%`;
  }

  if (diff === 0) {
    qs('#next-time-label').textContent = 'Enviando agora...';
  } else {
    qs('#next-time-label').textContent = `Próximo às ${fmtTime(new Date(_nextRunAt).toISOString())}`;
  }
}

function pulse(el) {
  el.classList.remove('pulse');
  void el.offsetWidth;
  el.classList.add('pulse');
  setTimeout(() => el.classList.remove('pulse'), 300);
}

/* ─── Next Post ─── */
async function loadNext() {
  try {
    const data = await fetch(API.next).then(r => r.json());
    const { next_post, next_run_at, seconds_remaining } = data;

    if (next_run_at) {
      _nextRunAt = new Date(next_run_at).getTime();
      const totalSecs = seconds_remaining || 5400;
      _totalInterval = Math.max(totalSecs, 1);
      qs('#next-time-label').textContent = `Próximo às ${fmtTime(next_run_at)}`;
    }

    if (next_post) {
      _cachePost(next_post);
      renderNextPost(next_post);
    }
  } catch (e) {
    console.warn('Next fetch failed', e);
  }
}

function renderNextPost(post) {
  const img = qs('#next-img');
  const placeholder = qs('#next-placeholder');
  const extBadgeEl = qs('#next-ext');

  const thumbUrl = post.preview_url || post.sample_url;
  if (thumbUrl) {
    img.src = thumbUrl;
    img.onload = () => {
      img.classList.remove('hidden');
      placeholder.classList.add('hidden');
    };
    img.onerror = () => {
      img.classList.add('hidden');
      placeholder.classList.remove('hidden');
    };
  }

  qs('#next-id').textContent = `e621#${post.e621_id}`;
  qs('#next-score').textContent = `⬆ ${post.score ?? 0}`;
  qs('#next-fav').textContent = `♥ ${post.fav_count ?? 0}`;
  extBadgeEl.textContent = extBadge(post.file_ext);
}

/* ─── History ─── */
async function loadHistory() {
  try {
    const posts = await fetch(API.history).then(r => r.json());
    const grid = qs('#history-grid');
    if (!posts.length) {
      grid.innerHTML = '<div class="loading-row">Nenhum envio ainda.</div>';
      return;
    }
    posts.forEach(p => _cachePost(p));
    grid.innerHTML = posts.map(p => thumbCard(p)).join('');
  } catch (e) {
    qs('#history-grid').innerHTML = '<div class="loading-row">Erro ao carregar.</div>';
  }
}

function thumbCard(post) {
  const src = post.preview_url || post.sample_url || '';
  const id = `thumb-${post.id}`;
  const dataIdx = `data-post-id="${post.id}"`;
  return `
    <div class="media-thumb" id="${id}" ${dataIdx}
         title="e621#${post.e621_id} — ⬆${post.score} ♥${post.fav_count}"
         onclick="window._openPostById(${post.id})">
      ${src ? `<img src="${src}" alt="e621#${post.e621_id}" loading="lazy" />` : '<div style="width:100%;height:100%;background:rgba(255,255,255,0.03)"></div>'}
      <div class="media-thumb__ext">${extBadge(post.file_ext)}</div>
      <div class="media-thumb__overlay">
        <span class="media-thumb__score">⬆ ${post.score ?? 0}</span>
        <span class="media-thumb__id">e621#${post.e621_id}</span>
      </div>
    </div>`;
}

/* ─── Lightbox ─── */
function openLightbox(post) {
  _currentPost = post;
  const ext = (post.file_ext || '').toLowerCase();
  const mediaEl = qs('#lightbox-media');
  const useUrl = (post.file_size && post.file_size > 50 * 1024 * 1024)
    ? (post.sample_url || post.file_url)
    : (post.file_url || post.sample_url);

  // Limpar media anterior
  mediaEl.innerHTML = '';

  if (ext === 'webm' || ext === 'mp4') {
    const vid = document.createElement('video');
    vid.src = useUrl;
    vid.controls = true;
    vid.autoplay = true;
    vid.loop = true;
    vid.muted = false;
    vid.setAttribute('playsinline', '');
    mediaEl.appendChild(vid);
  } else {
    const img = document.createElement('img');
    img.src = useUrl;
    img.alt = `e621#${post.e621_id}`;
    mediaEl.appendChild(img);
  }

  qs('#lb-id').textContent = `e621#${post.e621_id}`;
  qs('#lb-score').textContent = `⬆ ${post.score ?? 0}`;
  qs('#lb-fav').textContent = `♥ ${post.fav_count ?? 0}`;
  qs('#lb-ext').textContent = (ext || '?').toUpperCase();
  qs('#lb-link').href = `https://e621.net/posts/${post.e621_id}`;

  const tagsEl = qs('#lb-tags');
  const tags = Array.isArray(post.tags) ? post.tags : [];
  tagsEl.innerHTML = tags.length
    ? tags.map(t => `<span class="lb-tag">${t}</span>`).join('')
    : '<span style="color:var(--text-muted);font-size:11px">Sem tags disponíveis</span>';

  qs('#lightbox').classList.add('active');
  document.body.style.overflow = 'hidden';
}

function closeLightbox() {
  const lb = qs('#lightbox');
  lb.classList.remove('active');
  document.body.style.overflow = '';
  // Para vídeo
  const vid = lb.querySelector('video');
  if (vid) { vid.pause(); vid.src = ''; }
  qs('#lightbox-media').innerHTML = '';
  _currentPost = null;
}

/* ─── Filter Tags ─── */
async function loadConfig() {
  try {
    const cfg = await fetch(API.config).then(r => r.json());

    const incEl = qs('#tags-included');
    incEl.innerHTML = cfg.search_tags.map(t => {
      const cls = t.startsWith('~') ? 'tag-chip--or' : 'tag-chip--required';
      const label = t.startsWith('~') ? t.slice(1) : t;
      return `<span class="tag-chip ${cls}" title="${t.startsWith('~') ? 'OR' : 'Obrigatório'}">${label}</span>`;
    }).join('');

    const blEl = qs('#tags-blacklist');
    blEl.innerHTML = cfg.blacklist.map(t =>
      `<span class="tag-chip tag-chip--blacklist">−${t}</span>`
    ).join('');

    qs('#tags-interval').textContent = cfg.interval;
    qs('#tags-balance').textContent = `boost > ${cfg.balance_threshold} imagens`;
  } catch (e) {
    console.warn('Config fetch failed', e);
  }
}

async function loadQueue() {
  try {
    const posts = await fetch(API.queue).then(r => r.json());
    const list = qs('#queue-list');
    const badge = qs('#queue-count-badge');
    if (!posts.length) {
      list.innerHTML = '<div class="loading-row">Fila vazia — aguardando reabastecimento...</div>';
      if (badge) badge.textContent = '0';
      return;
    }
    if (badge) badge.textContent = posts.length;
    posts.forEach(p => _cachePost(p));
    list.innerHTML = posts.map((p, i) => queueItem(p, i)).join('');
  } catch (e) {
    qs('#queue-list').innerHTML = '<div class="loading-row">Erro ao carregar.</div>';
  }
}

function queueItem(post, idx) {
  const isNext = idx === 0;
  const src = post.preview_url || post.sample_url || '';
  const pos = idx + 1;
  return `
    <div class="queue-item${isNext ? ' queue-item--next' : ''}" onclick="window._openPostById(${post.id})" style="cursor:pointer">
      <span class="queue-item__pos${isNext ? ' queue-item__pos--next' : ''}">${isNext ? '▶' : pos}</span>
      ${src
        ? `<img class="queue-item__thumb" src="${src}" alt="e621#${post.e621_id}" loading="lazy" />`
        : `<div class="queue-item__thumb"></div>`}
      <div class="queue-item__info">
        <div class="queue-item__id">e621#${post.e621_id}</div>
        <div class="queue-item__meta">⬆ ${post.score ?? 0} · ♥ ${post.fav_count ?? 0} · ${extBadge(post.file_ext)}</div>
      </div>
      ${isNext ? '<span class="queue-item__badge">PRÓXIMO</span>' : ''}
    </div>`;
}

/* ─── Trigger ─── */
qs('#btn-trigger').addEventListener('click', async () => {
  try {
    qs('#btn-trigger').disabled = true;
    qs('#btn-trigger').textContent = '⏳ Enviando...';
    const res = await fetch(API.trigger, { method: 'POST' });
    if (res.ok) {
      toast('Envio agendado para os próximos segundos!', 'success');
    } else {
      toast('Erro ao acionar envio.', 'error');
    }
  } catch (e) {
    toast('Erro de conexão.', 'error');
  } finally {
    setTimeout(() => {
      qs('#btn-trigger').disabled = false;
      qs('#btn-trigger').textContent = '⚡ Enviar agora';
    }, 5000);
  }
});

/* ─── SSE ─── */
function connectSSE() {
  if (_sse) { _sse.close(); }
  _sse = new EventSource(API.stream);

  _sse.onopen = () => {
    setStatus(true);
  };

  _sse.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.event === 'connected') {
        setStatus(true);
      }
      if (data.event === 'balance_boost') {
        const pct = Math.round((data.image_ratio || 0) * 100);
        toast(`🧠 Fila com ${pct}% de fotos — buscando GIFs e vídeos...`, 'info');
        const modeEl = qs('#comp-mode');
        modeEl.textContent = '⚡ Buscando animados';
        modeEl.className = 'composition-bar__mode mode-boost';
      }
      if (data.event === 'post_sent') {
        toast(`Mídia enviada! e621#${data.e621_id}`, data.success ? 'success' : 'error');
        if (data.next_run_at) {
          _nextRunAt = new Date(data.next_run_at).getTime();
          _totalInterval = data.interval_seconds || 5400;
        }
        if (data.queue_composition) {
          renderComposition(data.queue_composition);
        }
        setTimeout(() => {
          loadStats();
          loadHistory();
          loadQueue();
          loadNext();
        }, 2000);
      }
    } catch (err) { /* keepalive */ }
  };

  _sse.onerror = () => {
    setStatus(false);
    _sse.close();
    setTimeout(connectSSE, 5000);
  };
}

/* ─── Init ─── */
async function init() {
  await Promise.all([loadStats(), loadNext(), loadHistory(), loadQueue(), loadConfig()]);
  startCountdown();
  connectSSE();

  setInterval(loadStats, 30_000);
  setInterval(loadQueue, 30_000);

  // Lightbox events
  qs('#lightbox-close').addEventListener('click', closeLightbox);
  qs('#lightbox-backdrop').addEventListener('click', closeLightbox);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

  // Lookup global para abrir lightbox por ID do post (usado nos onclick inline)
  window._postCache = {};
  window._openPostById = (id) => {
    const post = window._postCache[id];
    if (post) openLightbox(post);
  };
}

function _cachePost(post) {
  if (!window._postCache) window._postCache = {};
  if (post && post.id) window._postCache[post.id] = post;
}

document.addEventListener('DOMContentLoaded', init);
