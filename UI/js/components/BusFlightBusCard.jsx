// ── BusFlightBusCard: bus → flight → bus itinerary picker ───────────────────
// One pair card represents a route via two airport hubs. The selected journey
// stays price-consistent with pair.minTotal because the backend now selects the
// best-value valid trio by default.

const BFB_MIN_TRANSFER_MIN = 120; // 2h, mirrored from backend min_transfer_hours.
const BFB_COLLAPSE_BASE = 3;
const BFB_COLLAPSE_STEP = 3;

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

function BFBOption({ kind, opt, selected, disabled, onSelect, currency, lang, badge }) {
  const t = T[lang];
  const fmt = (n) => formatPrice(n, currency);
  const cls = `bfb-opt${selected ? ' selected' : ''}${disabled ? ' disabled' : ''}${badge ? ' overnight' : ''}`;
  const route = opt?.from && opt?.to ? `${opt.from} → ${opt.to}` : '';
  const meta = kind === 'flight'
    ? `${opt.duration || ''} · ${_bfbCarrier(kind, opt, lang)}`
    : `${opt.duration || ''} · ${_bfbCarrier(kind, opt, lang)}`;
  // Day offset prefix shown on the departure: "(-1) 22:00" for prev-day,
  // "(+1) 06:30" for next-day, so it's immediately clear the leg straddles
  // the trip date. Existing nextDay arrival annotation stays on arrival.
  const prefix = opt?.overnight === 'prev' ? '(-1) ' : (opt?.overnight === 'next' ? '(+1) ' : '');

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
          {opt.dep && opt.arr ? `${prefix}${opt.dep} → ${opt.arr}${opt.nextDay ? ' (+1)' : ''}` : (opt.dep || '—')}
        </span>
        <span className="bfb-opt-price">{opt.price != null ? fmt(opt.price) : '—'}</span>
      </span>
      {route && <span className="bfb-opt-route">{route}</span>}
      <span className="bfb-opt-meta">
        <span>{meta}</span>
        <span className="bfb-opt-tags">
          {badge && <span className="bfb-overnight-badge">🌙 {badge}</span>}
          {selected && <span className="bfb-selected-pill">{t.bfbSelected}</span>}
        </span>
      </span>
    </button>
  );
}

function BFBTimelineLeg({ step, icon, title, route, times, meta, price }) {
  return (
    <div className="bfb-timeline-leg">
      <div className="bfb-step-badge">{step}</div>
      <div className="bfb-leg-content">
        <div className="bfb-leg-label">{icon} {title}</div>
        <div className="bfb-leg-route">{route || '—'}</div>
        <div className="bfb-leg-times">{times || '— → —'}</div>
        <div className="bfb-leg-meta">{meta || '—'}{price}</div>
      </div>
    </div>
  );
}

function BFBTimelineTransfer({ wait, iata, city, lang }) {
  const bad = wait != null && wait < BFB_MIN_TRANSFER_MIN;
  return (
    <div className={`bfb-transfer-node${bad ? ' bad' : ''}`}>
      <div className="bfb-transfer-line" />
      <div className="bfb-transfer-chip">
        <span>⏱ {wait != null ? fmtConnMinutes(wait, lang) : '—'}</span>
        <small>{iata || city || '—'} {T[lang].waitWord}</small>
      </div>
      <div className="bfb-transfer-line" />
    </div>
  );
}

function BusFlightBusCard({ pair, currency, lang, defaultExpanded = false }) {
  const t = T[lang];
  const fmt = (n) => formatPrice(n, currency);
  const bus1Options = pair?.bus1Options || [];
  const flightOptions = pair?.flightOptions || [];
  const bus2Options = pair?.bus2Options || [];
  const bus1PrevOptions = pair?.bus1PrevOptions || [];
  const bus2NextOptions = pair?.bus2NextOptions || [];
  const trio = pair?.defaultTrio || {};

  // Source-aware seeding: when defaultTrio says bus1Source='prev', the
  // initial selection lives in bus1PrevIdx, not bus1Idx. Same for bus2/next.
  const _seedBus1Idx = trio.bus1Source === 'prev' ? 0 : (trio.bus1Idx ?? 0);
  const _seedBus1PrevIdx = trio.bus1Source === 'prev' ? (trio.bus1Idx ?? 0) : null;
  const _seedBus2Idx = trio.bus2Source === 'next' ? 0 : (trio.bus2Idx ?? 0);
  const _seedBus2NextIdx = trio.bus2Source === 'next' ? (trio.bus2Idx ?? 0) : null;

  const [bus1Idx, setBus1Idx] = useState(_seedBus1Idx);
  const [bus1PrevIdx, setBus1PrevIdx] = useState(_seedBus1PrevIdx);
  const [flightIdx, setFlightIdx] = useState(trio.flightIdx ?? 0);
  const [bus2Idx, setBus2Idx] = useState(_seedBus2Idx);
  const [bus2NextIdx, setBus2NextIdx] = useState(_seedBus2NextIdx);
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [showMap, setShowMap] = useState(false);
  const [bus1Stage, setBus1Stage] = useState(0);
  const [flightStage, setFlightStage] = useState(0);
  const [bus2Stage, setBus2Stage] = useState(0);
  const [bus1PrevStage, setBus1PrevStage] = useState(0);
  const [bus2NextStage, setBus2NextStage] = useState(0);
  const [showInvalidFlights, setShowInvalidFlights] = useState(false);
  const [showInvalidBus2, setShowInvalidBus2] = useState(false);

  // Stable identity key — only resets state when the user runs a new search
  // (different origin/dest hub or flight default). Without this, every
  // parent re-render hands us a fresh `pair` reference and the effect would
  // wipe whatever the user just clicked. That was the next-day-closes bug.
  const pairKey = `${pair?.originHub?.iata || ''}|${pair?.destHub?.iata || ''}|${trio.flightIdx}|${trio.bus1Idx}|${trio.bus2Idx}|${trio.bus1Source || ''}|${trio.bus2Source || ''}`;

  useEffect(() => {
    setBus1Idx(_seedBus1Idx);
    setBus1PrevIdx(_seedBus1PrevIdx);
    setFlightIdx(trio.flightIdx ?? 0);
    setBus2Idx(_seedBus2Idx);
    setBus2NextIdx(_seedBus2NextIdx);
    setExpanded(defaultExpanded);
    setShowMap(false);
    setBus1Stage(0);
    setFlightStage(0);
    setBus2Stage(0);
    setBus1PrevStage(0);
    setBus2NextStage(0);
    setShowInvalidFlights(false);
    setShowInvalidBus2(false);
  }, [pairKey, defaultExpanded]);

  const sel1 = bus1PrevIdx != null
    ? (bus1PrevOptions[bus1PrevIdx] || null)
    : (bus1Options[bus1Idx] || null);
  const selF = flightOptions[flightIdx] || null;
  const sel2 = bus2NextIdx != null
    ? (bus2NextOptions[bus2NextIdx] || null)
    : (bus2Options[bus2Idx] || null);

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
    setBus1PrevIdx(null);
    const newB = bus1Options[idx];
    if (!flightValidGivenBus1(newB, selF)) {
      const fIdx = flightOptions.findIndex((f) => flightValidGivenBus1(newB, f));
      if (fIdx >= 0) {
        setFlightIdx(fIdx);
        const newF = flightOptions[fIdx];
        if (!bus2ValidGivenFlight(newF, sel2)) {
          const b2Idx = bus2Options.findIndex((b) => bus2ValidGivenFlight(newF, b));
          if (b2Idx >= 0) {
            setBus2Idx(b2Idx);
            setBus2NextIdx(null);
          }
        }
      }
    }
  }

  function pickBus2(idx) {
    setBus2Idx(idx);
    setBus2NextIdx(null);
  }

  function pickFlight(idx) {
    setFlightIdx(idx);
    const newF = flightOptions[idx];
    if (!bus2ValidGivenFlight(newF, sel2)) {
      const b2Idx = bus2Options.findIndex((b) => bus2ValidGivenFlight(newF, b));
      if (b2Idx >= 0) {
        setBus2Idx(b2Idx);
        setBus2NextIdx(null);
      } else {
        const b2nIdx = bus2NextOptions.findIndex((b) => bus2ValidGivenFlight(newF, b));
        if (b2nIdx >= 0) setBus2NextIdx(b2nIdx);
      }
    }
  }

  function pickBus1Prev(idx) {
    setBus1PrevIdx(idx);
    const newB = bus1PrevOptions[idx];
    if (!newB) return;
    // Auto-jump to the cheapest flight this overnight bus enables — that's
    // the whole point of selecting it; otherwise the click feels inert.
    const sortedFlights = flightOptions
      .map((f, i) => ({ f, i }))
      .filter(({ f }) => f.price != null && flightValidGivenBus1(newB, f))
      .sort((a, b) => (a.f.price || 0) - (b.f.price || 0));
    if (!sortedFlights.length) return;
    const { f: target, i: ti } = sortedFlights[0];
    setFlightIdx(ti);
    if (!bus2ValidGivenFlight(target, sel2)) {
      const b2Idx = bus2Options.findIndex((b) => bus2ValidGivenFlight(target, b));
      if (b2Idx >= 0) {
        setBus2Idx(b2Idx);
        setBus2NextIdx(null);
      } else {
        const b2nIdx = bus2NextOptions.findIndex((b) => bus2ValidGivenFlight(target, b));
        if (b2nIdx >= 0) setBus2NextIdx(b2nIdx);
      }
    }
  }

  function pickBus2Next(idx) {
    setBus2NextIdx(idx);
    const newB = bus2NextOptions[idx];
    if (!newB) return;
    const sortedFlights = flightOptions
      .map((f, i) => ({ f, i }))
      .filter(({ f }) => f.price != null && bus2ValidGivenFlight(f, newB))
      .sort((a, b) => (a.f.price || 0) - (b.f.price || 0));
    if (!sortedFlights.length) return;
    const { f: target, i: ti } = sortedFlights[0];
    setFlightIdx(ti);
    if (!flightValidGivenBus1(sel1, target)) {
      const b1Idx = bus1Options.findIndex((b) => flightValidGivenBus1(b, target));
      if (b1Idx >= 0) {
        setBus1Idx(b1Idx);
        setBus1PrevIdx(null);
      } else {
        const b1pIdx = bus1PrevOptions.findIndex((b) => flightValidGivenBus1(b, target));
        if (b1pIdx >= 0) setBus1PrevIdx(b1pIdx);
      }
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
  const mainRoute = [routeStart, routeEnd].filter(Boolean).join(' → ');
  const viaRoute = [originHubLabel, destHubLabel].filter(Boolean).join(' + ');
  const headerPrice = totalPrice != null ? totalPrice : pair?.minTotal;
  const flightCarrier = _bfbCarrier('flight', selF, lang);
  const bus1Carrier = _bfbCarrier('bus', sel1, lang);
  const bus2Carrier = _bfbCarrier('bus', sel2, lang);

  const bus1Indexes = bus1Options.map((_, i) => i);
  const validFlightIndexes = flightOptions.map((_, i) => i).filter((i) => flightValidGivenBus1(sel1, flightOptions[i]));
  const invalidFlightIndexes = flightOptions.map((_, i) => i).filter((i) => !validFlightIndexes.includes(i));
  const validBus2Indexes = bus2Options.map((_, i) => i).filter((i) => bus2ValidGivenFlight(selF, bus2Options[i]));
  const invalidBus2Indexes = bus2Options.map((_, i) => i).filter((i) => !validBus2Indexes.includes(i));

  // Overnight surfacing: only show prev-day bus1 / next-day bus2 when there's
  // at least one flight cheaper than the currently selected one that the
  // user can't reach with same-day buses (the "we skipped €18 for €55" case).
  const cheaperDisabledFlights = flightOptions
    .map((f, i) => ({ f, i }))
    .filter(({ f }) => {
      if (f.price == null || selF?.price == null) return false;
      if (f.price >= selF.price) return false;
      return !flightValidGivenBus1(sel1, f) || !bus2ValidGivenFlight(f, sel2);
    });
  const hasCheapDisabled = cheaperDisabledFlights.length > 0;
  const allBus1Combined = bus1Options.concat(bus1PrevOptions);
  const allBus2Combined = bus2Options.concat(bus2NextOptions);
  // Once the user has actually picked an overnight option we must keep the
  // whole list visible so they can switch between them. The hasCheapDisabled
  // heuristic governs *discovery* only; gating live interaction on it makes
  // the overnight section vanish on the very click that selected it (because
  // pickBus1Prev / pickBus2Next auto-jumps flightIdx to the cheaper flight,
  // emptying cheaperDisabledFlights on the next render).
  const _allBus1PrevSorted = bus1PrevOptions
    .map((_, i) => i)
    .sort((a, b) => {
      const aT = parseISO(bus1PrevOptions[a]?.arrISO)?.getTime() ?? 0;
      const bT = parseISO(bus1PrevOptions[b]?.arrISO)?.getTime() ?? 0;
      return bT - aT;
    });
  const _allBus2NextSorted = bus2NextOptions
    .map((_, i) => i)
    .sort((a, b) => {
      const aT = parseISO(bus2NextOptions[a]?.depISO)?.getTime() ?? Infinity;
      const bT = parseISO(bus2NextOptions[b]?.depISO)?.getTime() ?? Infinity;
      return aT - bT;
    });
  const usefulBus1PrevIndexes = bus1PrevIdx != null
    ? _allBus1PrevSorted
    : (hasCheapDisabled
      ? bus1PrevOptions
          .map((b, i) => ({ b, i }))
          .filter(({ b }) => cheaperDisabledFlights.some(({ f }) =>
            flightValidGivenBus1(b, f) &&
            allBus2Combined.some((b2) => bus2ValidGivenFlight(f, b2))
          ))
          .sort((a, b) => {
            const aT = parseISO(a.b.arrISO)?.getTime() ?? 0;
            const bT = parseISO(b.b.arrISO)?.getTime() ?? 0;
            return bT - aT;
          })
          .map(({ i }) => i)
      : []);
  const usefulBus2NextIndexes = bus2NextIdx != null
    ? _allBus2NextSorted
    : (hasCheapDisabled
      ? bus2NextOptions
          .map((b, i) => ({ b, i }))
          .filter(({ b }) => cheaperDisabledFlights.some(({ f }) =>
            bus2ValidGivenFlight(f, b) &&
            allBus1Combined.some((b1) => flightValidGivenBus1(b1, f))
          ))
          .sort((a, b) => {
            const aT = parseISO(a.b.depISO)?.getTime() ?? Infinity;
            const bT = parseISO(b.b.depISO)?.getTime() ?? Infinity;
            return aT - bT;
          })
          .map(({ i }) => i)
      : []);
  const overnightActive = bus1PrevIdx != null || bus2NextIdx != null;
  const overnightHubLabel = bus1PrevIdx != null
    ? originCity
    : (bus2NextIdx != null ? destCity : '');

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
    overnightOptions = [],
    overnightIndexes = [],
    overnightSelectedIdx = null,
    overnightStage = 0,
    setOvernightStage,
    overnightTitle,
    overnightHint,
    overnightBadge,
    onPickOvernight,
  }) {
    const visibleValid = _bfbVisibleIndexes(validIndexes, selectedIdx, stage);
    const hasMoreValid = validIndexes.length > visibleValid.length || stage > 0;
    const visibleOvernight = _bfbVisibleIndexes(overnightIndexes, overnightSelectedIdx, overnightStage);
    const hasMoreOvernight = overnightIndexes.length > visibleOvernight.length || overnightStage > 0;
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

        {overnightIndexes.length > 0 && (
          <div className="bfb-overnight-section">
            <div className="bfb-overnight-head">
              <span className="bfb-overnight-title">🌙 {overnightTitle}</span>
              <span className="bfb-overnight-hint">{overnightHint}</span>
            </div>
            <div className="bfb-option-stack overnight">
              {visibleOvernight.map((i) => (
                <BFBOption
                  key={overnightOptions[i]?.id || `ov${i}`}
                  kind={kind}
                  opt={overnightOptions[i]}
                  selected={i === overnightSelectedIdx}
                  disabled={false}
                  onSelect={() => onPickOvernight(i)}
                  currency={currency}
                  lang={lang}
                  badge={overnightBadge}
                />
              ))}
            </div>
            {hasMoreOvernight && setOvernightStage && (
              <button
                className="bfb-list-toggle overnight"
                type="button"
                onClick={() => setOvernightStage((s) => (s >= 2 ? 0 : s + 1))}
              >
                {_bfbToggleLabel(overnightStage, overnightIndexes.length, t)}
              </button>
            )}
          </div>
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
          <div className="bfb-route-title">
            <span>{mainRoute || `${originCity} → ${destCity}`}</span>
          </div>
          <div className="bfb-route-subtitle">
            <span className="bfb-mode-chip">🚌✈🚌 {t.bfbLegCount}</span>
            <span>{t.bfbViaLabel} {viaRoute || `${originIata} + ${destIata}`}</span>
            {overnightActive && (
              <span className="bfb-overnight-chip">🌙 {t.bfbOvernightChip}{overnightHubLabel ? `: ${overnightHubLabel}` : ''}</span>
            )}
          </div>
        </div>
        <div className="bfb-header-summary">
          <span className="bfb-header-price">{headerPrice != null ? fmt(headerPrice) : '—'}</span>
          <span className="bfb-header-dur">{totalMin != null ? fmtConnMinutes(totalMin, lang) : '—'}</span>
        </div>
        <span className="bfb-header-toggle" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
      </button>

      {expanded && (
        <div className="bfb-body">
          <div className="bfb-itinerary" aria-label={t.bfbSelectedJourney}>
            <div className="bfb-itinerary-head">
              <div>
                <div className="bfb-section-kicker">{t.bfbSelectedJourney}</div>
                <div className="bfb-itinerary-title">{mainRoute || '—'}</div>
                <div className="bfb-itinerary-via">{t.bfbViaLabel} {viaRoute || '—'}</div>
              </div>
              <div className="bfb-itinerary-stats">
                <div>
                  <small>{t.totalPriceLabel}</small>
                  <strong>{totalPrice != null ? fmt(totalPrice) : '—'}</strong>
                </div>
                <div>
                  <small>{t.totalTripLabel}</small>
                  <strong>{totalMin != null ? fmtConnMinutes(totalMin, lang) : '—'}</strong>
                </div>
              </div>
            </div>

            <div className="bfb-timeline">
              <BFBTimelineLeg
                step="1"
                icon="🚌"
                title={t.bfbFirstBus}
                route={sel1 ? `${routeStart} → ${originHubLabel}` : ''}
                times={sel1 ? `${sel1.dep} → ${sel1.arr}${sel1.nextDay ? ' (+1)' : ''}` : ''}
                meta={sel1 ? `${bus1Carrier} · ${sel1.duration || '—'}` : ''}
                price={sel1?.price != null ? ` · ${fmt(sel1.price)}` : ''}
              />
              <BFBTimelineTransfer wait={wait1} iata={originIata} city={originCity} lang={lang} />
              <BFBTimelineLeg
                step="2"
                icon="✈"
                title={t.flightLeg}
                route={selF ? `${originHubLabel} → ${destHubLabel}` : ''}
                times={selF ? `${selF.dep} → ${selF.arr}${selF.nextDay ? ' (+1)' : ''}` : ''}
                meta={selF ? `${flightCarrier} · ${selF.duration || '—'}` : ''}
                price={selF?.price != null ? ` · ${fmt(selF.price)}` : ''}
              />
              <BFBTimelineTransfer wait={wait2} iata={destIata} city={destCity} lang={lang} />
              <BFBTimelineLeg
                step="3"
                icon="🚌"
                title={t.bfbFinalBus}
                route={sel2 ? `${destHubLabel} → ${routeEnd}` : ''}
                times={sel2 ? `${sel2.dep} → ${sel2.arr}${sel2.nextDay ? ' (+1)' : ''}` : ''}
                meta={sel2 ? `${bus2Carrier} · ${sel2.duration || '—'}` : ''}
                price={sel2?.price != null ? ` · ${fmt(sel2.price)}` : ''}
              />
            </div>
          </div>

          <div className="bfb-alternatives-head">
            <div>
              <div className="bfb-section-kicker">{t.bfbChangeLegs}</div>
              <div className="bfb-alternatives-title">{t.bfbChangeLegsHint}</div>
            </div>
          </div>

          <div className="bfb-picker-grid">
            {renderColumn({
              title: t.bfbFirstBus,
              subtitle: routeStart ? `${routeStart} → ${originCity || originIata}` : originHubLabel,
              icon: '🚌',
              kind: 'bus',
              options: bus1Options,
              validIndexes: bus1Indexes,
              selectedIdx: bus1PrevIdx == null ? bus1Idx : null,
              stage: bus1Stage,
              setStage: setBus1Stage,
              onPick: pickBus1,
              overnightOptions: bus1PrevOptions,
              overnightIndexes: usefulBus1PrevIndexes,
              overnightSelectedIdx: bus1PrevIdx,
              overnightStage: bus1PrevStage,
              setOvernightStage: setBus1PrevStage,
              overnightTitle: t.bfbPrevDayBusTitle,
              overnightHint: t.bfbPrevDayBusHint,
              overnightBadge: t.bfbPrevDayBadge,
              onPickOvernight: pickBus1Prev,
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
              selectedIdx: bus2NextIdx == null ? bus2Idx : null,
              stage: bus2Stage,
              setStage: setBus2Stage,
              showInvalid: showInvalidBus2,
              setShowInvalid: setShowInvalidBus2,
              onPick: pickBus2,
              disabledFor: (b) => !bus2ValidGivenFlight(selF, b),
              overnightOptions: bus2NextOptions,
              overnightIndexes: usefulBus2NextIndexes,
              overnightSelectedIdx: bus2NextIdx,
              overnightStage: bus2NextStage,
              setOvernightStage: setBus2NextStage,
              overnightTitle: t.bfbNextDayBusTitle,
              overnightHint: t.bfbNextDayBusHint,
              overnightBadge: t.bfbNextDayBadge,
              onPickOvernight: pickBus2Next,
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
