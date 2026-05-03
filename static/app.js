'use strict';

const API = {
  stats: '/api/stats',
  next: '/api/next',
  history: '/api/history?limit=24',
  queue: '/api/queue?limit=10',
  stream: '/api/stream',
  trigger: '/api/trigger',
  config: '/api/config',
  login: '/api/auth/login',
  logout: '/api/auth/logout',
  me: '/api/auth/me',
  changePw: '/api/auth/change-password',
  resetQueue: '/api/admin/reset-queue',
};

window._isAdmin = false;

function _adminHeaders() {
  const token = localStorage.getItem('admin_token');
  return token ? { 'X-Admin-Token': token } : {};
}

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
    const mimeType = ext === 'mp4' ? 'video/mp4' : 'video/webm';
    const vid = document.createElement('video');
    vid.controls = true;
    vid.autoplay = true;
    vid.loop = true;
    vid.muted = true;
    vid.setAttribute('playsinline', '');
    vid.style.cssText = 'max-width:88vw;max-height:70vh;border-radius:var(--radius-lg)';

    const src = document.createElement('source');
    src.src = useUrl;
    src.type = mimeType;
    vid.appendChild(src);

    const fallbackLink = document.createElement('div');
    fallbackLink.style.cssText = 'text-align:center;padding:12px;color:var(--text-muted);font-size:12px;margin-top:8px';
    fallbackLink.innerHTML = `<a href="${useUrl}" target="_blank" rel="noopener" style="color:var(--neon-cyan)">🎬 Abrir vídeo direto ↗</a>`;

    const handleVideoError = () => {
      mediaEl.innerHTML = '';
      if (post.sample_url) {
        const img = document.createElement('img');
        img.src = post.sample_url;
        img.alt = `e621#${post.e621_id} (preview)`;
        mediaEl.appendChild(img);
      }
      const notice = document.createElement('div');
      notice.style.cssText = 'text-align:center;padding:8px;color:var(--text-muted);font-size:11px';
      notice.innerHTML = `Vídeo não suportado neste navegador — <a href="${useUrl}" target="_blank" rel="noopener" style="color:var(--neon-cyan)">Abrir direto ↗</a>`;
      mediaEl.appendChild(notice);
    };

    src.addEventListener('error', handleVideoError);
    vid.addEventListener('error', handleVideoError);

    mediaEl.appendChild(vid);
    mediaEl.appendChild(fallbackLink);
    vid.load();
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
    const cfg = await fetch(API.config, { headers: _adminHeaders() }).then(r => r.json());
    renderTagGroup('tags-mandatory', cfg.mandatory_tags || [], 'mandatory', 'tag-chip--mandatory');
    renderTagGroup('tags-required', cfg.required_tags || [], 'required', 'tag-chip--required');
    renderTagGroup('tags-or', cfg.or_tags || [], 'or', 'tag-chip--or');
    renderTagGroup('tags-blacklist', cfg.blacklist || [], 'blacklist', 'tag-chip--blacklist');
    qs('#tags-interval').textContent = cfg.interval;
    qs('#tags-balance').textContent = `boost > ${cfg.balance_threshold} imagens`;
  } catch (e) {
    console.warn('Config fetch failed', e);
  }
}

function renderTagGroup(containerId, tags, type, chipClass) {
  const el = qs(`#${containerId}`);
  if (!el) return;
  if (!tags.length) {
    el.innerHTML = '<span style="color:var(--text-muted);font-size:11px;font-style:italic">nenhuma</span>';
    return;
  }
  el.innerHTML = tags.map(t => `
    <span class="tag-chip ${chipClass}">
      ${t}
      <button class="tag-chip__remove" onclick="removeTagFromUI('${type}','${t}',this)" title="Remover">&times;</button>
    </span>`).join('');
}

async function removeTagFromUI(type, tag, btn) {
  btn.disabled = true;
  try {
    const res = await fetch('/api/config/tags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'remove', type, tag }),
    }).then(r => r.json());
    if (res.ok) {
      toast(`Tag removida: ${tag}`, 'success');
      await loadConfig();
    } else {
      toast(`Erro: ${res.error}`, 'error');
      btn.disabled = false;
    }
  } catch {
    toast('Erro de conexão', 'error');
    btn.disabled = false;
  }
}

window.addTagFromInput = async function(type) {
  const input = qs(`#input-${type}`);
  const tag = (input?.value || '').trim().toLowerCase().replace(/^[~\-]+/, '');
  if (!tag) return;
  input.value = '';
  try {
    const res = await fetch('/api/config/tags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'add', type, tag }),
    }).then(r => r.json());
    if (res.ok) {
      toast(`Tag adicionada: ${tag}`, 'success');
      await loadConfig();
    } else {
      toast(`Erro: ${res.error}`, 'error');
    }
  } catch {
    toast('Erro de conexão', 'error');
  }
};

['mandatory','required','or','blacklist'].forEach(type => {
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && document.activeElement?.id === `input-${type}`) {
      addTagFromInput(type);
    }
  });
});

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
    const res = await fetch(API.trigger, { method: 'POST', headers: _adminHeaders() });
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

/* ─── Auth ─── */
async function checkAuth() {
  const token = localStorage.getItem('admin_token');
  if (!token) { applyRole(false, null); return; }
  try {
    const data = await fetch(API.me, { headers: _adminHeaders() }).then(r => r.json());
    if (data.authenticated) {
      window._isAdmin = true;
      applyRole(true, data.display_name);
      if (data.must_change_password) showChangePwModal();
    } else {
      localStorage.removeItem('admin_token');
      applyRole(false, null);
    }
  } catch { applyRole(false, null); }
}

function applyRole(isAdmin, displayName) {
  window._isAdmin = isAdmin;
  const loginBtn = qs('#btn-login');
  const userInfo = qs('#admin-user-info');
  const adminPanel = qs('#admin-panel');
  const tagsSection = qs('.tags-section');
  const triggerBtn = qs('#btn-trigger');

  if (isAdmin) {
    loginBtn?.classList.add('hidden');
    userInfo?.classList.remove('hidden');
    adminPanel?.classList.remove('hidden');
    tagsSection?.classList.remove('hidden');
    if (triggerBtn) triggerBtn.style.display = '';
    loadConfig();
    loadSuggestions();
  } else {
    loginBtn?.classList.remove('hidden');
    userInfo?.classList.add('hidden');
    adminPanel?.classList.add('hidden');
    tagsSection?.classList.add('hidden');
    if (triggerBtn) triggerBtn.style.display = 'none';
  }
}

function showLoginModal() { qs('#modal-login')?.classList.remove('hidden'); qs('#login-username')?.focus(); }
function hideLoginModal() { qs('#modal-login')?.classList.add('hidden'); qs('#login-error')?.classList.add('hidden'); }
function showChangePwModal() { qs('#modal-change-pw')?.classList.remove('hidden'); qs('#cp-current')?.focus(); }

async function submitLogin(e) {
  e.preventDefault();
  const username = qs('#login-username').value.trim();
  const password = qs('#login-password').value;
  const btn = qs('#btn-login-submit');
  const errEl = qs('#login-error');
  btn.disabled = true; btn.textContent = 'Entrando...';
  try {
    const res = await fetch(API.login, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then(r => r.json());
    if (res.ok) {
      localStorage.setItem('admin_token', res.token);
      hideLoginModal();
      applyRole(true, res.display_name);
      if (res.must_change_password) showChangePwModal();
      toast(`Bem-vindo, ${res.display_name}!`, 'success');
    } else {
      errEl.textContent = res.error;
      errEl.classList.remove('hidden');
    }
  } catch { errEl.textContent = 'Erro de conexão'; errEl.classList.remove('hidden'); }
  btn.disabled = false; btn.textContent = 'Entrar';
  return false;
}

async function logoutAdmin() {
  await fetch(API.logout, { method: 'POST', headers: _adminHeaders() }).catch(() => {});
  localStorage.removeItem('admin_token');
  applyRole(false, null);
  toast('Sessão encerrada', 'info');
}

async function submitChangePassword(e) {
  e.preventDefault();
  const current = qs('#cp-current').value;
  const newPw = qs('#cp-new').value;
  const confirm = qs('#cp-confirm').value;
  const errEl = qs('#cp-error');
  if (newPw !== confirm) { errEl.textContent = 'As senhas não coincidem'; errEl.classList.remove('hidden'); return false; }
  try {
    const res = await fetch(API.changePw, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ..._adminHeaders() },
      body: JSON.stringify({ current_password: current, new_password: newPw }),
    }).then(r => r.json());
    if (res.ok) {
      qs('#modal-change-pw').classList.add('hidden');
      toast('Senha alterada com sucesso!', 'success');
    } else { errEl.textContent = res.error; errEl.classList.remove('hidden'); }
  } catch { errEl.textContent = 'Erro de conexão'; errEl.classList.remove('hidden'); }
  return false;
}

window.confirmResetQueue = async function() {
  if (!confirm('Tem certeza? Isso vai limpar TODOS os posts em fila (não afeta o canal).')) return;
  const btn = qs('#btn-reset-queue');
  btn.disabled = true; btn.textContent = '⏳ Resetando...';
  try {
    const res = await fetch(API.resetQueue, { method: 'POST', headers: _adminHeaders() }).then(r => r.json());
    if (res.ok) {
      toast(`Fila resetada! ${res.removed_from_queue} posts removidos.`, 'success');
      loadQueue();
      loadStats();
    } else { toast('Erro ao resetar', 'error'); }
  } catch { toast('Erro de conexão', 'error'); }
  btn.disabled = false; btn.textContent = '🗑️ Resetar Fila de Imagens';
};

/* ─── Init ─── */
async function init() {
  await checkAuth();
  await Promise.all([loadStats(), loadNext(), loadHistory(), loadQueue()]);
  startCountdown();
  connectSSE();

  setInterval(loadStats, 30_000);
  setInterval(loadQueue, 30_000);

  qs('#lightbox-close').addEventListener('click', closeLightbox);
  qs('#lightbox-backdrop').addEventListener('click', closeLightbox);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

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

/* ─── Suggestions ─── */
function showSuggestionModal() {
  qs('#suggestion-tag').value = '';
  qs('#suggestion-error').classList.add('hidden');
  qs('#suggestion-success').classList.add('hidden');
  qs('#modal-suggestion').classList.remove('hidden');
  qs('#suggestion-tag').focus();
}
function hideSuggestionModal() { qs('#modal-suggestion').classList.add('hidden'); }

async function submitSuggestion(e) {
  e.preventDefault();
  const tag = qs('#suggestion-tag').value.trim();
  const errEl = qs('#suggestion-error');
  const sucEl = qs('#suggestion-success');
  const btn = qs('#btn-suggestion-submit');
  errEl.classList.add('hidden'); sucEl.classList.add('hidden');
  btn.disabled = true; btn.textContent = 'Enviando...';
  try {
    const res = await fetch('/api/suggestions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag }),
    }).then(r => r.json());
    if (res.ok) {
      sucEl.classList.remove('hidden');
      qs('#suggestion-tag').value = '';
    } else {
      errEl.textContent = res.error;
      errEl.classList.remove('hidden');
    }
  } catch { errEl.textContent = 'Erro de conexão'; errEl.classList.remove('hidden'); }
  btn.disabled = false; btn.textContent = 'Enviar Sugestão';
  return false;
}

async function loadSuggestions() {
  try {
    const list = await fetch('/api/suggestions', { headers: _adminHeaders() }).then(r => r.json());
    const el = qs('#suggestions-list');
    const countEl = qs('#suggestions-count');
    if (!el) return;
    if (!list.length) {
      el.innerHTML = '<span class="suggestions-empty">Nenhuma sugestão pendente.</span>';
      countEl?.classList.add('hidden');
      return;
    }
    countEl.textContent = list.length;
    countEl?.classList.remove('hidden');
    el.innerHTML = list.map(s => `
      <div class="suggestion-item" id="sug-${s.id}">
        <span class="suggestion-tag">${s.tag}</span>
        <button class="btn-accept" onclick="acceptSuggestion(${s.id}, '${s.tag}')">✅ Aceitar</button>
        <button class="btn-reject" onclick="rejectSuggestion(${s.id})">❌ Rejeitar</button>
      </div>
    `).join('');
  } catch { /* silencioso */ }
}

async function acceptSuggestion(id, tag) {
  const res = await fetch(`/api/suggestions/${id}/accept`, { method: 'POST', headers: _adminHeaders() }).then(r => r.json());
  if (res.ok) {
    qs(`#sug-${id}`)?.remove();
    toast(`Tag "${tag}" adicionada ao OR!`, 'success');
    loadConfig();
    loadSuggestions();
  } else { toast(res.error, 'error'); }
}

async function rejectSuggestion(id) {
  const res = await fetch(`/api/suggestions/${id}/reject`, { method: 'POST', headers: _adminHeaders() }).then(r => r.json());
  if (res.ok) {
    qs(`#sug-${id}`)?.remove();
    toast('Sugestão rejeitada.', 'info');
    loadSuggestions();
  } else { toast(res.error, 'error'); }
}
