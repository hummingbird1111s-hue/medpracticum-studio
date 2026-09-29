// IndexedDB queue + auto-sync. Loaded on every page via base.html.
(function () {
  const DB_NAME = 'medpracticum';
  const STORE = 'offline_logs';
  let syncing = false;
  let lastError = '';

  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'key' });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function tx(mode, fn) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const req = fn(t.objectStore(STORE));
      t.oncomplete = () => resolve(req ? req.result : undefined);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  }

  function getCookie(name) {
    const match = document.cookie.split('; ').find((c) => c.startsWith(name + '='));
    return match ? decodeURIComponent(match.split('=')[1]) : '';
  }

  async function sendLogs(logs) {
    const res = await fetch('/api/logs/sync/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken'),
      },
      body: JSON.stringify({ logs }),
    });
    if (res.status === 401 || res.status === 403) {
      const err = new Error('Please log in again.');
      err.code = 'auth';
      throw err;
    }
    return res.json();
  }

  async function queueLog(payload) {
    payload.key = payload.attachment_id + '-' + payload.date; // same day = overwrite
    payload.created_offline = true;
    await tx('readwrite', (s) => s.put(payload));
    updateBanner();
  }

  function pendingCount() {
    return tx('readonly', (s) => s.count()).catch(() => 0);
  }

  async function updateBanner() {
    const el = document.getElementById('mp-banner');
    if (!el) return;
    const n = await pendingCount();
    const offline = !navigator.onLine;
    if (!offline && n === 0) {
      el.classList.add('hidden');
      return;
    }
    const waiting = n + ' log' + (n === 1 ? '' : 's') + ' waiting to sync';
    let text = offline
      ? "You're offline" + (n ? ' · ' + waiting : ' · new logs will be saved on this device')
      : waiting + '…';
    if (lastError) text += ' (' + lastError + ')';
    el.textContent = text;
    el.classList.remove('hidden');
  }

  async function syncQueue() {
    if (syncing || !navigator.onLine) return;
    syncing = true;
    try {
      const logs = await tx('readonly', (s) => s.getAll());
      if (!logs || !logs.length) return;
      const result = await sendLogs(logs);
      for (const key of result.synced || []) {
        await tx('readwrite', (s) => s.delete(key));
      }
      lastError = result.errors && result.errors.length ? result.errors[0].error : '';
      if ((result.synced || []).length) {
        window.dispatchEvent(new CustomEvent('mp:synced', { detail: result }));
      }
    } catch (e) {
      // Stay queued; we'll try again next time.
    } finally {
      syncing = false;
      updateBanner();
    }
  }

  window.addEventListener('online', () => { updateBanner(); syncQueue(); });
  window.addEventListener('offline', updateBanner);
  document.addEventListener('DOMContentLoaded', () => { updateBanner(); syncQueue(); });

  window.MPOffline = { queueLog, syncQueue, sendLogs, pendingCount };
})();