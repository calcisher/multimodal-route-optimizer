// ── Share helpers — native share + clipboard fallback + URL deeplink ─────────

function buildShareUrl(params) {
  const url = new URL(window.location.href);
  url.search = '';
  Object.entries(params).forEach(([k, v]) => {
    if (v != null && v !== '') url.searchParams.set(k, String(v));
  });
  return url.toString();
}

function showShareToast(msg) {
  let el = document.getElementById('__share_toast');
  if (!el) {
    el = document.createElement('div');
    el.id = '__share_toast';
    el.className = 'share-toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 1800);
}

async function shareTrip({ url, title, text, lang }) {
  const copiedMsg = lang === 'tr' ? 'Link kopyalandı ✓' : 'Link copied ✓';
  const errMsg = lang === 'tr' ? 'Kopyalanamadı' : 'Could not copy';
  try {
    await navigator.clipboard.writeText(url);
    showShareToast(copiedMsg);
  } catch (e) {
    // Fallback: create a temporary input and copy from it
    try {
      const inp = document.createElement('input');
      inp.value = url;
      inp.style.position = 'fixed';
      inp.style.opacity = '0';
      document.body.appendChild(inp);
      inp.focus();
      inp.select();
      document.execCommand('copy');
      document.body.removeChild(inp);
      showShareToast(copiedMsg);
    } catch (e2) {
      showShareToast(errMsg);
    }
  }
}

function ShareButton({ url, title, text, lang }) {
  return (
    <button
      className="share-btn"
      onClick={(e) => { e.stopPropagation(); shareTrip({ url, title, text, lang }); }}
      title={lang === 'tr' ? 'Bu seferi paylaş' : 'Share this trip'}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
        <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
      </svg>
      {lang === 'tr' ? 'Paylaş' : 'Share'}
    </button>
  );
}
