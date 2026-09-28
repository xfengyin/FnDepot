'use strict';
/* ── 状态 ─────────────────────────────────────────────────────────── */
const LS_KEY = 'g2a.key', LS_THEME = 'g2a.theme';
let theme = localStorage.getItem(LS_THEME) || 'auto';   // auto | light | dark
let view = 'dashboard';
let refTimer = null;
let logPin = true, logCh = 'all';
let modelsCache = null;

const $ = id => document.getElementById(id);

/* ── 主题（浅/深两态，首访跟随系统偏好）───────────────────────────── */
function effTheme() {
  return theme === 'auto' ? (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : theme;
}
function applyTheme() {
  const eff = effTheme();
  document.documentElement.dataset.theme = eff;
  $('icoTheme').innerHTML = eff === 'light'
    ? '<circle cx="8" cy="8" r="3"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.2 3.2l1.4 1.4M11.4 11.4l1.4 1.4M12.8 3.2l-1.4 1.4M4.6 11.4l-1.4 1.4"/>'
    : '<path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8z"/>';
  $('btnTheme').title = eff === 'light' ? '切换到深色' : '切换到浅色';
}
addEventListener('change', applyTheme);
$('btnTheme').onclick = () => {
  theme = effTheme() === 'light' ? 'dark' : 'light';
  localStorage.setItem(LS_THEME, theme);
  applyTheme();
};
applyTheme();

/* ── 请求 ─────────────────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const h = Object.assign({}, opts.headers || {});
  const k = localStorage.getItem(LS_KEY);
  if (k) h['Authorization'] = 'Bearer ' + k;
  if (opts.body) h['Content-Type'] = 'application/json';
  const r = await fetch('/panel/api/' + path, Object.assign({}, opts, { headers: h }));
  if (r.status === 401) { openKey(); throw new Error('密钥无效或未填写'); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}
function toast(msg, cls) {
  const el = document.createElement('div');
  el.className = 'tst ' + (cls || '');
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => el.remove(), 3600);
}
/* esc 显式替换 5 个字符（& 最先），拼 HTML 属性时防引号闭合注入 */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ── 单位格式化 ───────────────────────────────────────────────────── */
function fmtCount(n) {
  if (n == null || n === '') return '—';
  n = Number(n);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1000) return String(Math.round(n));
  const units = [['k', 1e3], ['m', 1e6], ['b', 1e9]];
  let unit = units[0];
  for (const c of units) if (n >= c[1]) unit = c;
  let value = n / unit[1];
  let rounded = Number(value.toFixed(1));
  const next = units[units.indexOf(unit) + 1];
  if (next && rounded >= 1000) { unit = next; rounded = Number((n / unit[1]).toFixed(1)); }
  return rounded + unit[0];
}
function fmtTok(n) {
  if (n == null || n === '') return '—';
  n = Number(n);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n));
}
function fmtLatency(ms) {
  if (ms == null || ms === '') return '—';
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return '—';
  return n < 1000 ? Math.round(n) + 'ms' : (n / 1000).toFixed(1).replace(/\.0$/, '') + 's';
}
function ago(ts) {
  if (!ts && ts !== 0) return '—';
  const t = typeof ts === 'number' ? ts * 1000 : new Date(ts).getTime();
  if (!Number.isFinite(t)) return '—';
  const s = (Date.now() - t) / 1000;
  if (s < 0) return '刚刚';
  if (s < 60) return Math.floor(s) + ' 秒前';
  if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
  if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
  return Math.floor(s / 86400) + ' 天前';
}
function fmtUptime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d > 0) return d + ' 天 ' + h + ' 时 ' + m + ' 分';
  if (h > 0) return h + ' 时 ' + m + ' 分';
  return m + ' 分 ' + (sec % 60) + ' 秒';
}

/* ── 密钥门 ───────────────────────────────────────────────────────── */
function openKey() { $('keyVeil').classList.add('on'); setTimeout(() => $('keyInput').focus(), 60); }
$('btnKey').onclick = async () => {
  const v = $('keyInput').value.trim();
  if (!v) return;
  localStorage.setItem(LS_KEY, v);
  try {
    await api('overview');
    $('keyErr').hidden = true;
    $('keyVeil').classList.remove('on');
    start();
    go(view);
  } catch (e) { $('keyErr').hidden = false; }
};
$('keyInput').addEventListener('keydown', e => { if (e.key === 'Enter') $('btnKey').click(); });

/* ── 路由 ─────────────────────────────────────────────────────────── */
const TITLES = { dashboard: '概览', credentials: '凭证', models: '模型与探测', usage: '用量', config: '配置', logs: '运行日志' };
function go(v) {
  view = v;
  document.querySelectorAll('.view').forEach(s => s.hidden = s.id !== 'view-' + v);
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('on', a.dataset.view === v));
  $('ttl').textContent = TITLES[v];
  if (v === 'dashboard') loadDashboard(true);
  if (v === 'credentials') loadCredentials();
  if (v === 'models') loadModels();
  if (v === 'usage') loadUsage();
  if (v === 'config') loadConfig();
  if (v === 'logs') loadLogs();
}
document.querySelectorAll('.nav a').forEach(a => a.onclick = e => { e.preventDefault(); go(a.dataset.view); history.replaceState(null, '', '#' + a.dataset.view); });
document.querySelectorAll('[data-goto]').forEach(el => el.onclick = e => { e.preventDefault(); go(el.dataset.goto); history.replaceState(null, '', '#' + el.dataset.goto); });
$('btnRefresh').onclick = () => go(view);

/* ── 概览 ─────────────────────────────────────────────────────────── */
const MODE_NAMES = { 1: 'FAST', 2: 'THINKING', 3: 'PRO', 4: 'AUTO', 5: 'FAST+动态思考', 6: 'FLASH_LITE' };

async function loadDashboard(quiet) {
  try {
    const [d, m] = await Promise.all([api('overview'), api('models')]);
    modelsCache = m;
    const t = d.totals || {};
    $('dvState').textContent = '运行中';
    $('stState').className = 'stat good';
    $('dvReq').textContent = fmtCount(t.requests);
    $('dvErr').textContent = fmtCount(t.errors);
    $('stErr').className = 'stat' + (t.errors > 0 ? ' bad' : '');
    $('dvTok').textContent = fmtCount((t.prompt_tokens || 0) + (t.completion_tokens || 0));
    $('dvLat').textContent = fmtLatency(t.avg_latency_ms);
    $('dvInflight').textContent = d.in_flight;
    $('subMeta').textContent = '运行 ' + fmtUptime(d.uptime_sec);
    $('navSub').textContent = 'v' + d.version;
    $('navVer').textContent = 'v' + d.version;
    $('navAux').textContent = d.cookie.configured ? ('凭证' + (d.cookie.sapisid ? '·哈希' : '')) : '匿名';
    $('navState').textContent = '服务正常';
    $('navPulse').className = 'pulse' + (d.api_key_enabled ? '' : ' warn');

    const kv = (label, value, plain) =>
      '<tr><th>' + esc(label) + '</th><td' + (plain ? ' class="plain"' : '') + '>' + value + '</td></tr>';
    $('svcNote').textContent = d.config_path ? '' : '未加载配置文件';
    $('svcBody').innerHTML =
      kv('版本', esc(d.version) + ' ') +
      kv('监听', esc(d.host + ':' + d.port)) +
      kv('配置文件', esc(d.config_path || '（未指定）')) +
      kv('默认模型', esc(d.default_model || '—')) +
      kv('模型数量', String(d.model_count)) +
      kv('网络代理', d.proxy_set ? '已配置' : '直连 / 系统环境') +
      kv('临时对话', d.temporary_chats ? '开启' : '关闭') +
      kv('重试', '次数 ' + esc(String(d.retry.attempts)) + ' · 间隔 ' + esc(String(d.retry.delay_sec)) + 's · 超时 ' + esc(String(d.retry.timeout_sec)) + 's');

    $('credSummary').innerHTML =
      kv('状态', d.cookie.configured ? '<span class="tag ok">已配置</span>' : '<span class="tag warn">未配置（匿名访问）</span>', true) +
      kv('Cookie 文件', esc(d.cookie.file || '—')) +
      kv('SAPISIDHASH', d.cookie.sapisid ? '<span class="tag ok">可用</span>' : '<span class="tag mute">无</span>', true) +
      kv('auth_user', d.cookie.auth_user == null ? '默认账号' : esc(String(d.cookie.auth_user))) +
      kv('更新时间', esc(d.cookie.mtime || '—')) +
      kv('接口鉴权', d.api_key_enabled ? '<span class="tag ok">已启用（' + d.api_key_count + ' 个密钥）</span>' : '<span class="tag bad">未启用</span>', true);

    const probes = (m.models || []).filter(x => x.probe);
    $('dashProbes').innerHTML = probes.length ? probes.map(x => {
      const p = x.probe;
      const tag = p.ok ? '<span class="tag ok">正常</span>' : '<span class="tag bad" title="' + esc(p.error || '') + '">失败</span>';
      return '<tr><td>' + esc(x.id) + '</td><td>' + tag + '</td><td class="num">' + fmtLatency(p.latency_ms) + '</td><td class="num">' + esc(ago(p.tested_at)) + '</td></tr>';
    }).join('') : '<tr><td colspan="4"><div class="empty">还没有探测记录，去「模型与探测」跑一次</div></td></tr>';
  } catch (e) { if (!quiet) toast(e.message, 'err'); }
}

/* ── 凭证 ─────────────────────────────────────────────────────────── */
function kvRow(label, value, plain) {
  return '<tr><th>' + esc(label) + '</th><td' + (plain ? ' class="plain"' : '') + '>' + value + '</td></tr>';
}
async function loadCredentials() {
  try {
    const d = await api('credentials');
    let html =
      kvRow('状态', d.configured ? '<span class="tag ok">已配置</span>' : '<span class="tag warn">未配置（匿名访问）</span>', true) +
      kvRow('Cookie 文件', esc(d.file || d.default_file || '—')) +
      kvRow('SAPISIDHASH', d.configured ? (d.sapisid ? '<span class="tag ok">可用</span>' : '<span class="tag mute">无 SAPISID</span>') : '—', true) +
      kvRow('auth_user', d.auth_user == null ? '默认账号' : esc(String(d.auth_user))) +
      kvRow('xsrf_token', d.xsrf_token_set ? '已设置' : '未设置');
    if (d.configured) {
      html += kvRow('文件大小', esc(String(d.size)) + ' B');
      html += kvRow('更新时间', esc(d.mtime || '—'));
      html += kvRow('Cookie 项', (d.names || []).map(n => '<span class="tag mute">' + esc(n) + '</span>').join(' ') || '—', true);
    }
    $('credBody').innerHTML = html;
    $('credNote').textContent = d.file ? '' : '尚未指定 cookie 文件，保存时将写入默认路径';
  } catch (e) { toast('读取凭证失败：' + e.message, 'err'); }
}
function showCredState(prefix, ok, text) {
  const okEl = $(prefix + 'Ok'), errEl = $(prefix + 'Err');
  okEl.hidden = true; errEl.hidden = true;
  if (ok) { okEl.textContent = text; okEl.hidden = false; }
  else { errEl.textContent = text; errEl.hidden = false; }
}
function renderValidateResult(prefix, r) {
  if (r.ok) {
    const who = r.email ? ('，账号 ' + r.email) : '（未能从页面解析出账号邮箱）';
    showCredState(prefix, true, '校验通过：会话已登录' + who + '（上游 HTTP ' + (r.status || '?') + '）');
  } else {
    showCredState(prefix, false, '校验失败：' + (r.error || '未知错误'));
  }
}
$('btnCredValidate').onclick = async () => {
  const btn = $('btnCredValidate');
  btn.disabled = true; btn.textContent = '校验中…';
  try {
    const r = await api('credentials/validate', { method: 'POST', body: JSON.stringify({}) });
    renderValidateResult('cred', r);
  } catch (e) { showCredState('cred', false, '校验失败：' + e.message); }
  finally { btn.disabled = false; btn.textContent = '校验当前凭证'; }
};
async function saveCred(withValidate) {
  const content = $('credInput').value;
  if (!content.trim()) { toast('请先粘贴 cookie 内容', 'err'); return; }
  const b1 = $('btnCredSave'), b2 = $('btnCredSaveOnly');
  b1.disabled = true; b2.disabled = true;
  $('credSaveOk').hidden = true; $('credSaveErr').hidden = true;
  try {
    const r = await api('credentials', { method: 'POST', body: JSON.stringify({ content }) });
    $('credInput').value = '';
    await loadCredentials();
    toast('凭证已保存', 'ok');
    if (withValidate) {
      const v = await api('credentials/validate', { method: 'POST', body: JSON.stringify({ content }) });
      renderValidateResult('credSave', v);
    } else {
      showCredState('credSave', true, '已保存：' + r.names.length + ' 项' + (r.sapisid ? '（含 SAPISID）' : '（无 SAPISID）'));
    }
  } catch (e) {
    showCredState('credSave', false, '保存失败：' + e.message);
  } finally { b1.disabled = false; b2.disabled = false; }
}
$('btnCredSave').onclick = () => saveCred(true);
$('btnCredSaveOnly').onclick = () => saveCred(false);
$('btnCredClear').onclick = async () => {
  if (!confirm('删除当前 cookie 文件？删除后将以匿名方式访问 Gemini，可随时重新粘贴。')) return;
  try {
    await api('credentials/clear', { method: 'POST' });
    toast('已清除凭证', 'ok');
    await loadCredentials();
  } catch (e) { toast('清除失败：' + e.message, 'err'); }
};

/* ── 模型与探测 ───────────────────────────────────────────────────── */
function probeCell(p) {
  if (!p) return '<span style="color:var(--ink-3)">未探测</span>';
  return p.ok
    ? '<span class="tag ok">' + fmtLatency(p.latency_ms) + '</span>'
    : '<span class="tag bad" title="' + esc(p.error || '') + '">失败</span>';
}
async function loadModels() {
  try {
    const d = await api('models');
    modelsCache = d;
    $('mdNote').textContent = d.models.length + ' 个模型 · 默认 ' + (d.default_model || '—');
    $('mdBody').innerHTML = d.models.map(x => {
      const tags = [];
      if (x.is_default) tags.push('<span class="tag ok">默认</span>');
      if (x.enhanced) tags.push('<span class="tag warn">增强</span>');
      return '<tr>' +
        '<td class="who"><div class="nm">' + esc(x.id) + '</div><div class="id">' + tags.join(' ') + '</div></td>' +
        '<td>' + (x.is_default ? '<span class="tag ok">默认</span>' : '<span style="color:var(--ink-3)">—</span>') + '</td>' +
        '<td class="plain">' + esc(MODE_NAMES[x.mode] || x.mode) + '</td>' +
        '<td class="num">' + esc(String(x.think)) + '</td>' +
        '<td class="plain" style="color:var(--ink-2);font-size:12.5px">' + esc(x.desc || '') + '</td>' +
        '<td class="num">' + probeCell(x.probe) + '</td>' +
        '<td class="c-acts">' +
          '<button class="xs ghost" data-a="probe" data-m="' + esc(x.id) + '">探测</button>' +
          (x.is_default ? '' : '<button class="xs ghost" data-a="default" data-m="' + esc(x.id) + '">设为默认</button>') +
        '</td></tr>';
    }).join('');
  } catch (e) { toast('读取模型失败：' + e.message, 'err'); }
}
$('mdBody').addEventListener('click', async ev => {
  const b = ev.target.closest('button[data-a]');
  if (!b) return;
  const model = b.dataset.m;
  b.disabled = true;
  const old = b.textContent;
  b.textContent = b.dataset.a === 'probe' ? '探测中…' : '设置中…';
  try {
    if (b.dataset.a === 'probe') {
      const r = await api('models/probe', { method: 'POST', body: JSON.stringify({ model }) });
      const item = (r.results || [])[0] || {};
      toast(item.ok ? model + ' 探测正常（' + fmtLatency(item.latency_ms) + '）' : model + ' 探测失败：' + (item.error || ''), item.ok ? 'ok' : 'err');
    } else {
      await api('models/default', { method: 'POST', body: JSON.stringify({ model }) });
      toast('默认模型已切换为 ' + model, 'ok');
    }
    await loadModels();
    if (view === 'dashboard') loadDashboard(true);
  } catch (e) { toast(e.message, 'err'); }
  finally { b.disabled = false; b.textContent = old; }
});
$('btnProbeAll').onclick = async () => {
  const n = modelsCache ? modelsCache.models.length : '全部';
  if (!confirm('将依次对 ' + n + ' 个模型发起真实的 ping 请求，可能需要几十秒，继续？')) return;
  const btn = $('btnProbeAll');
  btn.disabled = true; btn.textContent = '探测中…';
  try {
    const r = await api('models/probe', { method: 'POST', body: JSON.stringify({}) });
    const okN = (r.results || []).filter(x => x.ok).length;
    toast('探测完成：' + okN + '/' + (r.results || []).length + ' 个模型正常', okN === (r.results || []).length ? 'ok' : 'err');
    await loadModels();
  } catch (e) { toast('探测失败：' + e.message, 'err'); }
  finally { btn.disabled = false; btn.textContent = '全部探测'; }
};
$('btnModelsReload').onclick = loadModels;

/* ── 用量 ─────────────────────────────────────────────────────────── */
function statCard(v, k, cls) {
  return '<div class="stat' + (cls ? ' ' + cls : '') + '"><div class="v">' + v + '</div><div class="k">' + esc(k) + '</div></div>';
}
async function loadUsage() {
  const hours = $('usWindow').value;
  try {
    const d = await api('usage?hours=' + encodeURIComponent(hours));
    const t = d.window_totals || d.totals || {};
    const failCls = t.errors > 0 ? 'bad' : '';
    $('usStats').innerHTML =
      statCard(fmtCount(t.requests), '请求数') +
      statCard(fmtCount(t.total_tokens), '总 tokens') +
      statCard(fmtCount(t.prompt_tokens), '输入 tokens') +
      statCard(fmtCount(t.completion_tokens), '输出 tokens') +
      statCard(fmtCount(t.errors), '失败请求', failCls) +
      statCard(fmtLatency(t.avg_latency_ms), '平均延迟');
    const since = d.since ? new Date(d.since * 1000).toLocaleString('zh-CN', { hour12: false }) : '—';
    $('usNote').textContent = (hours === '0' ? '全部历史' : '近 ' + hours + ' 小时') + ' · ' + d.buckets + ' 个分桶 · 数据自 ' + since;
    renderUsageChart(d.series || []);
    const rows = (d.by_model || []);
    $('usModelNote').textContent = rows.length ? '共 ' + rows.length + ' 个模型' : '';
    $('usModelBody').innerHTML = rows.length ? rows.map(x =>
      '<tr><td>' + esc(x.model) + '</td>' +
      '<td class="num">' + fmtCount(x.requests) + '</td>' +
      '<td class="num"' + (x.errors ? ' style="color:var(--bad)"' : '') + '>' + fmtCount(x.errors) + '</td>' +
      '<td class="num">' + fmtTok(x.prompt_tokens) + '</td>' +
      '<td class="num">' + fmtTok(x.completion_tokens) + '</td>' +
      '<td class="num">' + fmtTok(x.total_tokens) + '</td>' +
      '<td class="num">' + fmtLatency(x.avg_latency_ms) + '</td></tr>'
    ).join('') : '<tr><td colspan="7"><div class="empty">暂无数据</div></td></tr>';
  } catch (e) {
    $('usChart').innerHTML = '<div class="us-empty">读取用量失败：' + esc(e.message) + '</div>';
  }
}
function renderUsageChart(series) {
  const box = $('usChart');
  if (!series.length) { box.innerHTML = '<div class="us-empty">暂无用量数据。发起一次对话后再刷新。</div>'; return; }
  const W = 760, H = 180, P = { l: 46, r: 12, t: 10, b: 26 };
  const pts = series.map(s => {
    // 后端 t 形如 "2026-09-28T10"（本地小时桶）
    const t = new Date(s.t + ':00:00').getTime();
    const p = s.prompt_tokens || 0, c = s.completion_tokens || 0;
    return { t, p, c, n: s.requests || 0, total: p + c };
  }).filter(x => Number.isFinite(x.t));
  if (!pts.length) { box.innerHTML = '<div class="us-empty">暂无用量数据。发起一次对话后再刷新。</div>'; return; }
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const span = Math.max(3600000, t1 - t0);
  const innerW = W - P.l - P.r, innerH = H - P.t - P.b;
  const maxV = Math.max(1, ...pts.map(x => x.total));
  const x = t => P.l + (t - t0) / span * innerW;
  let minGap = Infinity;
  for (let i = 1; i < pts.length; i++) minGap = Math.min(minGap, pts[i].t - pts[i - 1].t);
  if (!Number.isFinite(minGap)) minGap = 3600000;
  const ppms = innerW / span;
  const barW = Math.max(1.5, Math.min(30, minGap * ppms * 0.7));
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const y = P.t + innerH * i / 4;
    g += '<line class="gl" x1="' + P.l + '" y1="' + y + '" x2="' + (W - P.r) + '" y2="' + y + '"/>';
    g += '<text class="tk" x="' + (P.l - 6) + '" y="' + (y + 3) + '" text-anchor="end">' + fmtTok(Math.round(maxV * (1 - i / 4))) + '</text>';
  }
  g += '<line class="ax" x1="' + P.l + '" y1="' + (P.t + innerH) + '" x2="' + (W - P.r) + '" y2="' + (P.t + innerH) + '"/>';
  let bars = '';
  for (const pt of pts) {
    const x0 = x(pt.t) - barW / 2, base = P.t + innerH;
    const hp = pt.p / maxV * innerH, hc = pt.c / maxV * innerH;
    const label = new Date(pt.t).toLocaleString('zh-CN', { hour12: false });
    const title = label + ' · 输入 ' + fmtTok(pt.p) + ' / 输出 ' + fmtTok(pt.c) + ' / ' + pt.n + ' 次';
    if (hc > 0) bars += '<rect x="' + x0 + '" y="' + (base - hp - hc) + '" width="' + barW + '" height="' + Math.max(1, hc) + '" rx="1.5" style="fill:var(--ok)"><title>' + esc(title) + '</title></rect>';
    if (hp > 0) bars += '<rect x="' + x0 + '" y="' + (base - hp) + '" width="' + barW + '" height="' + Math.max(1, hp) + '" rx="1.5" style="fill:var(--accent)"><title>' + esc(title) + '</title></rect>';
  }
  const step = Math.max(1, Math.ceil(pts.length / 6));
  let xl = '';
  for (let i = 0; i < pts.length; i += step) {
    const dte = new Date(pts[i].t);
    const lab = span > 36 * 3600000
      ? (dte.getMonth() + 1) + '-' + String(dte.getDate()).padStart(2, '0')
      : String(dte.getHours()).padStart(2, '0') + ':00';
    xl += '<text class="tk" x="' + x(pts[i].t) + '" y="' + (H - 8) + '" text-anchor="middle">' + lab + '</text>';
  }
  box.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">' + g + bars + xl + '</svg>';
}
$('btnUsage').onclick = loadUsage;
$('usWindow').onchange = loadUsage;

/* ── 配置 ─────────────────────────────────────────────────────────── */
const CFG_FIELDS = ['port', 'host', 'default_model', 'api_keys', 'gemini_bl', 'proxy', 'auth_user', 'xsrf_token', 'retry_attempts', 'retry_delay_sec', 'request_timeout_sec', 'log_requests', 'temporary_chats'];
async function loadConfig() {
  try {
    const [d, m] = await Promise.all([api('config'), api('models')]);
    modelsCache = m;
    $('cfgPath').textContent = d.path || '';
    $('cfgModel').innerHTML = m.models.map(x => '<option value="' + esc(x.id) + '">' + esc(x.id) + '</option>').join('');
    const f = $('cfgForm');
    for (const k of CFG_FIELDS) {
      const el = f.elements[k];
      if (!el) continue;
      const v = d.config[k];
      if (el.type === 'checkbox') el.checked = !!v;
      else if (k === 'api_keys') el.value = (v || []).join('\n');
      else el.value = v == null ? '' : v;
    }
    $('cfgNote').textContent = '';
  } catch (e) { toast('读取配置失败：' + e.message, 'err'); }
}
function collectConfig() {
  const f = $('cfgForm'), out = {};
  for (const k of CFG_FIELDS) {
    const el = f.elements[k];
    if (!el) continue;
    if (el.type === 'checkbox') { out[k] = el.checked; continue; }
    const raw = el.value.trim();
    if (el.type === 'number') { if (raw === '') continue; out[k] = Number(raw); continue; }
    if (k === 'api_keys') { out[k] = raw.split(/[\n,]+/).map(s => s.trim()).filter(Boolean); continue; }
    if (k === 'proxy' || k === 'xsrf_token') { out[k] = raw === '' ? null : raw; continue; }
    if (k === 'auth_user') { out[k] = raw === '' ? null : Number(raw); continue; }
    if (raw === '') continue;  // host / default_model / gemini_bl 不允许空值
    out[k] = raw;
  }
  return out;
}
$('btnCfgReload').onclick = loadConfig;
$('cfgForm').onsubmit = async ev => {
  ev.preventDefault();
  const btn = $('btnCfgSave');
  btn.disabled = true; btn.textContent = '保存中…';
  try {
    const patch = collectConfig();
    const r = await api('config', { method: 'POST', body: JSON.stringify(patch) });
    const n = (r.restart_required || []).length;
    toast(n ? '配置已保存，其中 ' + n + ' 项需重启进程生效' : '配置已保存并立即生效', 'ok');
    $('cfgNote').textContent = n ? ('需重启生效：' + r.restart_required.join('、')) : '';
    const keys = patch.api_keys;
    const k = localStorage.getItem(LS_KEY);
    if (Array.isArray(keys) && keys.length && k && !keys.includes(k)) {
      toast('当前面板密钥已不在新列表中，请重新输入', 'err');
      localStorage.removeItem(LS_KEY);
      openKey();
    }
    loadConfig();
    if (view === 'dashboard') loadDashboard(true);
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
  finally { btn.disabled = false; btn.textContent = '保存配置'; }
};

/* ── 运行日志 ─────────────────────────────────────────────────────── */
async function loadLogs() {
  const box = $('logBox');
  const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  try {
    const d = await api('logs');
    const entries = (d.entries || []).filter(e => logCh === 'all' || e.ch === logCh);
    box.innerHTML = entries.length ? entries.map(e => {
      const lvl = /error|失败|错误/i.test(e.text) ? ' e' : /warn|冷却|超时|retry|重试/i.test(e.text) ? ' w' : '';
      const t = e.ts ? new Date(e.ts).toLocaleTimeString('zh-CN', { hour12: false }) : '';
      const ch = logCh === 'all' ? '<i class="lch c-' + esc(e.ch) + '">' + ({ chat: '对话', task: '任务', sys: '系统' }[e.ch] || esc(e.ch)) + '</i>' : '';
      return '<span class="ln' + lvl + '">' + ch + esc(t + ' ' + e.text) + '</span>';
    }).join('') : '<span style="color:var(--ink-3)">暂无日志</span>';
    if (logPin && atEnd) box.scrollTop = box.scrollHeight;
    const counts = {};
    for (const e of (d.entries || [])) counts[e.ch] = (counts[e.ch] || 0) + 1;
    $('logNote').textContent = logCh === 'all'
      ? '对话 ' + (counts.chat || 0) + ' · 任务 ' + (counts.task || 0) + ' · 系统 ' + (counts.sys || 0)
      : (logCh === 'chat' ? '对话' : logCh === 'task' ? '任务' : '系统') + ' ' + entries.length + ' 行';
  } catch (e) { /* 概览轮询已提示 */ }
}
document.querySelectorAll('#logChips .chip').forEach(chip => chip.onclick = () => {
  logCh = chip.dataset.ch;
  document.querySelectorAll('#logChips .chip').forEach(c => c.classList.toggle('on', c === chip));
  loadLogs();
});
$('btnLogPin').onclick = () => {
  logPin = !logPin;
  $('btnLogPin').textContent = '自动滚动：' + (logPin ? '开' : '关');
};

/* ── 启动 ─────────────────────────────────────────────────────────── */
function start() {
  if (refTimer) clearInterval(refTimer);
  refTimer = setInterval(() => {
    if (view === 'dashboard') loadDashboard(true);
    else if (view === 'logs') loadLogs();
  }, 5000);
}
const initial = (location.hash || '#dashboard').slice(1);
go(initial in TITLES ? initial : 'dashboard');
start();
loadDashboard(true);
