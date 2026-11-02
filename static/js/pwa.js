/* ═══════════════════════════════════════════════════════════
   WTI PWA — Service Worker Registration & Install Prompt
   ═══════════════════════════════════════════════════════════ */

'use strict';

// ── Register Service Worker ─────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(reg => {
        console.log('[WTI PWA] Service Worker registered:', reg.scope);

        // Check for updates every 60 seconds
        setInterval(() => reg.update(), 60000);

        reg.addEventListener('updatefound', () => {
          const newWorker = reg.installing;
          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              showUpdateBanner();
            }
          });
        });
      })
      .catch(err => console.warn('[WTI PWA] SW registration failed:', err));
  });
}

// ── Install Prompt (Add to Home Screen) ─────────────────────
let deferredPrompt = null;

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  showInstallBanner();
});

window.addEventListener('appinstalled', () => {
  deferredPrompt = null;
  hideInstallBanner();
  console.log('[WTI PWA] App installed successfully');
});

function showInstallBanner() {
  // Don't show if already in standalone mode
  if (window.matchMedia('(display-mode: standalone)').matches) return;
  if (window.navigator.standalone === true) return;

  const banner = document.getElementById('pwaInstallBanner');
  if (banner) {
    banner.style.display = 'flex';
    // Auto-hide after 15 seconds
    setTimeout(() => hideInstallBanner(), 15000);
  }
}

function hideInstallBanner() {
  const banner = document.getElementById('pwaInstallBanner');
  if (banner) {
    banner.style.animation = 'slideOutDown 0.4s ease forwards';
    setTimeout(() => { banner.style.display = 'none'; }, 400);
  }
}

window.installPWA = function () {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  deferredPrompt.userChoice.then(choice => {
    if (choice.outcome === 'accepted') {
      console.log('[WTI PWA] User accepted install');
    }
    deferredPrompt = null;
    hideInstallBanner();
  });
};

window.dismissInstall = function () {
  hideInstallBanner();
  // Remember dismissal for 24 hours
  localStorage.setItem('pwa_dismissed', Date.now());
};

// ── Update Banner ────────────────────────────────────────────
function showUpdateBanner() {
  const banner = document.getElementById('pwaUpdateBanner');
  if (banner) banner.style.display = 'flex';
}

window.applyUpdate = function () {
  navigator.serviceWorker.getRegistration().then(reg => {
    if (reg && reg.waiting) {
      reg.waiting.postMessage({ type: 'SKIP_WAITING' });
    }
  });
  window.location.reload();
};

// ── Online/Offline Status ────────────────────────────────────
function updateOnlineStatus() {
  const indicator = document.getElementById('onlineIndicator');
  if (!indicator) return;
  if (navigator.onLine) {
    indicator.style.display = 'none';
  } else {
    indicator.style.display = 'flex';
  }
}

window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);
document.addEventListener('DOMContentLoaded', updateOnlineStatus);

// ── Check if already dismissed recently ─────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const dismissed = localStorage.getItem('pwa_dismissed');
  if (dismissed && Date.now() - parseInt(dismissed) < 86400000) {
    // Dismissed within 24 hours — don't show
    const banner = document.getElementById('pwaInstallBanner');
    if (banner) banner.remove();
  }
});
