(function () {
  // 1. Register the service worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/service-worker.js', { scope: '/' }).catch(() => {});
    });
  }

  // 2. "Install app" button (Chrome/Edge/Android). iPhones use Share → Add to Home Screen.
  let deferredPrompt = null;
  const btn = document.getElementById('install-btn');

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (btn) btn.classList.remove('hidden');
  });

  if (btn) {
    btn.addEventListener('click', async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      btn.classList.add('hidden');
    });
  }

  window.addEventListener('appinstalled', () => {
    if (btn) btn.classList.add('hidden');
  });

  // 3. On logout, wipe saved pages so the next person on a shared phone can't see them
  document.querySelectorAll('form[data-logout]').forEach((form) => {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const keys = await caches.keys();
        await Promise.all(keys.filter((k) => k.startsWith('mp-pages-')).map((k) => caches.delete(k)));
      } catch (err) { /* ignore */ }
      form.submit();
    });
  });
})();