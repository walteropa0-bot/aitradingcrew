(function () {
  'use strict';
  var mark = document.getElementById('brand-mark');
  if (mark) {
    mark.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" width="17" height="17"><path d="M4 17l5-6 4 4 7-9"/><path d="M15 6h5v5"/></svg>';
  }
  var form = document.getElementById('form');
  var err = document.getElementById('err');
  var go = document.getElementById('go');
  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    err.textContent = '';
    go.disabled = true;
    fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'sol-crew' },
      body: JSON.stringify({ password: document.getElementById('pw').value }),
      credentials: 'same-origin'
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) { window.location.replace('/'); return; }
        err.textContent = res.j.message || 'Anmeldung fehlgeschlagen.';
        go.disabled = false;
      })
      .catch(function () { err.textContent = 'Keine Verbindung zum Server.'; go.disabled = false; });
  });
})();
