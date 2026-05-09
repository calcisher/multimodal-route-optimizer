// ── BusFlightBusCard: pair card for the bus → flight → bus 5th section ───────
// Three-column interactive picker with arrows. Each pair card shows one
// (origin_hub, dest_hub) routing; user picks bus1 / flight / bus2 from each
// column, the 2h transfer rule disables invalid options across columns,
// and the default selection is the cheapest-flight + tightest-buses trio
// computed server-side (pair.defaultTrio).

const BFB_MIN_TRANSFER_MIN = 120; // 2h, mirrored from backend min_transfer_hours.

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
  const sum = (bus1?.price || 0) + (flight?.price || 0) + (bus2?.price || 0);
  return sum > 0 ? sum : null;
}

function BFBOption({ kind, opt, selected, disabled, onSelect, currency, lang }) {
  const t = T[lang];
  const fmt = (n) => formatPrice(n, currency);
  const cls = `bfb-opt${selected ? ' selected' : ''}${disabled ? ' disabled' : ''}`;
  const meta = kind === 'flight'
    ? `${opt.duration || ''} · ${opt.airline || ''}${opt.flightNo ? ' ' + opt.flightNo : ''}`
    : `${opt.duration || ''} · FlixBus`;
  return (
    <div className={cls} onClick={disabled ? undefined : onSelect}
         title={disabled ? t.optionDisabledHint : undefined}>
      <div className="bfb-opt-row1">
        <span className="bfb-opt-time">
          {opt.dep && opt.arr ? `${opt.dep} → ${opt.arr}` : (opt.dep || '—')}
        </span>
        <span className="bfb-opt-price">{opt.price != null ? fmt(opt.price) : '—'}</span>
      </div>
      <div className="bfb-opt-row2">{meta}</div>
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

  // Snap selections back to the server-computed default whenever a fresh
  // pair lands (new search). Without this the user's stale picks would
  // bleed into the next route.
  useEffect(() => {
    setBus1Idx(trio.bus1Idx ?? 0);
    setFlightIdx(trio.flightIdx ?? 0);
    setBus2Idx(trio.bus2Idx ?? 0);
    setExpanded(defaultExpanded);
  }, [pair]);

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

  // Cascading snap: when the user's pick invalidates a downstream column,
  // jump to the first still-valid option there. Keeps the visible trio
  // consistent without forcing the user to re-pick everything.
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
  function pickBus2(idx) {
    setBus2Idx(idx);
  }

  const wait1 = sel1 && selF ? _bfbWait(sel1.arrISO, selF.depISO) : null;
  const wait2 = selF && sel2 ? _bfbWait(selF.arrISO, sel2.depISO) : null;
  const totalMin = _bfbTotalMin(sel1, selF, sel2);
  const totalPrice = _bfbTotalPrice(sel1, selF, sel2);

  const originIata = pair?.originHub?.iata || '';
  const destIata = pair?.destHub?.iata || '';
  const originCity = pair?.originHub?.city || '';
  const destCity = pair?.destHub?.city || '';

  return (
    <div className={`bfb-card${expanded ? ' open' : ''}`}>
      <div className="bfb-header" onClick={() => setExpanded((p) => !p)}>
        <div className="bfb-header-route">
          <span className="bfb-iata">{originIata}</span>
          <span className="bfb-arrow">→</span>
          <span className="bfb-iata">{destIata}</span>
        </div>
        <div className="bfb-header-cities">{originCity} → {destCity}</div>
        <div className="bfb-header-summary">
          <span className="bfb-header-price">{pair?.minTotal != null ? fmt(pair.minTotal) : '—'}</span>
          {totalMin != null && (
            <span className="bfb-header-dur">{fmtConnMinutes(totalMin, lang)}</span>
          )}
        </div>
        <button className="bfb-header-toggle" type="button" aria-label="toggle">
          {expanded ? '▾' : '▸'}
        </button>
      </div>

      {expanded && (
        <div className="bfb-body">
          <div className="bfb-cols">
            <div className="bfb-col">
              <div className="bfb-col-title">🚌 {t.busLeg} 1</div>
              <div className="bfb-col-sub">{originCity}</div>
              {bus1Options.map((b, i) => (
                <BFBOption
                  key={b.id || i}
                  kind="bus"
                  opt={b}
                  selected={i === bus1Idx}
                  disabled={false}
                  onSelect={() => pickBus1(i)}
                  currency={currency}
                  lang={lang}
                />
              ))}
            </div>

            <div className="bfb-gutter">
              <div className="bfb-arrow-h">→</div>
              {wait1 != null && (
                <div className={`bfb-wait${wait1 < BFB_MIN_TRANSFER_MIN ? ' bad' : ''}`}>
                  {fmtConnMinutes(wait1, lang)}
                  <span className="bfb-wait-label">{t.waitWord}</span>
                </div>
              )}
            </div>

            <div className="bfb-col">
              <div className="bfb-col-title">✈ {t.flightLeg}</div>
              <div className="bfb-col-sub">{originIata} → {destIata}</div>
              {flightOptions.map((f, i) => (
                <BFBOption
                  key={f.id || i}
                  kind="flight"
                  opt={f}
                  selected={i === flightIdx}
                  disabled={!flightValidGivenBus1(sel1, f)}
                  onSelect={() => pickFlight(i)}
                  currency={currency}
                  lang={lang}
                />
              ))}
            </div>

            <div className="bfb-gutter">
              <div className="bfb-arrow-h">→</div>
              {wait2 != null && (
                <div className={`bfb-wait${wait2 < BFB_MIN_TRANSFER_MIN ? ' bad' : ''}`}>
                  {fmtConnMinutes(wait2, lang)}
                  <span className="bfb-wait-label">{t.waitWord}</span>
                </div>
              )}
            </div>

            <div className="bfb-col">
              <div className="bfb-col-title">🚌 {t.busLeg} 2</div>
              <div className="bfb-col-sub">{destCity}</div>
              {bus2Options.map((b, i) => (
                <BFBOption
                  key={b.id || i}
                  kind="bus"
                  opt={b}
                  selected={i === bus2Idx}
                  disabled={!bus2ValidGivenFlight(selF, b)}
                  onSelect={() => pickBus2(i)}
                  currency={currency}
                  lang={lang}
                />
              ))}
            </div>
          </div>

          <div className="bfb-summary">
            <div className="bfb-summary-route">
              <strong>
                {sel1?.dep || '—'} → {selF?.dep || '—'} → {sel2?.dep || '—'}
              </strong>
            </div>
            <div className="bfb-summary-totals">
              <span>{t.totalTripLabel}: {totalMin != null ? fmtConnMinutes(totalMin, lang) : '—'}</span>
              <span>{t.totalPriceLabel}: {totalPrice != null ? fmt(totalPrice) : '—'}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
