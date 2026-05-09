// ── App: top-level state, search orchestration ────────────────────────────────
const { useState, useEffect, useRef, useMemo } = React;

// Distinct-airport palette for the multi-airport-arrival color cue. Picks
// from this in order; if the user happens to fly to >5 different airports
// in one search, we wrap (still readable, cycle is not semantic).

function App() {
  const [lang, setLang] = useState('en');
  const [tweaks, setTweaks] = useState(TWEAK_DEFAULTS);
  const [showTweaks, setShowTweaks] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [date, setDate] = useState('');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingCards, setLoadingCards] = useState({ flights: false, flightBus: false, busFlight: false, busFlightBus: false, busOrTrain: false });
  const [errorCards, setErrorCards] = useState({ flights: null, flightBus: null, busFlight: null, busFlightBus: null, busOrTrain: null });
  const [activeSec, setActiveSec] = useState(0);
  const [filter, setFilter] = useState(FILTER_DEFAULTS);
  const [filterOpen, setFilterOpen] = useState(false);
  const [airports, setAirports] = useState([]);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiCache, setAiCache] = useState(null); // { picks, summary } — persists across open/close
  const [flashId, setFlashId] = useState(null);
  const t = T[lang];
  const resultsRef = useRef(null);
  const lastBodyRef = useRef(null); // Keeps latest search body so visibilitychange can retry failed endpoints.

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', tweaks.theme || 'light');
  }, [tweaks.theme]);

  useEffect(() => {
    setDate('2026-05-20');

    fetch('/api/airports')
      .then((r) => r.json())
      .then((data) => setAirports(Array.isArray(data?.airports) ? data.airports : []))
      .catch((err) => console.error('/api/airports failed:', err));
  }, []);

  // Restore search from URL on first mount: ?from=Venice&to=Nürnberg&date=...
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fromParam = params.get('from');
    const toParam = params.get('to');
    const dateParam = params.get('date');
    if (!fromParam || !toParam || !dateParam) return;
    setFrom(fromParam);
    setTo(toParam);
    setDate(dateParam);
    doSearch({ from: fromParam, to: toParam, date: dateParam });
  }, []);

  function mergeAirports(data) {
    if (!data || !data.airports) return;
    for (const [iata, info] of Object.entries(data.airports)) {
      if (info && typeof info.lat === 'number' && typeof info.lon === 'number') {
        LATLNG[iata] = [info.lat, info.lon];
      }
      if (info && info.city && !CITY_NAMES[iata]) {
        CITY_NAMES[iata] = info.city;
      }
    }
  }

  async function fetchPart(url, body) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: 'HTTP ' + resp.status }));
      throw new Error(err.error || 'HTTP ' + resp.status);
    }
    return resp.json();
  }

  function swap() { setFrom(to); setTo(from); }

  function updateTweak(key, val) {
    setTweaks((prev) => ({ ...prev, [key]: val }));
  }

  // Per-endpoint config so retry can re-run a single one without duplicating logic.
  const ENDPOINT_DEFS = {
    flights: {
      url: '/api/flights',
      apply: (data) => setResults((prev) => ({
        ...(prev || {}),
        bestFlights: data.bestFlights ?? [],
        cheapFlights: data.cheapFlights ?? [],
        resolved: data.resolved ?? null,
      })),
    },
    flightBus: {
      url: '/api/flight-plus-bus',
      apply: (data) => setResults((prev) => ({
        ...(prev || {}),
        flightPlusBus: data.flightPlusBus ?? [],
      })),
    },
    busFlight: {
      url: '/api/bus-plus-flight',
      apply: (data) => setResults((prev) => ({
        ...(prev || {}),
        busPlusFlight: data.busPlusFlight ?? [],
      })),
    },
    busFlightBus: {
      url: '/api/bus-flight-bus',
      apply: (data) => setResults((prev) => ({
        ...(prev || {}),
        busFlightBus: data.busFlightBus ?? [],
      })),
    },
    busOrTrain: {
      url: '/api/trains',
      apply: (data) => {
        if (data.fromCoords && data.fromCity) LATLNG[data.fromCity] = [data.fromCoords.lat, data.fromCoords.lon];
        if (data.toCoords   && data.toCity)   LATLNG[data.toCity]   = [data.toCoords.lat,   data.toCoords.lon];
        for (const r of (data.trains ?? [])) {
          if (r.from && data.fromCoords) LATLNG[r.from] = [data.fromCoords.lat, data.fromCoords.lon];
          if (r.to   && data.toCoords)   LATLNG[r.to]   = [data.toCoords.lat,   data.toCoords.lon];
          for (const wp of (r.waypoints ?? [])) {
            if (wp.name && wp.lat != null && wp.lon != null) LATLNG[wp.name] = [wp.lat, wp.lon];
          }
        }
        setResults((prev) => ({
          ...(prev || {}),
          busOrTrain: data.trains ?? [],
        }));
      },
    },
  };

  function runEndpoint(key, body) {
    const def = ENDPOINT_DEFS[key];
    setLoadingCards((prev) => ({ ...prev, [key]: true }));
    setErrorCards((prev) => ({ ...prev, [key]: null }));
    return fetchPart(def.url, body)
      .then((data) => {
        mergeAirports(data);
        def.apply(data);
      })
      .catch((err) => {
        console.error(def.url + ' failed:', err);
        setErrorCards((prev) => ({ ...prev, [key]: err?.message || 'Request failed' }));
      })
      .finally(() => setLoadingCards((prev) => ({ ...prev, [key]: false })));
  }

  async function doSearch(override) {
    const fromVal = override?.from ?? from;
    const toVal = override?.to ?? to;
    const dateVal = override?.date ?? date;
    if (!fromVal || !toVal) return;
    const body = { from_city: fromVal, to_city: toVal, date: dateVal };
    lastBodyRef.current = body;

    const urlParams = new URLSearchParams();
    urlParams.set('from', fromVal);
    urlParams.set('to', toVal);
    urlParams.set('date', dateVal);
    const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
    if (newUrl !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', newUrl);
    }

    setLoading(true);
    setErrorCards({ flights: null, flightBus: null, busFlight: null, busFlightBus: null, busOrTrain: null });
    setFilter(FILTER_DEFAULTS);
    setFilterOpen(false);
    setAiOpen(false);
    setAiCache(null);
    setFlashId(null);
    setResults({ bestFlights: [], cheapFlights: [], flightPlusBus: [], busPlusFlight: [], busFlightBus: [], busOrTrain: [] });
    setActiveSec(0);

    requestAnimationFrame(() => {
      const el = resultsRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY - 24;
      window.scrollTo({ top, behavior: 'smooth' });
    });

    await Promise.all([
      runEndpoint('flights', body),
      runEndpoint('flightBus', body),
      runEndpoint('busFlight', body),
      runEndpoint('busFlightBus', body),
      runEndpoint('busOrTrain', body),
    ]);
    setLoading(false);
  }

  function retryEndpoint(key) {
    if (!lastBodyRef.current) return;
    runEndpoint(key, lastBodyRef.current);
  }

  // When the tab regains visibility, re-fire any endpoints that errored out
  // (typically Chrome's net::ERR_NETWORK_IO_SUSPENDED on backgrounded tabs).
  // Cache makes retries near-instant.
  useEffect(() => {
    function onVisible() {
      if (document.visibilityState !== 'visible' || !lastBodyRef.current) return;
      for (const key of Object.keys(ENDPOINT_DEFS)) {
        if (errorCards[key]) runEndpoint(key, lastBodyRef.current);
      }
    }
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [errorCards]);

  const cur = tweaks.currency || 'EUR';
  const fmt = (n) => formatPrice(n, cur);

  const minPriceOf = (arr, key) => {
    if (!arr || !arr.length) return null;
    const prices = arr.map((d) => d[key]).filter((p) => typeof p === 'number' && isFinite(p));
    return prices.length ? Math.min(...prices) : null;
  };

  const { filtered: filteredResults, removed } = applyFilters(results, filter);
  const fActive = isFilterActive(filter);
  const fCount = activeFilterCount(filter);

  // When the destination city has multiple airports, SerpAPI returns flights
  // that land at different IATAs. Build a stable IATA→color map across both
  // best+cheap so users can spot at a glance which airport each flight uses.
  // Returns null when there's only one arrival airport (no cue needed).
  const arrColorMap = useMemo(() => {
    const flights = [
      ...(filteredResults?.bestFlights || []),
      ...(filteredResults?.cheapFlights || []),
    ];
    const distinct = [...new Set(flights.map((f) => f.arrIata).filter(Boolean))];
    if (distinct.length < 2) return null;
    const map = {};
    distinct.forEach((iata, i) => { map[iata] = ARR_AIRPORT_PALETTE[i % ARR_AIRPORT_PALETTE.length]; });
    return map;
  }, [filteredResults?.bestFlights, filteredResults?.cheapFlights]);

  // Mirror of arrColorMap for the departure side — kicks in when the origin
  // city has multiple airports (MXP+LIN), so the LIN flights stand out from
  // the MXP ones at a glance. Same palette; no clash because dep and arr
  // never share an IATA on the same flight.
  const depColorMap = useMemo(() => {
    const flights = [
      ...(filteredResults?.bestFlights || []),
      ...(filteredResults?.cheapFlights || []),
    ];
    const distinct = [...new Set(flights.map((f) => f.depIata).filter(Boolean))];
    if (distinct.length < 2) return null;
    const map = {};
    distinct.forEach((iata, i) => { map[iata] = ARR_AIRPORT_PALETTE[i % ARR_AIRPORT_PALETTE.length]; });
    return map;
  }, [filteredResults?.bestFlights, filteredResults?.cheapFlights]);

  const CATS = [
    { icon: '✈️', label: t.bestFlight, sub: t.bestFlightSub, color: '#2563EB', data: filteredResults?.bestFlights, minPrice: minPriceOf(filteredResults?.bestFlights, 'price'), loading: loadingCards.flights, error: errorCards.flights, errorKey: 'flights', removed: removed[0] },
    { icon: '💶', label: t.cheapFlight, sub: t.cheapFlightSub, color: '#16A34A', data: filteredResults?.cheapFlights, minPrice: minPriceOf(filteredResults?.cheapFlights, 'price'), loading: loadingCards.flights, error: errorCards.flights, errorKey: 'flights', removed: removed[1] },
    { icon: '✈🚌', label: t.flightBus, sub: t.flightBusSub, color: '#7C3AED', data: filteredResults?.flightPlusBus, minPrice: minPriceOf(filteredResults?.flightPlusBus, 'minTotal'), loading: loadingCards.flightBus, error: errorCards.flightBus, errorKey: 'flightBus', removed: removed[2] },
    { icon: '🚌✈', label: t.busFlight, sub: t.busFlightSub, color: '#0891B2', data: filteredResults?.busPlusFlight, minPrice: minPriceOf(filteredResults?.busPlusFlight, 'minTotal'), loading: loadingCards.busFlight, error: errorCards.busFlight, errorKey: 'busFlight', removed: removed[3] },
    { icon: '🚌✈🚌', label: t.busFlightBus, sub: t.busFlightBusSub, color: '#D97706', data: filteredResults?.busFlightBus, minPrice: minPriceOf(filteredResults?.busFlightBus, 'minTotal'), loading: loadingCards.busFlightBus, error: errorCards.busFlightBus, errorKey: 'busFlightBus', removed: removed[4] },
    { icon: '🚌🚆', label: t.busOnly, sub: t.busOnlySub, color: '#475569', data: filteredResults?.busOrTrain, minPrice: minPriceOf(filteredResults?.busOrTrain, 'price'), loading: loadingCards.busOrTrain, error: errorCards.busOrTrain, errorKey: 'busOrTrain', removed: removed[5] },
  ];

  const activeCat = CATS[activeSec];
  const sectionEmpty = activeCat && !activeCat.loading && (!activeCat.data || activeCat.data.length === 0);

  return (
    <>
      <header className="hdr" style={{ fontFamily: '"Space Grotesk"' }}>
        <div className="logo">
          <div className="logo-icon">
            <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M7 23 Q 12 14 16 14 Q 20 14 25 9" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeDasharray="2.5 2.5" fill="none" />
              <circle cx="7" cy="23" r="3" fill="#fff" />
              <circle cx="16" cy="14" r="2.2" fill="#fff" fillOpacity="0.55" />
              <circle cx="25" cy="9" r="3" fill="#fff" />
            </svg>
          </div>
          Multi Route
        </div>
        <div className="hdr-right">
          <button className="theme-btn" onClick={() => setShowTweaks((p) => !p)} aria-label={t.tweaks}>⚙</button>
          <button className="theme-btn" onClick={() => updateTweak('theme', tweaks.theme === 'dark' ? 'light' : 'dark')}>
            {tweaks.theme === 'dark' ? '☀ Light' : '🌙 Dark'}
          </button>
          <div className="lang-toggle">
            <button className={`lang-btn${lang === 'tr' ? ' active' : ''}`} onClick={() => setLang('tr')}>TR</button>
            <button className={`lang-btn${lang === 'en' ? ' active' : ''}`} onClick={() => setLang('en')}>EN</button>
          </div>
        </div>
      </header>

      <section className="hero">
        <div style={{ maxWidth: 900 }}>
          <div className="hero-title">Multi Route</div>
          <div className="hero-sub">{t.tagline}</div>
          <div className="search-box">
            <CityInput label={t.from} value={from} onChange={setFrom} lang={lang} airports={airports} />
            <button className="swap-btn" onClick={swap}>⇄</button>
            <CityInput label={t.to} value={to} onChange={setTo} lang={lang} airports={airports} />
            <div className="sf-field sf-date">
              <div className="sf-label">{t.date}</div>
              <div className="sf-input-wrap">
                <input type="date" className="sf-input" aria-label={t.date} value={date} onChange={(e) => setDate(e.target.value)} />
              </div>
            </div>
            <button className="search-btn" onClick={doSearch} disabled={loading}>
              {loading ? t.searching : `🔍 ${t.search}`}
            </button>
          </div>
          {!results && !loading && (
            <div
              style={{ marginTop: 14, color: 'rgba(255,255,255,.6)', fontSize: 13, cursor: 'pointer', userSelect: 'none' }}
              onClick={() => { setFrom('Milan'); setTo('Nuremberg'); doSearch({ from: 'Milan', to: 'Nuremberg' }); }}>
              💡 {t.trySearch}
            </div>
          )}
        </div>
      </section>

      <main className="results" ref={resultsRef}>
        {results && (
          <>
            {loading && <div className="loading-bar" />}
            <div className="res-info">
              <b>{results.resolved ? `${results.resolved.fromCity} (${results.resolved.from})` : from}</b>
              {' → '}
              <b>{results.resolved ? `${results.resolved.toCity} (${results.resolved.to})` : to}</b>
              · {date}
            </div>

            {/* FILTER BAR */}
            <div className="filter-bar">
              <button className={`filter-toggle${filterOpen ? ' open' : ''}`} onClick={() => setFilterOpen((p) => !p)}>
                ⚙ {t.advFilter}
                {fCount > 0 && <span className="filter-toggle-badge">{fCount}</span>}
                <span style={{ fontSize: 11, opacity: .6 }}>{filterOpen ? '▴' : '▾'}</span>
              </button>
              {!aiOpen ? (
                <button className="ai-suggest-btn" onClick={() => setAiOpen(true)}>
                  ✨ {lang === 'tr' ? 'AI Önerisi Al' : 'Get AI Suggestion'}
                </button>
              ) : null}
              <ActiveFilterChips filter={filter} setFilter={setFilter} lang={lang} />
            </div>
            {filterOpen && <FilterPanel filter={filter} setFilter={setFilter} lang={lang} />}
            {aiOpen && (
              <AiSuggestPanel
                results={filteredResults}
                currency={cur}
                lang={lang}
                initialCache={aiCache}
                onResult={(picks, summary) => setAiCache({ picks, summary })}
                onClose={() => setAiOpen(false)}
                onPick={(secIdx, id) => {
                  setActiveSec(secIdx);
                  setAiOpen(false);
                  setFlashId(null);
                  requestAnimationFrame(() => setFlashId(id));
                }}
              />
            )}

            {/* CATEGORY BAR */}
            <div className="cat-bar">
              {CATS.map((c, i) => (
                <div key={i} className={`cat-pill${activeSec === i ? ' active' : ''}${c.error ? ' err' : ''}`} onClick={() => setActiveSec(i)}>
                  <span className="cat-pill-icon">{c.icon}</span>
                  <span className="cat-pill-label">{c.label}</span>
                  <span className="cat-pill-price">
                    {c.error ? '⚠' : (c.loading ? <span className="cat-pill-spinner" /> : (c.minPrice != null ? fmt(c.minPrice) : '—'))}
                  </span>
                  <span className="cat-pill-sub">{c.sub}</span>
                  {fActive && c.removed > 0 && <span className="cat-pill-filtered">-{c.removed}</span>}
                  {activeSec === i && <div className="cat-active-bar" />}
                </div>
              ))}
            </div>

            {activeCat.error && activeCat.errorKey && (
              <div className="filter-banner err">
                <span className="filter-banner-icon">⚠</span>
                <span>{lang === 'tr' ? 'Bu bölüm yüklenemedi' : 'This section failed to load'}: {activeCat.error}</span>
                <button className="filter-banner-action" onClick={() => retryEndpoint(activeCat.errorKey)}>↻ {lang === 'tr' ? 'Tekrar dene' : 'Retry'}</button>
              </div>
            )}
            {fActive && sectionEmpty && !activeCat.error && (
              <div className="filter-banner">
                <span className="filter-banner-icon">🚫</span>
                <span>{t.bannerEmpty}</span>
                <button className="filter-banner-action" onClick={() => setFilter(FILTER_DEFAULTS)}>↺ {t.fReset}</button>
              </div>
            )}
            {fActive && !sectionEmpty && activeCat.removed > 0 && (
              <div className="filter-banner info">
                <span className="filter-banner-icon">ℹ</span>
                <span>{activeCat.removed} {t.bannerSomeFiltered}</span>
              </div>
            )}

            {/* ACTIVE SECTION */}
            <div className="sec-body">
              {activeCat.loading && (!activeCat.data || activeCat.data.length === 0) ? (
                <div className="sk" style={{ height: 200, borderRadius: 12 }} />
              ) : (
                <SectionContent secIdx={activeSec} data={activeCat.data} currency={cur} lang={lang} date={date} flashId={flashId} arrColorMap={arrColorMap} depColorMap={depColorMap} />
              )}
            </div>
          </>
        )}

        {!results && (
          <div className="empty-state">
            <div className="empty-icon">✈</div>
            <div className="empty-title">{lang === 'tr' ? 'Seyahatini ara' : 'Search your journey'}</div>
            <div style={{ color: 'var(--lt)', fontSize: 14, marginTop: 6 }}>{lang === 'tr' ? 'Formu doldur ve Ara\'ya bas' : 'Fill in the form above and hit Search'}</div>
          </div>
        )}
      </main>

      {showTweaks && (
        <div className="tweaks-panel">
          <div className="tweaks-title">⚙ {t.tweaks}</div>
          <div className="tw-group">
            <div className="tw-label">{t.currency}</div>
            <div className="tw-opts">
              {['EUR', 'USD', 'GBP'].map((c) => (
                <div key={c} className={`tw-opt${cur === c ? ' active' : ''}`} onClick={() => updateTweak('currency', c)}>{c}</div>
              ))}
            </div>
          </div>
          <div className="tw-group">
            <div className="tw-label">Theme</div>
            <div className="tw-opts">
              {[['light', '☀ Light'], ['dark', '🌙 Dark']].map(([v, l]) => (
                <div key={v} className={`tw-opt${tweaks.theme === v ? ' active' : ''}`} onClick={() => updateTweak('theme', v)}>{l}</div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
