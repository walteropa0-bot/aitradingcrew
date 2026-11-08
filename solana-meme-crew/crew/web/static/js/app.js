/* SOL·CREW dashboard – vanilla JS, no build step. */
(function () {
  'use strict';

  // ───────────────────────────── helpers ─────────────────────────────────
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function store(k, v) { try { if (v === undefined) return window.localStorage.getItem(k); window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } return null; }

  var ICONS = {
    home: '<path d="M4 10.5L12 4l8 6.5V20a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
    power: '<path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/>',
    radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.2"/><path d="M12 12l6.2-6.2"/><circle cx="12" cy="12" r=".9" fill="currentColor"/>',
    list: '<path d="M8.5 6h11.5M8.5 12h11.5M8.5 18h11.5"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
    pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    chart: '<rect x="4" y="8" width="4" height="8" rx="1"/><path d="M6 4v4M6 16v4"/><rect x="14" y="6" width="4" height="10" rx="1"/><path d="M16 3v3M16 16v5"/>',
    shield: '<path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
    stop: '<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8z"/><path d="M12 8v5M12 16h.01"/>',
    alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/>',
    ext: '<path d="M14 4h6v6"/><path d="M20 4L10 14"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    trend: '<path d="M4 17l5-6 4 4 7-9"/><path d="M15 6h5v5"/>',
    out: '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M10 17l-5-5 5-5M5 12h11"/>',
    reset: '<path d="M4 4v6h6"/><path d="M5.5 15a8 8 0 1 0 1.2-8.2L4 10"/>'
  };
  function icon(name) { return '<svg class="i" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || '') + '</svg>'; }

  var nfCache = {};
  function nf(min, max) { var k = min + '/' + max; return nfCache[k] || (nfCache[k] = new Intl.NumberFormat('de-DE', { minimumFractionDigits: min, maximumFractionDigits: max })); }
  function fmtUsd(v) { if (!isFinite(v)) return '–'; var a = Math.abs(v); return nf(2, 2).format(v) + ' $'; }
  function fmtSol(v, d) { if (!isFinite(v)) return '–'; d = d == null ? 3 : d; return nf(d, d).format(v) + ' SOL'; }
  function fmtPct(v, signed) { if (!isFinite(v)) return '–'; var s = nf(1, 1).format(v); return (signed !== false && v > 0 ? '+' : '') + s + ' %'; }
  var SUB = '₀₁₂₃₄₅₆₇₈₉';
  function fmtPrice(p) {
    if (!p || !isFinite(p)) return '–';
    if (p >= 1) return nf(2, 4).format(p);
    if (p >= 0.01) return nf(4, 6).format(p);
    var e = Math.floor(Math.log10(p)), zeros = -e - 1;
    var digits = (p * Math.pow(10, -e)).toFixed(3).replace('.', '');
    if (zeros >= 3) { return '0,0' + String(zeros).split('').map(function (d) { return SUB[+d]; }).join('') + digits; }
    return '0,' + new Array(zeros + 1).join('0') + digits;
  }
  function ago(ts, now) {
    var s = Math.max(0, Math.round((now || Date.now() / 1000) - ts));
    if (s < 5) return 'jetzt'; if (s < 90) return 'vor ' + s + 's';
    var m = Math.round(s / 60); if (m < 90) return 'vor ' + m + 'm';
    var h = Math.round(m / 60); if (h < 48) return 'vor ' + h + 'h'; return 'vor ' + Math.round(h / 24) + 'd';
  }
  function dur(s) { s = Math.max(0, Math.round(s)); if (s < 90) return s + 's'; var m = Math.round(s / 60); if (m < 120) return m + 'm'; var h = Math.floor(m / 60); return h + 'h ' + (m % 60) + 'm'; }
  function sign(v) { return v > 0 ? 'up' : v < 0 ? 'down' : ''; }
  function hue(str) { var h = 0; for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 360; return h; }
  function localISO(ms) {
    var d = new Date(ms), p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  function avatar(symbol, image) {
    var h = hue(symbol || '?'), letter = esc((symbol || '?').charAt(0).toUpperCase());
    var bg = 'linear-gradient(135deg,hsl(' + h + ',70%,48%),hsl(' + ((h + 50) % 360) + ',75%,38%))';
    var img = image ? '<img src="' + esc(image) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '';
    return '<div class="avatar" style="background:' + bg + '">' + letter + img + '</div>';
  }
  function tween(el, to, fmt, ms) {
    var from = el._v == null ? to : el._v;
    el._v = to;
    if (el._raf) cancelAnimationFrame(el._raf);
    if (from === to) { el.textContent = fmt(to); return; }
    var cls = to > from ? 'flash-up' : 'flash-down';
    el.classList.remove('flash-up', 'flash-down'); void el.offsetWidth; el.classList.add(cls);
    var t0 = performance.now();
    (function step(t) {
      var k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(from + (to - from) * e);
      if (k < 1) el._raf = requestAnimationFrame(step);
    })(t0);
  }

  // ───────────────────────────── state ───────────────────────────────────
  var S = {
    snap: null, since: 0, first: true, range: store('range') || '6h', unit: store('unit') || 'usd',
    queue: [], fails: 0, equity: null, equityAt: 0, seenTx: {}, txFirst: true, committeeKey: '', logLines: [], timer: null, cards: {},
    plotlyReady: false, panicSheet: false
  };

  function api(path, opts) {
    opts = opts || {};
    var o = { method: opts.method || 'GET', credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
    if (o.method === 'POST') { o.headers['X-Requested-With'] = 'sol-crew'; o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body || {}); }
    return fetch(path, o).then(function (r) {
      if (r.status === 401) { window.location.replace('/login'); throw new Error('auth'); }
      return r.json().then(function (j) { j._status = r.status; return j; });
    });
  }

  function toast(text, level) {
    var el = document.createElement('div'); el.className = 'toast ' + (level || 'ok'); el.textContent = text;
    $('#toasts').appendChild(el); setTimeout(function () { el.remove(); }, 3500);
  }

  // ───────────────────────────── polling ─────────────────────────────────
  function schedule(ms) { clearTimeout(S.timer); S.timer = setTimeout(poll, ms); }
  function poll() {
    if (document.hidden) { schedule(2500); return; }
    api('/api/state?since=' + S.since).then(function (snap) {
      S.fails = 0; setConn(true); onSnapshot(snap); schedule(1200);
    }).catch(function (e) {
      if (e && e.message === 'auth') return;
      S.fails++; if (S.fails >= 2) setConn(false);
      schedule(Math.min(9000, 1500 * (1 + S.fails)));
    });
  }
  function setConn(ok) { var c = $('#conn'); c.className = 'conn ' + (ok ? 'ok' : 'bad'); c.title = ok ? 'Verbunden' : 'Verbindung unterbrochen'; }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { poll(); loadEquity(true); } });

  function onSnapshot(snap) {
    var prev = S.snap; S.snap = snap;
    var evs = snap.events || [];
    if (S.first) {
      evs.forEach(function (ev) { logEvent(ev, false); if (ev.kind === 'agent') tickerSeed(ev); });
    } else {
      evs.forEach(function (ev) { logEvent(ev, true); if (ev.kind === 'agent') S.queue.push(ev); });
    }
    S.since = snap.last_seq || S.since;
    renderAll(snap, prev);
    S.first = false;
  }

  // ───────────────────────────── render ──────────────────────────────────
  function renderAll(s, prev) {
    renderHeader(s); renderBanners(s); renderHero(s); renderControl(s); renderAgents(s); renderCommittee(s);
    renderPositions(s); renderFeed(s); renderStats(s); renderSystem(s);
    if (!S.equity || Date.now() - S.equityAt > 5000) loadEquity();
  }

  function renderHeader(s) {
    var b = $('#mode-badge');
    if (s.mode === 'live') { b.className = 'badge live'; b.textContent = 'LIVE'; }
    else if (s.demo_market) { b.className = 'badge demo'; b.textContent = 'DEMO-MARKT'; }
    else { b.className = 'badge paper'; b.textContent = 'DEMO'; }
    var day = s.money.day_pnl_pct;
    document.title = 'SOL·CREW ' + (isFinite(day) ? (day >= 0 ? '▲ ' : '▼ ') + nf(1, 1).format(Math.abs(day)) + '%' : '');
  }

  function renderBanners(s) {
    var box = $('#banners'), html = '';
    (s.banners || []).forEach(function (b) {
      html += '<div class="banner ' + esc(b.level) + '">' + icon(b.level === 'info' ? 'info' : 'alert') + '<div>' + esc(b.text) + '</div></div>';
    });
    if (box._html !== html) { box._html = html; box.innerHTML = html; }
  }

  function unitInfo(s) {
    var usd = S.unit === 'usd' && s.money.sol_usd > 0;
    return { usd: usd, k: usd ? s.money.sol_usd : 1, fmt: usd ? fmtUsd : function (v) { return fmtSol(v, 3); } };
  }

  function renderHero(s) {
    var m = s.money, u = unitInfo(s);
    tween($('#hero-value'), m.equity_sol * u.k, function (v) { return u.usd ? fmtUsd(v) : nf(3, 3).format(v) + ' SOL'; }, 700);
    $$('#unit-toggle button').forEach(function (b) { b.classList.toggle('on', b.dataset.unit === (u.usd ? 'usd' : 'sol')); });
    function pnl(sel, sol, pct) {
      var box = $(sel); box.className = 'pnl ' + (Math.abs(sol) < 1e-9 ? '' : sign(sol));
      $('.pv', box).textContent = (sol >= 0 ? '+' : '') + (u.usd ? fmtUsd(sol * u.k) : fmtSol(sol, 3));
      $('.pp', box).textContent = (sol >= 0 ? '▲ ' : '▼ ') + nf(2, 2).format(Math.abs(pct)) + ' %';
    }
    pnl('#pnl-day', m.day_pnl_sol, m.day_pnl_pct); pnl('#pnl-total', m.pnl_sol, m.pnl_pct);
    $('#hero-value').classList.remove('mono');
    $('#m-cash').textContent = u.usd ? fmtUsd(m.cash_sol * u.k) : fmtSol(m.cash_sol, 2);
    $('#m-inv').textContent = u.usd ? fmtUsd(m.invested_sol * u.k) : fmtSol(m.invested_sol, 2);
    var third = $('#m-third'), usdcMode = (s.limits.profit_currency || '').toUpperCase() === 'USDC';
    $('#m-third-k').textContent = usdcMode ? 'Gesichert (USDC)' : 'Realisiert';
    third.textContent = usdcMode ? nf(2, 2).format(m.secured_usdc) + ' USDC' : (m.realized_sol >= 0 ? '+' : '') + (u.usd ? fmtUsd(m.realized_sol * u.k) : fmtSol(m.realized_sol, 3));
    third.className = 'v mono ' + (usdcMode ? '' : sign(m.realized_sol));
  }

  var STATUS = { stopped: ['BEREIT', ''], running: ['LÄUFT', 'running'], halted: ['PAUSE (LIMIT)', 'halted'], panic: ['NOT-AUS AKTIV', 'panic'] };
  function renderControl(s) {
    var e = s.engine, st = STATUS[e.state] || STATUS.stopped;
    var chip = $('#status-chip'); chip.className = 'status-chip ' + st[1]; $('#status-text').textContent = st[0];
    var bs = $('#btn-start'), txt = $('#btn-start-text'), ico = $('#btn-start-ico');
    var running = e.state === 'running' || e.state === 'halted';
    bs.classList.toggle('running', running);
    bs.disabled = e.state === 'panic';
    if (e.state === 'panic') { ico.innerHTML = icon('alert'); txt.innerHTML = 'NOT-AUS LÄUFT …'; }
    else if (running) { ico.innerHTML = icon('pause'); txt.innerHTML = 'SYSTEM PAUSIEREN'; }
    else { ico.innerHTML = icon('bolt'); txt.innerHTML = '<span class="br">[</span> SYSTEM STARTEN <span class="br">]</span>'; }
    var bp = $('#btn-panic'); bp.classList.toggle('active', e.state === 'panic');
    $('#btn-panic-ico').innerHTML = icon('stop');
    var n = s.positions.length;
    var meta = '<span>Jagd: <b>' + (e.hunting ? 'aktiv' : 'aus') + '</b></span>' +
      '<span>Schutz: <b>' + (n ? n + ' Position' + (n > 1 ? 'en' : '') + ' mit Trailing-Stop' : 'keine offenen Positionen') + '</b></span>' +
      '<span>Scans: <b>' + (e.scans || 0) + '</b>' + (e.last_scan ? ' · ' + ago(e.last_scan, s.now) : '') + '</span>';
    if (e.halt_reason) meta += '<span style="color:var(--amber)">' + esc(e.halt_reason) + '</span>';
    if (!e.ready && e.ready_error) meta += '<span style="color:var(--red)">' + esc(e.ready_error) + '</span>';
    var cm = $('#ctl-meta'); if (cm._html !== meta) { cm._html = meta; cm.innerHTML = meta; }
  }

  // ── agents ──────────────────────────────────────────────────────────────
  var STATE_LBL = { idle: 'BEREIT', analyzing: 'ANALYSIERT', confirmed: 'BESTÄTIGT', blocked: 'BLOCKIERT', waiting: 'WARTET', error: 'FEHLER' };
  function ensureCards(agents) {
    var box = $('#agents');
    if (box._built) return;
    box._built = true;
    agents.forEach(function (a) {
      var el = document.createElement('div'); el.className = 'agent'; el.dataset.id = a.id; el.dataset.state = 'idle';
      el.innerHTML = '<div class="a-ico">' + icon(a.icon) + '<span class="dot"></span></div><div class="a-main">' +
        '<div class="a-head"><div><div class="a-name">' + esc(a.name) + '</div><div class="a-role">' + esc(a.role) + '</div></div><span class="a-state">BEREIT</span></div>' +
        '<div class="a-bar"><i></i></div><div class="a-live"><span class="txt"></span><span class="caret"></span></div>' +
        '<div class="a-ticker"></div><div class="a-stats"></div></div>';
      box.appendChild(el); S.cards[a.id] = { el: el, state: 'idle', text: '', typing: null, last: '' };
    });
  }
  function setCard(id, state, message, progress, animate) {
    var c = S.cards[id]; if (!c) return;
    var el = c.el;
    if (c.state !== state) { c.state = state; el.dataset.state = state; $('.a-state', el).textContent = STATE_LBL[state] || state.toUpperCase(); }
    var bar = $('.a-bar i', el);
    if (state === 'analyzing' && (progress == null)) { bar.classList.add('indet'); bar.style.width = ''; }
    else { bar.classList.remove('indet'); bar.style.width = (progress != null ? Math.round(progress * 100) : (state === 'confirmed' ? 100 : state === 'blocked' ? 100 : 0)) + '%'; }
    if (message !== c.text) {
      if (c.last) pushTicker(el, c.last);
      c.last = message; c.text = message;
      typeInto($('.txt', el), message, c, animate);
    }
  }
  function typeInto(node, text, c, animate) {
    if (c.typing) { clearInterval(c.typing); c.typing = null; }
    if (!animate || text.length < 3) { node.textContent = text; return; }
    var i = 0, step = Math.max(1, Math.ceil(text.length / 45));
    node.textContent = '';
    c.typing = setInterval(function () { i += step; node.textContent = text.slice(0, i); if (i >= text.length) { clearInterval(c.typing); c.typing = null; } }, 16);
  }
  function pushTicker(el, line) {
    var box = $('.a-ticker', el), d = document.createElement('div'); d.textContent = line; box.appendChild(d);
    while (box.children.length > 3) box.removeChild(box.firstChild);
  }
  function tickerSeed(ev) { var c = S.cards[ev.agent]; if (c) { c.seed = c.seed || []; c.seed.push(ev.message); } }

  function renderAgents(s) {
    ensureCards(s.agents);
    s.agents.forEach(function (a) {
      var c = S.cards[a.id]; if (!c) return;
      if (S.first) {
        (c.seed || []).slice(-4, -1).forEach(function (m) { pushTicker(c.el, m); });
        setCard(a.id, a.state, a.message, a.progress, false);
      } else if (!S.queue.length && !c.typing) {
        setCard(a.id, a.state, a.message, a.progress, false);       // authoritative sync when nothing is pending
      }
      var st = a.stats || {};
      $('.a-stats', c.el).innerHTML = 'geprüft <b>' + (st.checked || 0) + '</b><span>✓ <b>' + (st.passed || 0) + '</b></span><span>✗ <b>' + (st.blocked || 0) + '</b></span>';
    });
    var sc = s.agents.filter(function (a) { return a.state === 'analyzing'; }).length;
    $('#crew-hint').textContent = sc ? sc + ' Agent' + (sc > 1 ? 'en' : '') + ' arbeitet' + (sc > 1 ? 'n' : '') : (s.engine.hunting ? 'wartet auf Signale' : 'pausiert');
  }
  var playing = false;
  function playQueue() {
    if (S.queue.length > 24) {                                     // fast-forward when far behind
      var keep = {}, tail = S.queue.slice(-6);
      S.queue.forEach(function (e) { keep[e.agent] = e; });
      S.queue = Object.keys(keep).map(function (k) { return keep[k]; }).concat(tail);
    }
    var ev = S.queue.shift();
    if (ev) setCard(ev.agent, ev.state || 'idle', ev.message, ev.progress, true);
    setTimeout(playQueue, S.queue.length > 8 ? 120 : 330);
  }
  if (!playing) { playing = true; setTimeout(playQueue, 400); }

  function renderCommittee(s) {
    var box = $('#committee'), c = s.committee;
    if (!c || !c.symbol) return;
    var key = c.ts + c.symbol + c.decision;
    if (key === S.committeeKey) return;
    S.committeeKey = key;
    var order = [['scout', 'Scout'], ['sentiment', 'Sentiment'], ['tech', 'Tech'], ['risk', 'Risiko']];
    var votes = order.map(function (o, i) {
      var v = (c.votes || {})[o[0]]; if (!v) return '';
      return '<span class="vote ' + (v.ok ? 'ok' : 'no') + '" style="animation-delay:' + (i * 140) + 'ms">' + (v.ok ? '✓' : '✗') + ' ' + o[1] + ' <b>' + esc(v.label) + '</b></span>';
    }).join('');
    box.className = 'committee';
    box.innerHTML = '<div class="cm-head"><div><div class="cm-title">Komitee-Entscheid · ' + ago(c.ts, s.now) + '</div><div class="cm-sym">$' + esc(c.symbol) +
      ' <span class="muted" style="font-weight:600;font-size:12px">Score ' + esc(c.score) + '</span></div></div><span class="verdict ' + (c.decision === 'KAUF' ? 'buy' : 'no') + '">' + esc(c.decision) +
      '</span></div><div class="votes">' + votes + '</div><div class="cm-detail">' + esc(c.detail || '') + '</div>';
  }

  // ── positions ───────────────────────────────────────────────────────────
  function renderPositions(s) {
    var box = $('#pos-list'), list = s.positions;
    $('#pos-hint').textContent = list.length ? list.length + ' offen' : '';
    if (!list.length) { box.innerHTML = '<div class="empty">Keine offenen Positionen.</div>'; return; }
    var empty = $('.empty', box); if (empty) empty.remove();
    var ids = {};
    list.forEach(function (p) {
      ids[p.id] = 1;
      var el = box.querySelector('[data-pid="' + p.id + '"]');
      if (!el) {
        el = document.createElement('div'); el.className = 'pos'; el.dataset.pid = p.id;
        el.innerHTML = '<div class="p-top">' + avatar(p.symbol, p.image) + '<div class="p-id"><div class="p-sym"><span class="sym"></span><span class="tags"></span></div><div class="p-sub"></div></div>' +
          '<div class="p-pnl"><div class="big"></div><div class="sm"></div></div></div><div class="gauge"><i></i></div>' +
          '<div class="g-lbl"><span class="gl"></span><span class="gm"></span><span class="gr"></span></div>';
        box.insertBefore(el, box.firstChild);
      }
      el.classList.toggle('exiting', !!p.exiting);
      $('.sym', el).innerHTML = '$' + esc(p.symbol) + ' <a href="' + esc(p.solscan) + '" target="_blank" rel="noopener noreferrer" class="muted" aria-label="Solscan">' + icon('ext') + '</a>';
      var tags = (p.exiting ? '<span class="tag a">VERKAUF LÄUFT</span>' : '') + (p.trailing ? '<span class="tag g">TRAILING</span>' : '<span class="tag">STOP-LOSS</span>') +
        (p.tp_done.length ? '<span class="tag b">TP ✓</span>' : '') + (p.simulated ? '' : '<span class="tag r">LIVE</span>') + (p.recovered ? '<span class="tag a">ÜBERNOMMEN</span>' : '');
      $('.tags', el).innerHTML = tags;
      $('.p-sub', el).textContent = fmtPrice(p.entry) + ' → ' + fmtPrice(p.price) + ' · ' + dur(p.age_s);
      var big = $('.big', el); big.textContent = fmtPct(p.gain_pct); big.className = 'big ' + sign(p.gain_pct);
      $('.sm', el).textContent = fmtSol(p.value_sol, 3) + ' · ' + (p.pnl_sol >= 0 ? '+' : '') + nf(3, 3).format(p.pnl_sol);
      $('.gauge i', el).style.left = (Math.max(0.02, Math.min(0.98, p.gauge)) * 100) + '%';
      $('.gl', el).textContent = 'Stop ' + fmtPrice(p.stop) + ' (−' + nf(1, 1).format(Math.max(0, p.stop_dist_pct)) + '%)';
      $('.gr', el).textContent = 'Hoch ' + fmtPrice(p.peak);
    });
    $$('.pos', box).forEach(function (el) { if (!ids[el.dataset.pid]) el.remove(); });
  }

  // ── feed ────────────────────────────────────────────────────────────────
  function renderFeed(s) {
    var box = $('#tx-list'), list = s.trades.slice(0, 30);
    $('#feed-hint').textContent = s.trades.length ? 'letzte ' + Math.min(30, s.trades.length) : '';
    if (!list.length) return;
    var empty = $('.empty', box); if (empty) empty.remove();
    var ids = {};
    list.forEach(function (t, idx) {
      ids[t.id] = 1;
      var el = box.querySelector('[data-tid="' + t.id + '"]'), isNew = false;
      if (!el) {
        isNew = !S.txFirst; el = document.createElement('div'); el.className = 'tx' + (isNew ? ' new' : ''); el.dataset.tid = t.id;
        el.innerHTML = avatar(t.symbol, t.image) + '<div class="tx-main"><div class="tx-top"></div><div class="tx-sub"></div></div><div class="tx-end"><div class="tx-pct"></div><div class="tx-time"></div></div>';
        box.insertBefore(el, box.children[idx] || null);
        if (isNew && t.side === 'SELL') toast((t.pnl_sol >= 0 ? '✅ ' : '🔻 ') + '$' + t.symbol + ' verkauft ' + fmtPct(t.pnl_pct) + ' (' + t.reason + ')', t.pnl_sol >= 0 ? 'ok' : 'bad');
        if (isNew && t.side === 'BUY') toast('🟢 Kauf $' + t.symbol + ' · ' + nf(3, 3).format(t.sol) + ' SOL', 'ok');
      }
      var buy = t.side === 'BUY', win = (t.pnl_sol || 0) >= 0;
      var badge = buy ? '<span class="tag g">KAUF</span>' : '<span class="tag ' + (win ? 'g' : 'r') + '">VERKAUF</span>';
      $('.tx-top', el).innerHTML = '$' + esc(t.symbol) + ' ' + badge + (t.simulated ? '' : ' <span class="tag r">LIVE</span>');
      $('.tx-sub', el).textContent = buy ? 'Einstieg ' + fmtPrice(t.price) + ' · ' + nf(2, 3).format(t.sol) + ' SOL' : t.reason + ' · Ausstieg ' + fmtPrice(t.price);
      var pct = $('.tx-pct', el), val = null, live = false;
      if (buy) { if (t.open && t.live_pct != null) { val = t.live_pct; live = true; } else if (t.result_pct != null) { val = t.result_pct; } }
      else { val = t.pnl_pct; }
      pct.textContent = val == null ? '–' : fmtPct(val); pct.className = 'tx-pct ' + (val == null ? 'muted' : sign(val));
      var href = t.tx || t.token;
      $('.tx-time', el).innerHTML = (live ? '<span class="live-dot"></span>live' : ago(t.ts, s.now)) +
        '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer" aria-label="Auf Solscan öffnen" title="' + (t.tx ? 'Transaktion' : 'Token') + ' auf Solscan">' + icon('ext') + '</a>';
    });
    S.txFirst = false;
    $$('.tx', box).forEach(function (el) { if (!ids[el.dataset.tid]) el.remove(); });
  }

  // ── stats + system ─────────────────────────────────────────────────────
  function renderStats(s) {
    var st = s.stats, u = unitInfo(s), grid = $('#stat-grid');
    var tiles = [
      ['Win-Rate', st.closed ? nf(0, 0).format(st.win_rate) + ' %' : '–', st.closed ? sign(st.win_rate - 50) : ''],
      ['Abgeschlossen', st.closed + ' (' + st.wins + '✓ / ' + st.losses + '✗)', ''],
      ['Bester Trade', st.closed ? fmtPct(st.best_pct) : '–', st.best_pct > 0 ? 'up' : ''],
      ['Schlechtester', st.closed ? fmtPct(st.worst_pct) : '–', st.worst_pct < 0 ? 'down' : ''],
      ['Realisiert', (st.realized_pnl_sol >= 0 ? '+' : '') + (u.usd ? fmtUsd(st.realized_pnl_sol * u.k) : fmtSol(st.realized_pnl_sol, 3)), sign(st.realized_pnl_sol)],
      ['Gebühren', u.usd ? fmtUsd(st.fees_sol * u.k) : fmtSol(st.fees_sol, 3), ''],
      ['Ø Haltedauer', st.closed ? dur(st.avg_hold_s) : '–', ''],
      ['Max. Drawdown', nf(1, 1).format(st.max_drawdown_pct) + ' %', st.max_drawdown_pct > 10 ? 'down' : '']
    ];
    var html = tiles.map(function (t) { return '<div class="mini"><div class="k">' + t[0] + '</div><div class="v mono ' + t[2] + '">' + t[1] + '</div></div>'; }).join('');
    if (grid._html !== html) { grid._html = html; grid.innerHTML = html; }
  }

  var SRC_LBL = { dexscreener: 'Dexscreener', geckoterminal: 'GeckoTerminal', rugcheck: 'RugCheck', 'solana-rpc': 'Solana RPC', jupiter: 'Jupiter', 'demo-markt': 'Demo-Markt' };
  function renderSystem(s) {
    var src = (s.sources || []).map(function (h) {
      return '<div class="src"><span class="l"><span class="sdot ' + esc(h.status) + '"></span>' + esc(SRC_LBL[h.name] || h.name) + '</span><span class="muted mono" style="font-size:12px">' +
        (h.status === 'down' ? 'pausiert' : h.status === 'idle' ? 'bereit' : (h.latency_ms ? h.latency_ms + ' ms' : 'ok')) + '</span></div>';
    }).join('');
    var sb = $('#sources'); if (sb._html !== src) { sb._html = src; sb.innerHTML = src; }
    var L = s.limits, w = s.wallet;
    var rows = [
      ['Modus', s.mode === 'live' ? 'LIVE – echtes Geld' : 'Demo – Spielgeld'],
      ['Wallet', w.address ? '<a class="mono" href="' + esc(w.explorer) + '" target="_blank" rel="noopener noreferrer">' + esc(w.short) + '</a>' : '–'],
      ['SOL-Kurs', s.money.sol_usd ? nf(2, 2).format(s.money.sol_usd) + ' $' : 'unbekannt'],
      ['Max. pro Trade', nf(2, 2).format(L.max_position_sol) + ' SOL · max. ' + L.max_open + ' Positionen'],
      ['Stop-Loss / Tageslimit', '−' + L.stop_pct + '% / −' + L.daily_loss_pct + '%'],
      ['Speicher', esc(s.engine.storage || '–')],
      ['Laufzeit', dur(s.engine.uptime_s)]
    ];
    var info = rows.map(function (r) { return '<div class="kv"><span>' + r[0] + '</span><span>' + r[1] + '</span></div>'; }).join('');
    var ib = $('#sysinfo'); if (ib._html !== info) { ib._html = info; ib.innerHTML = info; }
    var btns = '<button class="sm-btn" id="btn-selftest">' + icon('pulse') + ' Verbindungstest</button>';
    if (s.auth && s.auth.protected) btns += '<button class="sm-btn" id="btn-logout">' + icon('out') + ' Abmelden</button>';
    if (s.mode === 'paper') btns += '<button class="sm-btn" id="btn-reset">' + icon('reset') + ' Demo-Konto zurücksetzen</button>';
    var bb = $('#sys-btns'); if (bb._html !== btns) { bb._html = btns; bb.innerHTML = btns; }
  }

  function logEvent(ev, live) {
    var t = new Date(ev.ts * 1000), p = function (n) { return (n < 10 ? '0' : '') + n; };
    S.logLines.push({ cls: ev.level, text: p(t.getHours()) + ':' + p(t.getMinutes()) + ':' + p(t.getSeconds()) + '  ' + (ev.agent && ev.agent !== 'system' ? '[' + ev.agent + '] ' : '') + ev.message });
    if (S.logLines.length > 120) S.logLines.shift();
    if (live && (ev.kind === 'alert')) toast(ev.message, ev.level === 'bad' ? 'bad' : 'warn');
    var box = $('#log');
    box.innerHTML = S.logLines.slice(-60).reverse().map(function (l) { return '<div class="' + esc(l.cls) + '">' + esc(l.text) + '</div>'; }).join('');
  }

  // ───────────────────────────── chart ───────────────────────────────────
  var chartBusy = false;
  function loadEquity(force) {
    if (chartBusy && !force) return;
    chartBusy = true;
    api('/api/equity?range=' + S.range).then(function (j) { S.equity = j; S.equityAt = Date.now(); drawChart(); }).catch(function () { /* keep old */ }).then(function () { chartBusy = false; });
  }
  // Split one series into a "profit" part (>= 0, green) and a "loss" part (<= 0, red);
  // the colour switches exactly at the zero crossing (linear interpolation).
  function splitSeries(ms, ys) {
    var px = [], py = [], nx = [], ny = [], hx = [], hy = ys.slice(), i;
    for (i = 0; i < ms.length; i++) {
      var y = ys[i], x = localISO(ms[i]);
      if (i > 0) {
        var y0 = ys[i - 1];
        if ((y0 < 0 && y > 0) || (y0 > 0 && y < 0)) {
          var tt = (0 - y0) / (y - y0), xc = localISO(ms[i - 1] + (ms[i] - ms[i - 1]) * tt);
          px.push(xc); py.push(0); nx.push(xc); ny.push(0);
        }
      }
      // exact zeros belong to the green (profit) trace; they only join the red trace next to a real loss
      var negNeighbour = (ys[i - 1] < 0) || (ys[i + 1] < 0);
      px.push(x); py.push(y >= 0 ? y : null);
      nx.push(x); ny.push(y < 0 || (y === 0 && negNeighbour) ? y : null);
      hx.push(x);
    }
    return { px: px, py: py, nx: nx, ny: ny, hx: hx, hy: hy };
  }
  function drawChart() {
    var s = S.snap, e = S.equity, el = $('#chart'), empty = $('#chart-empty');
    if (!s || !e || !e.points || e.points.length < 2) { if (empty) empty.style.display = ''; return; }
    if (empty) empty.style.display = 'none';
    var u = unitInfo(s), pts = e.points, base = S.range === 'all' ? e.initial : pts[0][1];
    var ms = pts.map(function (p) { return p[0] * 1000; });
    var ys = pts.map(function (p) { return (p[1] - base) * u.k; });
    var last = ys[ys.length - 1], col = last >= 0 ? '#00f5a0' : '#ff3b5c';
    if (window.Plotly) { drawPlotly(el, ms, ys, last, col, u); } else { drawSvg(el, ms, ys, last, col, u); }
  }
  function drawPlotly(el, ms, ys, last, col, u) {
    var sp = splitSeries(ms, ys), unit = u.usd ? ' $' : ' SOL', dec = u.usd ? 2 : 3;
    var span = Math.max.apply(null, ys.map(Math.abs)), flat = span < (u.usd ? 0.005 : 0.00005);
    var posLine = flat ? '#6b7788' : '#00f5a0', posFill = flat ? 'rgba(107,119,136,0.10)' : 'rgba(0,245,160,0.15)';
    if (flat) col = '#6b7788';
    var yr = flat ? (u.usd ? [-5, 5] : [-0.05, 0.05]) : undefined;                // sensible axis for an unchanged account
    var yfmt = u.usd ? (span <= 10 ? ',.1f' : ',.0f') : (span <= 0.2 ? ',.3f' : ',.2f');
    var tspan = ms[ms.length - 1] - ms[0];
    var xfmt = (S.range === '7d' || S.range === 'all') && tspan > 86400000 ? '%d.%m.' : (tspan < 600000 ? '%H:%M:%S' : '%H:%M');
    var traces = [
      { x: sp.px, y: sp.py, type: 'scatter', mode: 'lines', line: { color: posLine, width: 2.4, shape: 'spline', smoothing: 0.4 }, fill: 'tozeroy', fillcolor: posFill, hoverinfo: 'skip', connectgaps: false },
      { x: sp.nx, y: sp.ny, type: 'scatter', mode: 'lines', line: { color: '#ff3b5c', width: 2.4, shape: 'spline', smoothing: 0.4 }, fill: 'tozeroy', fillcolor: 'rgba(255,59,92,0.15)', hoverinfo: 'skip', connectgaps: false },
      { x: sp.hx, y: sp.hy, type: 'scatter', mode: 'lines', line: { color: 'rgba(0,0,0,0)', width: 12 }, hovertemplate: '%{x|%d.%m. %H:%M:%S}<br><b>%{y:+,.' + dec + 'f}' + unit + '</b><extra></extra>' },
      { x: [sp.hx[sp.hx.length - 1]], y: [last], type: 'scatter', mode: 'markers', marker: { size: 16, color: col, opacity: 0.22 }, hoverinfo: 'skip', cliponaxis: false },
      { x: [sp.hx[sp.hx.length - 1]], y: [last], type: 'scatter', mode: 'markers', marker: { size: 7, color: col }, hoverinfo: 'skip', cliponaxis: false }
    ];
    var layout = {
      margin: { l: 4, r: 52, t: 8, b: 24 }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', showlegend: false, dragmode: false, hovermode: 'x',
      xaxis: { type: 'date', showgrid: false, zeroline: false, showline: false, fixedrange: true, nticks: 4, tickfont: { color: '#566072', size: 10 }, tickformat: xfmt, spikemode: 'across', spikecolor: '#6b7788', spikethickness: -2, spikedash: 'dot', spikesnap: 'data' },
      yaxis: { side: 'right', showgrid: true, gridcolor: 'rgba(255,255,255,0.045)', zeroline: true, zerolinecolor: 'rgba(255,255,255,0.22)', zerolinewidth: 1, fixedrange: true, nticks: 5, tickfont: { color: '#566072', size: 10 }, tickformat: yfmt, automargin: true, range: yr },
      hoverlabel: { bgcolor: '#0e1117', bordercolor: 'rgba(255,255,255,0.18)', font: { color: '#eef2f7', size: 12 } }
    };
    var cfg = { displayModeBar: false, responsive: true, scrollZoom: false, doubleClick: false };
    S.chartMs = ms;
    window.Plotly.react(el, traces, layout, cfg);
    attachChartInteraction(el);
  }
  // Plotly's own touch handlers block page scrolling on phones. A transparent overlay with
  // touch-action: pan-y takes all pointer input instead: vertical swipes scroll the page, a
  // horizontal finger/mouse movement drives the tooltip through Plotly.Fx.hover.
  function attachChartInteraction(el) {
    var ov = el.querySelector('.chart-touch');
    if (ov) return;
    ov = document.createElement('div'); ov.className = 'chart-touch'; el.appendChild(ov);
    var hideTimer = null;
    function show(ev) {
      var fl = el._fullLayout, ms = S.chartMs; if (!fl || !ms || !ms.length || !window.Plotly) return;
      var r = ov.getBoundingClientRect(), px = ev.clientX - r.left - fl._size.l;
      var t = fl.xaxis.p2c(px), lo = 0, hi = ms.length - 1;
      while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (ms[mid] < t) lo = mid; else hi = mid; }
      var idx = Math.abs(ms[lo] - t) <= Math.abs(ms[hi] - t) ? lo : hi;
      clearTimeout(hideTimer);
      try { window.Plotly.Fx.hover(el, [{ curveNumber: 2, pointNumber: idx }], 'xy'); } catch (e) { /* ignore */ }
    }
    function hide(delay) { clearTimeout(hideTimer); hideTimer = setTimeout(function () { try { window.Plotly.Fx.unhover(el); } catch (e) { /* ignore */ } }, delay); }
    ov.addEventListener('pointerdown', show);
    ov.addEventListener('pointermove', show);
    ov.addEventListener('pointerup', function () { hide(1400); });
    ov.addEventListener('pointercancel', function () { hide(0); });
    ov.addEventListener('pointerleave', function () { hide(200); });
  }
  function drawSvg(el, ms, ys, last, col, u) {            // built-in fallback if plotly.js could not be loaded
    var w = el.clientWidth || 320, h = el.clientHeight || 220, pad = 8, min = Math.min.apply(null, ys.concat([0])), max = Math.max.apply(null, ys.concat([0.0001]));
    var rng = (max - min) || 1, n = ys.length;
    function X(i) { return pad + (w - 2 * pad) * i / (n - 1); }
    function Y(v) { return h - 24 - (h - 40) * (v - min) / rng; }
    var path = '', i;
    for (i = 0; i < n; i++) path += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(ys[i]).toFixed(1);
    var z = Y(0).toFixed(1);
    el.innerHTML = '<svg width="100%" height="100%" viewBox="0 0 ' + w + ' ' + h + '"><defs><clipPath id="up"><rect x="0" y="0" width="' + w + '" height="' + z + '"/></clipPath><clipPath id="dn"><rect x="0" y="' + z + '" width="' + w + '" height="' + h + '"/></clipPath></defs>' +
      '<line x1="0" x2="' + w + '" y1="' + z + '" y2="' + z + '" stroke="rgba(255,255,255,.2)" stroke-dasharray="3 4"/>' +
      '<path d="' + path + 'L' + X(n - 1) + ' ' + z + 'L' + X(0) + ' ' + z + 'Z" fill="rgba(0,245,160,.15)" clip-path="url(#up)"/><path d="' + path + '" fill="none" stroke="#00f5a0" stroke-width="2.4" clip-path="url(#up)"/>' +
      '<path d="' + path + 'L' + X(n - 1) + ' ' + z + 'L' + X(0) + ' ' + z + 'Z" fill="rgba(255,59,92,.15)" clip-path="url(#dn)"/><path d="' + path + '" fill="none" stroke="#ff3b5c" stroke-width="2.4" clip-path="url(#dn)"/>' +
      '<circle cx="' + X(n - 1) + '" cy="' + Y(last) + '" r="4" fill="' + col + '"/></svg>';
  }

  // ───────────────────────────── controls ────────────────────────────────
  function openSheet(html) { $('#sheet-card').innerHTML = html; $('#sheet').hidden = false; }
  function closeSheet() { $('#sheet').hidden = true; $('#sheet-card').innerHTML = ''; }
  function post(action, okText) {
    return api('/api/control/' + action, { method: 'POST' }).then(function (j) {
      toast(j.message || (j.ok ? okText : 'Fehlgeschlagen'), j.ok ? 'ok' : 'bad'); poll(); return j;
    }).catch(function (e) { if (e.message !== 'auth') toast('Verbindungsfehler – bitte erneut versuchen', 'bad'); });
  }
  function confirmSheet(opts) {
    openSheet('<h3>' + opts.title + '</h3><p>' + opts.text + '</p><div class="btn-stack"><button class="big-btn ' + (opts.danger ? 'btn-panic' : 'btn-start') + '" id="sheet-ok">' + opts.ok +
      '</button><button class="big-btn btn-ghost" id="sheet-cancel">Abbrechen</button></div>');
    $('#sheet-ok').onclick = function () { closeSheet(); opts.onOk(); };
    $('#sheet-cancel').onclick = closeSheet;
  }

  document.addEventListener('click', function (ev) {
    var t = ev.target.closest('button, a'); if (!t) return;
    if (t.id === 'btn-start') {
      var s = S.snap; if (!s) return;
      if (s.engine.state === 'running' || s.engine.state === 'halted') { post('pause', 'Pausiert'); return; }
      if (s.mode === 'live') {
        confirmSheet({ title: 'LIVE-MODUS starten?', text: 'Das System handelt jetzt mit <b>echtem Geld</b> aus deiner Wallet. Meme-Coins können komplett wertlos werden. Fortfahren?', ok: 'JA, LIVE STARTEN', danger: true, onOk: function () { post('start', 'Gestartet'); } });
      } else { post('start', 'Gestartet'); }
    } else if (t.id === 'btn-panic') {
      var n = S.snap ? S.snap.positions.length : 0;
      confirmSheet({ title: 'NOT-AUS auslösen?', text: n ? 'Alle <b>' + n + ' offenen Position(en)</b> werden sofort zum Marktpreis verkauft und das System wird gestoppt. Das kann nicht rückgängig gemacht werden.' : 'Es gibt keine offenen Positionen. Das System wird gestoppt.', ok: 'JA, ALLES LIQUIDIEREN', danger: true, onOk: function () { post('panic', 'NOT-AUS ausgelöst'); } });
    } else if (t.id === 'btn-reset') {
      confirmSheet({ title: 'Demo-Konto zurücksetzen?', text: 'Spielgeld, Trades und Chart werden auf den Anfang zurückgesetzt. (Nur möglich ohne offene Positionen.)', ok: 'Zurücksetzen', danger: false, onOk: function () { post('reset', 'Zurückgesetzt'); setTimeout(function () { S.equity = null; loadEquity(true); }, 600); } });
    } else if (t.id === 'btn-selftest') {
      var out = $('#selftest-out'); t.disabled = true; out.innerHTML = '<div class="hint">Teste alle Verbindungen …</div>';
      api('/api/control/selftest', { method: 'POST' }).then(function (j) {
        out.innerHTML = (j.checks || []).map(function (c) {
          return '<div class="kv"><span>' + (c.ok ? '✅' : (c.critical ? '❌' : '⚠️')) + ' ' + esc(c.name) + '</span><span class="muted" style="text-align:right;font-size:12px;max-width:62%">' + esc(c.detail) + '</span></div>';
        }).join('') + '<div class="kv"><b>' + esc(j.message || '') + '</b><span></span></div>';
      }).catch(function () { out.innerHTML = '<div class="hint">Test fehlgeschlagen.</div>'; }).then(function () { t.disabled = false; });
    } else if (t.id === 'btn-logout') {
      api('/api/logout', { method: 'POST' }).then(function () { window.location.replace('/login'); });
    } else if (t.dataset && t.dataset.unit) {
      S.unit = t.dataset.unit; store('unit', S.unit); if (S.snap) { renderHero(S.snap); renderStats(S.snap); drawChart(); }
    } else if (t.dataset && t.dataset.r) {
      S.range = t.dataset.r; store('range', S.range); $$('#range-chips .chip').forEach(function (c) { c.classList.toggle('on', c.dataset.r === S.range); }); loadEquity(true);
    } else if (t.classList.contains('tab')) {
      var target = document.getElementById(t.dataset.t); if (target) { var y = target.getBoundingClientRect().top + window.scrollY - 70; window.scrollTo({ top: y, behavior: 'smooth' }); }
    }
  });
  $('#sheet').addEventListener('click', function (ev) { if (ev.target.id === 'sheet') closeSheet(); });

  // scroll-spy for the tab bar
  var tabs = $$('.tab'), spy = {};
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { spy[en.target.id] = en.isIntersecting ? en.intersectionRatio : 0; });
      var best = null, bv = 0; Object.keys(spy).forEach(function (k) { if (spy[k] > bv) { bv = spy[k]; best = k; } });
      if (best) tabs.forEach(function (t) { t.classList.toggle('on', t.dataset.t === best); });
    }, { rootMargin: '-15% 0px -45% 0px', threshold: [0, .1, .3, .6, 1] });
    ['home', 'control', 'crew', 'feed'].forEach(function (id) { var e = document.getElementById(id); if (e) io.observe(e); });
  }

  // ───────────────────────────── boot ────────────────────────────────────
  // broken token images fall back to the letter avatar (inline onerror would violate the CSP)
  document.addEventListener('error', function (ev) { var t = ev.target; if (t && t.tagName === 'IMG') t.remove(); }, true);
  $('#brand-mark').innerHTML = icon('trend');
  $$('[data-ico]').forEach(function (n) { n.innerHTML = icon(n.dataset.ico); });
  $$('#range-chips .chip').forEach(function (c) { c.classList.toggle('on', c.dataset.r === S.range); });
  poll();
  window.addEventListener('resize', function () { if (window.Plotly && $('#chart .js-plotly-plot')) window.Plotly.Plots.resize($('#chart')); });
})();
