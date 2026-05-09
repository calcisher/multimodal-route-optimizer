// ── BusFlightBusCard: bus → flight → bus itinerary picker ───────────────────
// One pair card represents a route via two airport hubs. The selected journey
// stays price-consistent with pair.minTotal because the backend now selects the
// best-value valid trio by default.

const BFB_MIN_TRANSFER_MIN = 120; // 2h, mirrored from backend min_transfer_hours.
const BFB_COLLAPSE_BASE = 5;
const BFB_COLLAPSE_STEP = 5;

function _bfbWait(arrISO, depISO) {
  const a = parseISO(arrISO);
  const d = parseISO(depISO);
  if (!a || !d) return null;
  return Math.round((d.getTime() - a.getTime()) / 60000);
}

function _bfbTotalMin(bus1, flight, bus2) {
  const start = parseISO(bus1?.depISO);
  const end = parseISO(bus2?.arrISO);
  if (!start || !end) return null;
  return Math.round((end.getTime() - start.getTime()) / 60000);
}

function _bfbTotalPrice(bus1, flight, bus2) {
  const prices = [bus1?.price, flight?.price, bus2?.price];
  if (prices.some((p) => p == null)) return null;
  const sum = prices.reduce((acc, p) => acc + Number(p || 0), 0);
  return Number.isFinite(sum) ? sum : null;
}

function _bfbStageLimit(stage, total) {
  if (stage >= 2) return total;
  if (stage === 1) return Math.min(total, BFB_COLLAPSE_BASE + BFB_COLLAPSE_STEP);
  return Math.min(total, BFB_COLLAPSE_BASE);
}

function _bfbVisibleIndexes(indexes, selectedIdx, stage) {
  const limit = _bfbStageLimit(stage, indexes.length);
  const visible = indexes.slice(0, limit);
  if (selectedIdx == null || !indexes.includes(selectedIdx) || visible.includes(selectedIdx)) {
    return visible;
  }
  if (visible.length >= limit && visible.length > 0) {
    visible[visible.length - 1] = selectedIdx;
  } else {
    visible.push(selectedIdx);
  }
  return [...new Set(visible)].sort((a, b) => indexes.indexOf(a) - indexes.indexOf(b));
}

function _bfbToggleLabel(stage, total, t) {
  if (stage >= 2) return t.showFewer;
  const nextLimit = _bfbStageLimit(stage + 1, total);
  return nextLimit >= total ? t.showAllOptions : t.showMoreOptions;
}

function _bfbAirportLabel(city, iata) {
  if (city && iata) return `${city} (${iata})`;
  return city || iata || '—';
}

function _bfbCarrier(kind, opt, lang) {
  if (!opt) return '—';
  if (kind === 'flight') {
    return `${opt.airline || ''}${opt.flightNo ? ' ' + opt.flightNo : ''}`.trim() || 'Flight';
  }
  return opt.company || (lang === 'tr' ? 'Otobüs' : 'Bus');
}

function BFBOption({ kind, opt, selected, disabled, onSelect, currency, lang }) {
  const t = T[lang];
  const fmt = (n) => formatPrice(n, currency);
  const cls = `bfb-opt${selected ? ' selected' : ''}${disabled ? ' disabled' : ''}`;
  const route = opt?.from && opt?.to ? `${opt.from} → ${opt.to}` : '';
  const meta = kind === 'flight'
    ? `${opt.duration || ''} · ${_bfbCarrier(kind, opt, lang)}`
    : `${opt.duration || ''} · ${_bfbCarrier(kind, opt, lang)}`;

  return (
    <button
      className={cls}
      type="button"
      disabled={disabled}
      aria-pressed={selected}
      onClick={onSelect}
      title={disabled ? t.optionDisabledHint : undefined}
    >
      <span className="bfb-opt-main">
        <span className="bfb-opt-time">
          {opt.dep && opt.arr ? `${opt.dep} → ${opt.arr}${opt.nextDay ? ' (+1)' : ''}` : (opt.dep || '—')}
        </span>
        <span className="bfb-opt-price">{opt.price != null ? fmt(opt.price) : '—'}</span>
      </span>
      {route && <span className="bfb-opt-route">{route}</span>}
      <span className="bfb-opt-meta">
        <span>{meta}</span>
        {selected && <span className="bfb-selected-pill">{t.bfbSelected}</span>}
      </span>
    </button>
  );
}

function BFBLegPanel({ icon, title, route, times, meta, price }) {
  return (
    <div className="bfb-leg-panel">
      <div className="bfb-leg-label">{icon} {title}</div>
      <div className="bfb-leg-times">{times || '— → —'}</div>
      <div className="bfb-leg-route">{route || '—'}</div>
      <div className="bfb-leg-meta">{meta || '—'}{price}</div>
    </div>
  );
}

function BFBWaitPanel({ wait, label, lang }) {
  const bad = wait != null && wait < BFB_MIN_TRANSFER_MIN;
  return (
    <div className={`bfb-wait-panel${bad ? ' bad' : ''}`}>
      <div className="bfb-wait-icon">⏱</div>
      <div className="bfb-wait-time">{wait != null ? fmtConnMinutes(wait, lang) : '—'}</div>
      <div className="bfb-wait-copy">{label}</div>
    </div>
  );
}

function BusFlightBusCard({ pair, currency, lang, defaultExpanded = false }) {
  const t = T[lang];
  const fmt = (n) => formatPrice(n, currency);
  const bus1Options = pair?.bus1Options || [];
  const flightOptions = pair?.flightOptions || [];
  const bus2Options = pair?.bus2Options || [];
  const trio = pair?.defaultTrio || {};

  const [bus1Idx, setBus1Idx] = useState(trio.bus1Idx ?? 0);
  const [flightIdx, setFlightIdx] = useState(trio.flightIdx ?? 0);
  const [bus2Idx, setBus2Idx] = useState(trio.bus2Idx ?? 0);
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [showMap, setShowMap] = useState(false);
  const [bus1Stage, setBus1Stage] = useState(0);
  const [flightStage, setFlightStage] = useState(0);
  const [bus2Stage, setBus2Stage] = useState(0);
  const [showInvalidFlights, setShowInvalidFlights] = useState(false);
  const [showInvalidBus2, setShowInvalidBus2] = useState(false);

  useEffect(() => {
    setBus1Idx(trio.bus1Idx ?? 0);
    setFlightIdx(trio.flightIdx ?? 0);
    setBus2Idx(trio.bus2Idx ?? 0);
    setExpanded(defaultExpanded);
    setShowMap(false);
    setBus1Stage(0);
    setFlightStage(0);
    setBus2Stage(0);
    setShowInvalidFlights(false);
    setShowInvalidBus2(false);
  }, [pair, defaultExpanded]);

  const sel1 = bus1Options[bus1Idx] || null;
  const selF = flightOptions[flightIdx] || null;
  const sel2 = bus2Options[bus2Idx] || null;

  function flightValidGivenBus1(b, f) {
    if (!b || !f) return true;
    const w = _bfbWait(b.arrISO, f.depISO);
    return w != null && w >= BFB_MIN_TRANSFER_MIN;
  }

  function bus2ValidGivenFlight(f, b) {
    if (!f || !b) return true;
    const w = _bfbWait(f.arrISO, b.depISO);
    return w != null && w >= BFB_MIN_TRANSFER_MIN;
  }

  function pickBus1(idx) {
    setBus1Idx(idx);
    const newB = bus1Options[idx];
    if (!flightValidGivenBus1(newB, selF)) {
      const fIdx = flightOptions.findIndex((f) => flightValidGivenBus1(newB, f));
      if (fIdx >= 0) {
        setFlightIdx(fIdx);
        const newF = flightOptions[fIdx];
        if (!bus2ValidGivenFlight(newF, sel2)) {
          const b2Idx = bus2Options.findIndex((b) => bus2ValidGivenFlight(newF, b));
          if (b2Idx >= 0) setBus2Idx(b2Idx);
        }
      }
    }
  }

  function pickFlight(idx) {
    setFlightIdx(idx);
    const newF = flightOptions[idx];
    if (!bus2ValidGivenFlight(newF, sel2)) {
      const b2Idx = bus2Options.findIndex((b) => bus2ValidGivenFlight(newF, b));
      if (b2Idx >= 0) setBus2Idx(b2Idx);
    }
  }

  const wait1 = sel1 && selF ? _bfbWait(sel1.arrISO, selF.depISO) : null;
  const wait2 = selF && sel2 ? _bfbWait(selF.arrISO, sel2.depISO) : null;
  const totalMin = _bfbTotalMin(sel1, selF, sel2);
  const totalPrice = _bfbTotalPrice(sel1, selF, sel2);

  const originIata = pair?.originHub?.iata || '';
  const destIata = pair?.destHub?.iata || '';
  const originCity = pair?.originHub?.city || '';
  const destCity = pair?.destHub?.city || '';
  const routeStart = sel1?.from || '';
  const routeEnd = sel2?.to || '';
  const originHubLabel = _bfbAirportLabel(originCity, originIata);
  const destHubLabel = _bfbAirportLabel(destCity, destIata);
  const fullRoute = [routeStart, originHubLabel, destHubLabel, routeEnd].filter(Boolean).join(' → ');
  const headerPrice = totalPrice != null ? totalPrice : pair?.minTotal;
  const flightCarrier = _bfbCarrier('flight', selF, lang);
  const bus1Carrier = _bfbCarrier('bus', sel1, lang);
  const bus2Carrier = _bfbCarrier('bus', sel2, lang);

  const bus1Indexes = bus1Options.map((_, i) => i);
  const validFlightIndexes = flightOptions.map((_, i) => i).filter((i) => flightValidGivenBus1(sel1, flightOptions[i]));
  const invalidFlightIndexes = flightOptions.map((_, i) => i).filter((i) => !validFlightIndexes.includes(i));
  const validBus2Indexes = bus2Options.map((_, i) => i).filter((i) => bus2ValidGivenFlight(selF, bus2Options[i]));
  const invalidBus2Indexes = bus2Options.map((_, i) => i).filter((i) => !validBus2Indexes.includes(i));

  const mapRoute = (sel1 && selF && sel2 && routeStart && routeEnd && originIata && destIata)
    ? { segments: [
      { from: routeStart, to: originIata, type: 'bus', carrier: bus1Carrier, duration: sel1.duration },
      { from: originIata, to: destIata, type: 'flight', carrier: flightCarrier, duration: selF.duration },
      { from: destIata, to: routeEnd, type: 'bus', carrier: bus2Carrier, duration: sel2.duration },
    ] }
    : null;

  function renderColumn({
    title,
    subtitle,
    icon,
    kind,
    options,
    validIndexes,
    invalidIndexes = [],
    selectedIdx,
    stage,
    setStage,
    showInvalid,
    setShowInvalid,
    onPick,
    disabledFor,
  }) {
    const visibleValid = _bfbVisibleIndexes(validIndexes, selectedIdx, stage);
    const hasMoreValid = validIndexes.length > visibleValid.length || stage > 0;
    return (
      <div className="bfb-col">
        <div className="bfb-col-head">
          <div>
            <div className="bfb-col-title">{icon} {title}</div>
            <div className="bfb-col-sub">{subtitle}</div>
          </div>
          <span className="bfb-col-count">{validIndexes.length}</span>
        </div>

        <div className="bfb-option-stack">
          {visibleValid.map((i) => (
            <BFBOption
              key={options[i]?.id || i}
              kind={kind}
              opt={options[i]}
              selected={i === selectedIdx}
              disabled={disabledFor ? disabledFor(options[i], i) : false}
              onSelect={() => onPick(i)}
              currency={currency}
              lang={lang}
            />
          ))}
        </div>

        {hasMoreValid && (
          <button className="bfb-list-toggle" type="button" onClick={() => setStage((s) => (s >= 2 ? 0 : s + 1))}>
            {_bfbToggleLabel(stage, validIndexes.length, t)}
          </button>
        )}

        {invalidIndexes.length > 0 && (
          <>
            <button className="bfb-invalid-toggle" type="button" onClick={() => setShowInvalid((p) => !p)}>
              {showInvalid ? t.bfbHideUnavailable : `${t.bfbShowUnavailable} (${invalidIndexes.length})`}
            </button>
            {showInvalid && (
              <div className="bfb-option-stack invalid">
                {invalidIndexes.map((i) => (
                  <BFBOption
                    key={options[i]?.id || i}
                    kind={kind}
                    opt={options[i]}
                    selected={i === selectedIdx}
                    disabled={true}
                    onSelect={() => onPick(i)}
                    currency={currency}
                    lang={lang}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  return (
    <div className={`bfb-card${expanded ? ' open' : ''}`}>
      <button className="bfb-header" type="button" onClick={() => setExpanded((p) => !p)}>
        <div className="bfb-header-main">
          <div className="bfb-header-route">
            <span className="bfb-iata">{originIata || '—'}</span>
            <span className="bfb-arrow">→</span>
            <span className="bfb-iata">{destIata || '—'}</span>
          </div>
          <div className="bfb-header-path">{fullRoute || `${originCity} → ${destCity}`}</div>
        </div>
        <div className="bfb-header-summary">
          <span className="bfb-header-price">{headerPrice != null ? fmt(headerPrice) : '—'}</span>
          <span className="bfb-header-dur">{totalMin != null ? fmtConnMinutes(totalMin, lang) : '—'}</span>
        </div>
        <span className="bfb-header-toggle" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="bfb-body">
          <div className="bfb-path-strip" aria-label={t.bfbFullPath}>
            <span>{routeStart || '—'}</span>
            <b>{originIata || '—'}</b>
            <b>{destIata || '—'}</b>
            <span>{routeEnd || '—'}</span>
          </div>

          <div className="bfb-selected">
            <BFBLegPanel
              icon="🚌"
              title={t.bfbFirstBus}
              route={sel1 ? `${routeStart} → ${originHubLabel}` : ''}
              times={sel1 ? `${sel1.dep} → ${sel1.arr}${sel1.nextDay ? ' (+1)' : ''}` : ''}
              meta={sel1 ? `${bus1Carrier} · ${sel1.duration || '—'}` : ''}
              price={sel1?.price != null ? ` · ${fmt(sel1.price)}` : ''}
            />
            <BFBWaitPanel wait={wait1} label={`${originIata || ''} ${t.waitWord}`.trim()} lang={lang} />
            <BFBLegPanel
              icon="✈"
              title={t.flightLeg}
              route={selF ? `${originHubLabel} → ${destHubLabel}` : ''}
              times={selF ? `${selF.dep} → ${selF.arr}${selF.nextDay ? ' (+1)' : ''}` : ''}
              meta={selF ? `${flightCarrier} · ${selF.duration || '—'}` : ''}
              price={selF?.price != null ? ` · ${fmt(selF.price)}` : ''}
            />
            <BFBWaitPanel wait={wait2} label={`${destIata || ''} ${t.waitWord}`.trim()} lang={lang} />
            <BFBLegPanel
              icon="🚌"
              title={t.bfbFinalBus}
              route={sel2 ? `${destHubLabel} → ${routeEnd}` : ''}
              times={sel2 ? `${sel2.dep} → ${sel2.arr}${sel2.nextDay ? ' (+1)' : ''}` : ''}
              meta={sel2 ? `${bus2Carrier} · ${sel2.duration || '—'}` : ''}
              price={sel2?.price != null ? ` · ${fmt(sel2.price)}` : ''}
            />
          </div>

          <div className="bfb-picker-grid">
            {renderColumn({
              title: t.bfbFirstBus,
              subtitle: routeStart ? `${routeStart} → ${originCity || originIata}` : originHubLabel,
              icon: '🚌',
              kind: 'bus',
              options: bus1Options,
              validIndexes: bus1Indexes,
              selectedIdx: bus1Idx,
              stage: bus1Stage,
              setStage: setBus1Stage,
              onPick: pickBus1,
            })}
            {renderColumn({
              title: t.flightLeg,
              subtitle: `${originIata || '—'} → ${destIata || '—'}`,
              icon: '✈',
              kind: 'flight',
              options: flightOptions,
              validIndexes: validFlightIndexes,
              invalidIndexes: invalidFlightIndexes,
              selectedIdx: flightIdx,
              stage: flightStage,
              setStage: setFlightStage,
              showInvalid: showInvalidFlights,
              setShowInvalid: setShowInvalidFlights,
              onPick: pickFlight,
              disabledFor: (f) => !flightValidGivenBus1(sel1, f),
            })}
            {renderColumn({
              title: t.bfbFinalBus,
              subtitle: routeEnd ? `${destCity || destIata} → ${routeEnd}` : destHubLabel,
              icon: '🚌',
              kind: 'bus',
              options: bus2Options,
              validIndexes: validBus2Indexes,
              invalidIndexes: invalidBus2Indexes,
              selectedIdx: bus2Idx,
              stage: bus2Stage,
              setStage: setBus2Stage,
              showInvalid: showInvalidBus2,
              setShowInvalid: setShowInvalidBus2,
              onPick: setBus2Idx,
              disabledFor: (b) => !bus2ValidGivenFlight(selF, b),
            })}
          </div>

          <div className="bfb-summary">
            <div className="bfb-summary-stats">
              <div>
                <small>{t.totalTripLabel}</small>
                <span>{totalMin != null ? fmtConnMinutes(totalMin, lang) : '—'}</span>
              </div>
              <div>
                <small>{t.totalPriceLabel}</small>
                <span className="price">{totalPrice != null ? fmt(totalPrice) : '—'}</span>
              </div>
            </div>
            <div className="bfb-actions">
              {mapRoute && (
                <button className="hub-buy-btn map" type="button" onClick={() => setShowMap((p) => !p)}>
                  🗺 {showMap ? t.mapHideBtn : t.mapShowBtn}
                </button>
              )}
              {sel1?.url && (
                <a className="hub-buy-btn bus" href={sel1.url} target="_blank" rel="noopener noreferrer">
                  🚌 {t.bfbBookFirstBus} ↗
                </a>
              )}
              {selF?.link && (
                <a className="hub-buy-btn flight" href={selF.link} target="_blank" rel="noopener noreferrer">
                  ✈ {t.bookFlightBtn} ↗
                </a>
              )}
              {sel2?.url && (
                <a className="hub-buy-btn bus" href={sel2.url} target="_blank" rel="noopener noreferrer">
                  🚌 {t.bfbBookFinalBus} ↗
                </a>
              )}
            </div>
          </div>

          {showMap && mapRoute && (
            <div className="bfb-map">
              <InlineMap route={mapRoute} onClose={() => setShowMap(false)} lang={lang} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
