// ── Detail shells: collapsible bottom sheets for flight + combo cards ───────
// ── Card Detail Shell ─────────────────────────────────────────────────────────
function CardDetailShell({ header, mapRoute, lang, journey, detailSegs, total, currency, flightPackage, cardId, flash, shareTitle, shareText }) {
  const [open, setOpen] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const fmt = (n) => formatPrice(n, currency);
  const cardRef = useRef(null);
  useEffect(() => {
    if (flash && cardRef.current) {
      setOpen(true);
      const el = cardRef.current;
      el.classList.remove('row-flash');
      void el.offsetWidth;
      el.classList.add('row-flash');
      const top = el.getBoundingClientRect().top + window.scrollY - 90;
      window.scrollTo({ top, behavior: 'smooth' });
    }
  }, [flash]);
  const shareUrl = cardId ? buildShareUrl({ trip: cardId }) : window.location.href;
  return (
    <div ref={cardRef} className="card" data-card-id={cardId} style={{ cursor: 'pointer' }} onClick={() => {setOpen((p) => !p);setShowMap(false);}}>
      <div className="card-main" style={{ display: 'flex', alignItems: 'center' }}>
        {header}
        <span className={`card-chevron${open ? ' open' : ''}`}>▾</span>
      </div>
      {open &&
      <div className="detail-panel" onClick={(e) => e.stopPropagation()}>
          {journey && <JourneyDiagram nodes={journey.nodes} segs={journey.segs} lang={lang} />}
          {detailSegs && <DetailGrid segs={detailSegs} currency={currency} lang={lang} flightPackage={flightPackage} />}
          {total &&
        <div className="dp-total-row">
              <span className="dp-total-label">{lang === 'tr' ? 'Toplam fiyat' : 'Total price'}</span>
              <span className="dp-total-price">{fmt(total)}</span>
            </div>
        }
          <div className="dp-actions" style={{ gap: 8 }}>
            <button className={`dp-map-btn${showMap ? ' active' : ''}`} onClick={() => setShowMap((p) => !p)}>
              🗺 {showMap ? lang === 'tr' ? 'Haritayı Gizle' : 'Hide Map' : lang === 'tr' ? 'Haritada Gör' : 'Show on Map'}
            </button>
            <button className="dp-map-btn" onClick={(e) => { e.stopPropagation(); shareTrip({ url: shareUrl, title: shareTitle, text: shareText, lang }); }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ display: 'inline-block', verticalAlign: 'middle' }}>
                <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
              </svg>{' '}{lang === 'tr' ? 'Paylaş' : 'Share'}
            </button>
          </div>
          {showMap && <InlineMap route={mapRoute} onClose={() => setShowMap(false)} lang={lang} />}
        </div>
      }
    </div>);

}

function FlightOperationalDetailShell({ header, mapRoute, lang, detailSegs, totalPrice, totalDuration, stops, currency, buyUrl, cardId, flash, shareTitle, shareText }) {
  const [open, setOpen] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const cardRef = useRef(null);
  useEffect(() => {
    if (flash && cardRef.current) {
      setOpen(true);
      const el = cardRef.current;
      el.classList.remove('row-flash');
      void el.offsetWidth;
      el.classList.add('row-flash');
      const top = el.getBoundingClientRect().top + window.scrollY - 90;
      window.scrollTo({ top, behavior: 'smooth' });
    }
  }, [flash]);
  const shareUrl = cardId ? buildShareUrl({ trip: cardId }) : window.location.href;
  const t = T[lang || 'tr'];
  const fmt = (n) => formatPrice(n, currency);
  const segs = Array.isArray(detailSegs) ? detailSegs : [];
  const first = segs[0] || {};
  const last = segs[segs.length - 1] || {};
  const refs = segs.map((seg) => seg.ref).filter(Boolean);
  const airline = first.carrier || '';
  const legCount = Math.max(segs.length, 1);
  const skyCopy = lang === 'tr' ? "Skyscanner'da Ara" : 'Search on Skyscanner';
  const personCopy = lang === 'tr' ? '/ kişi' : '/ person';
  const totalCopy = lang === 'tr' ? 'Toplam süre' : 'Total time';
  const stopCopy = stops === 0 ? t.nonstop : `${stops} ${t.transfer}`;

  return (
    <div ref={cardRef} className="card" data-card-id={cardId} style={{ cursor: 'pointer' }} onClick={() => {setOpen((p) => !p);setShowMap(false);}}>
      <div className="card-main" style={{ display: 'flex', alignItems: 'center' }}>
        {header}
        <span className={`card-chevron${open ? ' open' : ''}`}>▾</span>
      </div>
      {open &&
        <div className="flight-op-panel" onClick={(e) => e.stopPropagation()}>
          <div className="flight-op-grid">
            <aside className="flight-op-side">
              <div className="flight-op-side-art" aria-hidden="true">
                <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M8 56h48" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"/>
                  <path d="M14 56V40h12v16" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round"/>
                  <rect x="32" y="20" width="10" height="36" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round"/>
                  <path d="M37 20V10" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"/>
                  <circle cx="37" cy="8" r="2" fill="currentColor"/>
                  <path d="M32 30h10M32 38h10M32 46h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
                  <path d="M48 56V34l8-4v26" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round"/>
                </svg>
              </div>
              <div className="flight-op-kicker">✈ {t.flightPackage}</div>
              <div className="flight-op-airline">{airline}</div>
              {refs.length > 0 && <div className="flight-op-refs">{refs.join(' · ')}</div>}
              <div className="flight-op-pills">
                <span className="flight-op-pill"><b>{legCount}</b> {lang === 'tr' ? 'bacak' : (legCount === 1 ? 'leg' : 'legs')}</span>
                <span className="flight-op-pill"><b>{stops}</b> {(t.transfer || '').toLowerCase()}</span>
              </div>
            </aside>

            <main className="flight-op-main">
              <div className="flight-op-route">
                <div className="flight-op-route-end">
                  <div className="flight-op-route-time">{first.dep}</div>
                  <div className="flight-op-route-place"><b>{first.from}</b>{first.fromName || CITY_NAMES[first.from] || ''}</div>
                </div>
                <div className="flight-op-route-rail">
                  <div className="flight-op-rail-line" />
                  <span className="flight-op-rail-chip">✈ {totalDuration}</span>
                  <div className="flight-op-rail-line" />
                </div>
                <div className="flight-op-route-end right">
                  <div className="flight-op-route-time">{last.arr}{last.nextDay && <span className="dg2-nextday">+1</span>}</div>
                  <div className="flight-op-route-place"><b>{last.to}</b>{last.toName || CITY_NAMES[last.to] || ''}</div>
                </div>
              </div>

              <div className="flight-op-table">
                <div className="flight-op-head">
                  <div>{lang === 'tr' ? 'Operatör' : 'Operator'}</div>
                  <div>{lang === 'tr' ? 'Uçuş rotası' : 'Flight route'}</div>
                </div>
                {segs.map((seg, i) => {
                  const prev = i > 0 ? segs[i - 1] : null;
                  const layover = prev ? calcLayover(prev, seg) : null;
                  const where = prev ? (prev.toName || CITY_NAMES[prev.to] || prev.to) : '';
                  return (
                    <React.Fragment key={`${seg.ref || i}-${seg.from}-${seg.to}`}>
                      {i > 0 &&
                        <div className="flight-op-layover">
                          ⏱ {layover ? `${layover} ${t.layover}` : t.layover}
                          {where ? ` · ${where} (${prev.to})` : ''}
                        </div>
                      }
                      <div className="flight-op-row">
                        <div className="flight-op-carrier">
                          <div className="flight-op-carrier-name">{seg.carrier}</div>
                          {seg.ref && <div className="flight-op-carrier-ref">{seg.ref}</div>}
                        </div>
                        <div className="flight-op-leg-route">
                          <div>
                            <div className="flight-op-leg-time">{seg.dep}</div>
                            <div className="flight-op-leg-place"><b>{seg.from}</b>{seg.fromName || CITY_NAMES[seg.from] || ''}</div>
                          </div>
                          <div className="flight-op-leg-mid">
                            <div className="flight-op-leg-duration">{seg.duration}</div>
                            <div className="flight-op-leg-line"><span className="flight-op-leg-icon">✈</span></div>
                          </div>
                          <div style={{ textAlign: 'right' }}>
                            <div className="flight-op-leg-time">{seg.arr}{seg.nextDay && <span className="dg2-nextday">+1</span>}</div>
                            <div className="flight-op-leg-place"><b>{seg.to}</b>{seg.toName || CITY_NAMES[seg.to] || ''}</div>
                          </div>
                        </div>
                      </div>
                    </React.Fragment>
                  );
                })}
              </div>
            </main>

            <aside className="flight-op-action">
              <div>
                <div className="flight-op-price">{fmt(totalPrice)}</div>
                <div className="flight-op-price-sub">{personCopy}<br />{totalCopy} · {stopCopy}</div>
              </div>
              <div className="flight-op-action-stack">
                <a href={buyUrl || 'https://www.skyscanner.com.tr/'} target="_blank" rel="noopener noreferrer"
                  className="flight-op-primary" onClick={(e) => e.stopPropagation()}>
                  ✈ {skyCopy} ↗
                </a>
                <button className={`flight-op-secondary${showMap ? ' active' : ''}`} onClick={() => setShowMap((p) => !p)}>
                  🗺 {showMap ? t.mapHideBtn : t.mapShowBtn}
                </button>
                <ShareButton url={shareUrl} title={shareTitle} text={shareText} lang={lang} />
              </div>
            </aside>
          </div>
          {showMap && <InlineMap route={mapRoute} onClose={() => setShowMap(false)} lang={lang} />}
        </div>
      }
    </div>
  );
}
