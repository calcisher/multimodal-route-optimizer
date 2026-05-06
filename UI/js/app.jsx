// ── App: top-level state, search orchestration, and root layout ─────────────
const { useState, useEffect, useRef, useCallback } = React;

function App() {
  const [lang, setLang] = useState('tr');
  const [tweaks, setTweaks] = useState(TWEAK_DEFAULTS);
  const [showTweaks, setShowTweaks] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [fromAp, setFromAp] = useState(null);
  const [toAp, setToAp] = useState(null);
  const [date, setDate] = useState('2026-05-05');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingCards, setLoadingCards] = useState({ flights: false, flightBus: false, busFlight: false, busOrTrain: false });
  const [activeSec, setActiveSec] = useState(0);
  const [filter, setFilter] = useState(FILTER_DEFAULTS);
  const [filterOpen, setFilterOpen] = useState(false);
  const [airports, setAirports] = useState([]);
  const t = T[lang];
  const resultsRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', tweaks.theme || 'light');
  }, [tweaks.theme]);

  // Fetch the IT/DE airport list once for autocomplete suggestions.
  useEffect(() => {
    fetch('/api/airports')
      .then((r) => r.json())
      .then((data) => setAirports(Array.isArray(data?.airports) ? data.airports : []))
      .catch((err) => console.error('/api/airports failed:', err));
  }, []);

  // Restore search from URL on first mount: ?from=Venice&to=Nürnberg&date=...
  // Auto-runs the search so a refresh re-renders results instead of the home state.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fromParam = params.get('from');
    const toParam = params.get('to');
    const dateParam = params.get('date');
    if (!fromParam || !toParam || !dateParam) return;
    const fromIataParam = params.get('fromIata') || null;
    const toIataParam = params.get('toIata') || null;
    setFrom(fromParam);
    setTo(toParam);
    setDate(dateParam);
    setFromAp(fromIataParam);
    setToAp(toIataParam);
    doSearch({ from: fromParam, to: toParam, date: dateParam, fromAp: fromIataParam, toAp: toIataParam });
  }, []);

  function swap() {setFrom(to);setTo(from);setFromAp(toAp);setToAp(fromAp);}

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

  async function doSearch(override) {
    const fromVal = override?.from ?? from;
    const toVal = override?.to ?? to;
    const dateVal = override?.date ?? date;
    const fromApVal = override?.fromAp !== undefined ? override.fromAp : fromAp;
    const toApVal = override?.toAp !== undefined ? override.toAp : toAp;
    if (!fromVal || !toVal) return;
    const body = { from_city: fromVal, to_city: toVal, date: dateVal, from_iata: fromApVal, to_iata: toApVal };

    const urlParams = new URLSearchParams();
    urlParams.set('from', fromVal);
    urlParams.set('to', toVal);
    urlParams.set('date', dateVal);
    if (fromApVal) urlParams.set('fromIata', fromApVal);
    if (toApVal) urlParams.set('toIata', toApVal);
    const newUrl = `${window.location.pathname}?${urlParams.toString()}`;
    if (newUrl !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', newUrl);
    }

    setLoading(true);
    setLoadingCards({ flights: true, flightBus: true, busFlight: true, busOrTrain: true });
    setFilter(FILTER_DEFAULTS);
    setFilterOpen(false);
    setResults({
      bestFlights: [],
      cheapFlights: [],
      flightPlusBus: [],
      busPlusFlight: [],
      busOrTrain: [],
    });
    setActiveSec(0);

    // Smooth-scroll the results area into view so the loading state is the
    // first thing the user sees instead of staring at the dominant search hero.
    // Use requestAnimationFrame so the layout has settled after setResults({}).
    requestAnimationFrame(() => {
      const el = resultsRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + window.scrollY - 24;
      window.scrollTo({ top, behavior: 'smooth' });
    });

    const flightsTask = fetchPart('/api/flights', body)
      .then((data) => {
        mergeAirports(data);
        setResults((prev) => ({
          ...(prev || {}),
          bestFlights: data.bestFlights ?? [],
          cheapFlights: data.cheapFlights ?? [],
          resolved: data.resolved ?? null,
        }));
      })
      .catch((err) => console.error('/api/flights failed:', err))
      .finally(() => setLoadingCards((prev) => ({ ...prev, flights: false })));

    const flightBusTask = fetchPart('/api/flight-plus-bus', body)
      .then((data) => {
        mergeAirports(data);
        setResults((prev) => ({
          ...(prev || {}),
          flightPlusBus: data.flightPlusBus ?? [],
        }));
      })
      .catch((err) => console.error('/api/flight-plus-bus failed:', err))
      .finally(() => setLoadingCards((prev) => ({ ...prev, flightBus: false })));

    const busFlightTask = fetchPart('/api/bus-plus-flight', body)
      .then((data) => {
        mergeAirports(data);
        setResults((prev) => ({
          ...(prev || {}),
          busPlusFlight: data.busPlusFlight ?? [],
        }));
      })
      .catch((err) => console.error('/api/bus-plus-flight failed:', err))
      .finally(() => setLoadingCards((prev) => ({ ...prev, busFlight: false })));

    const groundTask = fetchPart('/api/trains', body)
      .then((data) => {
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
      })
      .catch((err) => console.error('/api/trains failed:', err))
      .finally(() => setLoadingCards((prev) => ({ ...prev, busOrTrain: false })));

    await Promise.all([flightsTask, flightBusTask, busFlightTask, groundTask]);
    setLoading(false);
  }

  function updateTweak(key, val) {
    setTweaks((prev) => ({ ...prev, [key]: val }));
  }

  const cur = tweaks.currency || 'EUR';
  const fmt = (n) => cur === "USD" ? `$${n}` : `€${n}`;

  const minPriceOf = (arr, key) => {
    if (!arr || !arr.length) return null;
    const prices = arr.map((d) => d[key]).filter((p) => typeof p === 'number' && isFinite(p));
    return prices.length ? Math.min(...prices) : null;
  };

  const { filtered: filteredResults, removed } = applyFilters(results, filter);
  const fActive = isFilterActive(filter);
  const fCount = activeFilterCount(filter);

  const CATS = [
  { icon: '✈️', label: t.bestFlight, sub: t.bestFlightSub, color: '#2563EB', data: filteredResults?.bestFlights, minPrice: minPriceOf(filteredResults?.bestFlights, 'price'), loading: loadingCards.flights, removed: removed[0] },
  { icon: '💶', label: t.cheapFlight, sub: t.cheapFlightSub, color: '#16A34A', data: filteredResults?.cheapFlights, minPrice: minPriceOf(filteredResults?.cheapFlights, 'price'), loading: loadingCards.flights, removed: removed[1] },
  { icon: '✈🚌', label: t.flightBus, sub: t.flightBusSub, color: '#7C3AED', data: filteredResults?.flightPlusBus, minPrice: minPriceOf(filteredResults?.flightPlusBus, 'minTotal'), loading: loadingCards.flightBus, removed: removed[2] },
  { icon: '🚌✈', label: t.busFlight, sub: t.busFlightSub, color: '#0891B2', data: filteredResults?.busPlusFlight, minPrice: minPriceOf(filteredResults?.busPlusFlight, 'minTotal'), loading: loadingCards.busFlight, removed: removed[3] },
  { icon: '🚌🚆', label: t.busOnly, sub: t.busOnlySub, color: '#475569', data: filteredResults?.busOrTrain, minPrice: minPriceOf(filteredResults?.busOrTrain, 'price'), loading: loadingCards.busOrTrain, removed: removed[4] }];

  const activeCat = CATS[activeSec];
  const sectionEmpty = activeCat && !activeCat.loading && (!activeCat.data || activeCat.data.length === 0);


  return (
    <>
      <header className="hdr" style={{ fontFamily: "\"Space Grotesk\"" }}>
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
            <CityInput label={t.from} value={from} onChange={setFrom} onAirportSelect={setFromAp} selectedAirport={fromAp} lang={lang} airports={airports} />
            <button className="swap-btn" onClick={swap}>⇄</button>
            <CityInput label={t.to} value={to} onChange={setTo} onAirportSelect={setToAp} selectedAirport={toAp} lang={lang} airports={airports} />
            <div className="sf-field sf-date">
              <div className="sf-label">{t.date}</div>
              <div className="sf-input-wrap">
                <input type="date" className="sf-input" value={date} onChange={(e) => setDate(e.target.value)} />
              </div>
            </div>
            <button className="search-btn" onClick={doSearch} disabled={loading}>
              {loading ? t.searching : `🔍 ${t.search}`}
            </button>
          </div>
          {!results && !loading &&
          <div style={{ marginTop: 14, color: 'rgba(255,255,255,.6)', fontSize: 13, cursor: 'pointer', userSelect: 'none' }}
          onClick={() => { setFrom('Venice'); setTo('Nürnberg'); doSearch({ from: 'Venice', to: 'Nürnberg' }); }}>
              💡 {t.trySearch}
            </div>
          }
        </div>
      </section>

      <main className="results" ref={resultsRef}>
        {results &&
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
              <ActiveFilterChips filter={filter} setFilter={setFilter} lang={lang} />
            </div>
            {filterOpen && <FilterPanel filter={filter} setFilter={setFilter} lang={lang} />}

            {/* CATEGORY BAR */}
            <div className="cat-bar">
              {CATS.map((c, i) =>
            <div key={i} className={`cat-pill${activeSec === i ? ' active' : ''}`} onClick={() => setActiveSec(i)}>
                  <span className="cat-pill-icon">{c.icon}</span>
                  <span className="cat-pill-label">{c.label}</span>
                  <span className="cat-pill-price">
                    {c.loading ? <span className="cat-pill-spinner" /> : (c.minPrice != null ? fmt(c.minPrice) : '—')}
                  </span>
                  <span className="cat-pill-sub">{c.sub}</span>
                  {fActive && c.removed > 0 && <span className="cat-pill-filtered">-{c.removed}</span>}
                  {activeSec === i && <div className="cat-active-bar" />}
                </div>
            )}
            </div>
            {fActive && sectionEmpty && (
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
              {activeCat.loading && (!activeCat.data || activeCat.data.length === 0) ?
                <div className="sk" style={{ height: 200, borderRadius: 12 }} /> :
                <SectionContent secIdx={activeSec} data={activeCat.data} currency={cur} lang={lang} date={date} />
              }
            </div>
          </>
        }

        {!results &&
        <div className="empty-state">
            <div className="empty-icon">✈</div>
            <div className="empty-title">{lang === 'tr' ? 'Seyahatini ara' : 'Search your journey'}</div>
            <div style={{ color: 'var(--lt)', fontSize: 14, marginTop: 6 }}>{lang === 'tr' ? 'Formu doldur ve Ara\'ya bas' : 'Fill in the form above and hit Search'}</div>
          </div>
        }
      </main>

      {showTweaks &&
      <div className="tweaks-panel">
          <div className="tweaks-title">⚙ {t.tweaks}</div>
          <div className="tw-group">
            <div className="tw-label">{t.currency}</div>
            <div className="tw-opts">
              {['EUR', 'USD', 'GBP'].map((c) =>
            <div key={c} className={`tw-opt${cur === c ? ' active' : ''}`} onClick={() => updateTweak('currency', c)}>{c}</div>
            )}
            </div>
          </div>
          <div className="tw-group">
            <div className="tw-label">Tema</div>
            <div className="tw-opts">
              {[['light', '☀ Light'], ['dark', '🌙 Dark']].map(([v, l]) =>
            <div key={v} className={`tw-opt${tweaks.theme === v ? ' active' : ''}`} onClick={() => updateTweak('theme', v)}>{l}</div>
            )}
            </div>
          </div>
        </div>
      }
    </>);

}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
