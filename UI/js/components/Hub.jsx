// ── Hub matrix: option rows, picker logic, and the master multi-leg card ────
// ── HUB CARD HELPERS ─────────────────────────────────────────────────────────
// Connection thresholds (minutes). Pill is a visual signal: green ≥ 90, yellow 45–89, red < 45.
// AI picks (cheapest/fastest/earliest) require the project-wide 2h floor matching backend's
// min_total_price rule, so suggestions never include impractical sub-2h transfers.
const CONN_GREEN_MIN = 90;
const CONN_RED_MIN = 45;
const CONN_LONG_MIN = 480; // 8h: above this the layover is "too long" (gray pill).
const PICK_MIN_CONNECTION = 120;


// ── HUB ROW (one bus or flight option in the matrix) ─────────────────────────
// `waitInfo`: when this row is the second leg AND a first-leg pick exists, render
// a colored "Xs Ydk bekleme" line below the carrier meta to expose the layover.
// `airportTag`: { color, iata } — when the hub's flight options span multiple
// origin/destination airports (e.g. flight+bus with MXP+LIN origin), this row
// gets a colored IATA pill so the user can tell which airport this option uses.
function HubOptionRow({ kind, opt, selected, onSelect, badges, currency, lang, disabled, waitInfo, airportTag }) {
  const t = T[lang];
  const fmt = (n) => formatPrice(n, currency);
  const time = opt.dep && opt.arr ? `${opt.dep}→${opt.arr}` : (opt.dep || '—');
  const meta = kind === 'bus' ?
    `${opt.duration || ''} · ${opt.company || 'FlixBus'}` :
    `${opt.duration || ''} · ${opt.airline || ''}${opt.flightNo ? ' ' + opt.flightNo : ''}${(opt.stops ?? 0) > 0 ? ` · ${opt.stops} ${t.transfer}` : ''}`;
  const cls = `hub-row${selected ? ' selected' : ''}${disabled ? ' disabled' : ''}`;
  const handleClick = disabled ? undefined : onSelect;
  const title = disabled ? t.optionDisabledHint : undefined;
  return (
    <div className={cls} onClick={handleClick} title={title}>
      <div className="hub-row-radio" />
      <div className="hub-row-body">
        <div className="hub-row-line1">
          <span className="hub-row-time">{time}</span>
          {opt.nextDay && <span className="hub-row-tag next">+1</span>}
          {kind === 'flight' && opt.flightType === 'Best' && <span className="hub-row-tag best">{t.bestFlight.split(' ')[0]}</span>}
          {airportTag && (
            <span className="hub-row-tag" style={{ background: airportTag.color, color: '#fff' }}>{airportTag.iata}</span>
          )}
        </div>
        <div className="hub-row-line2">
          <span>{meta}</span>
          {badges?.cheap && <span className="hub-row-tag cheap">{t.cheapestBadge}</span>}
          {badges?.fast && <span className="hub-row-tag fast">{t.fastestBadge}</span>}
          {waitInfo && waitInfo.minutes != null && (
            <span className="hub-row-wait" style={{ color: waitInfo.color }}>
              <span className="hub-row-wait-dot" style={{ background: waitInfo.color }} />
              {fmtConnMinutes(waitInfo.minutes)} {t.waitWord}
            </span>
          )}
        </div>
      </div>
      <div className="hub-row-price">{opt.price != null ? fmt(opt.price) : '—'}</div>
    </div>);
}

// Sort options chronologically by departure (missing depISO sinks to bottom).

// ── HUB MASTER CARD ──────────────────────────────────────────────────────────
function HubMasterCard({ hubData, currency, lang, defaultExpanded = false, date, preSelectBusId = null, preSelectFlightId = null, autoExpand = false }) {
  const t = T[lang];
  const fmt = (n) => formatPrice(n, currency);
  const mode = hubData.mode;
  const { busOptions, flightOptions, hub } = hubData;

  // Heuristic picks (used for the in-row badges next to the price).
  const cheapestPick = pickCheapest(busOptions, flightOptions, mode);
  const fastestPick = pickFastest(busOptions, flightOptions, mode);

  // Pill color rules for flight option rows: when multiple distinct origin
  // (flight+bus) or destination (bus+flight) IATAs exist, palette colors call
  // out the difference; with a single airport, pills still appear so the user
  // sees which airport they're flying — just in the section's default color.
  // `flightOptionAirportKey` picks fromIata for flight+bus, toIata for bus+flight.
  const flightOptionAirportKey = mode === 'flight_plus_bus' ? 'fromIata' : 'toIata';
  const flightAirportColorMap = (() => {
    const distinct = [...new Set(flightOptions.map((f) => f[flightOptionAirportKey]).filter(Boolean))];
    if (distinct.length < 2) return null;
    const map = {};
    distinct.forEach((iata, i) => { map[iata] = ARR_AIRPORT_PALETTE[i % ARR_AIRPORT_PALETTE.length]; });
    return map;
  })();

  // Default selection: cheapest flight + bus with the *tightest* valid (≥2h)
  // connection. Falls back to plain cheapest combo when no flight in the list
  // has any valid bus pairing. (Cheapest/Fastest badges below stay independent.)
  const tightPick = pickTightConnection(busOptions, flightOptions, mode);
  const initial = (() => {
    if (preSelectBusId && preSelectFlightId) {
      const busExists = busOptions.some((b) => b.id === preSelectBusId);
      const flightExists = flightOptions.some((f) => f.id === preSelectFlightId);
      if (busExists && flightExists) return { busId: preSelectBusId, flightId: preSelectFlightId };
    }
    return tightPick || cheapestPick || (busOptions[0] && flightOptions[0]
      ? { busId: busOptions[0].id, flightId: flightOptions[0].id }
      : { busId: null, flightId: null });
  })();
  const [sel, setSel] = useState(initial);
  const [showMap, setShowMap] = useState(false);
  // 0=collapsed (5 options), 1=mid (+6 more), 2=all
  const [busStage, setBusStage] = useState(0);
  const [flightStage, setFlightStage] = useState(0);
  // Card itself starts collapsed — user expands it from the header click.
  // The top card in a section gets defaultExpanded=true so users don't open an
  // entirely shut accordion on first paint.
  const [expanded, setExpanded] = useState(defaultExpanded || autoExpand);
  // Smart picks (AI top-3 button) — null = not requested, [] = no result
  const [smart, setSmart] = useState(null);
  const [smartLoading, setSmartLoading] = useState(false);

  // Reset selection when a new search lands (hubData identity changes).
  useEffect(() => {
    if (preSelectBusId && preSelectFlightId) {
      const busExists = busOptions.some((b) => b.id === preSelectBusId);
      const flightExists = flightOptions.some((f) => f.id === preSelectFlightId);
      if (busExists && flightExists) {
        setSel({ busId: preSelectBusId, flightId: preSelectFlightId });
        setBusStage(0); setFlightStage(0);
        setExpanded(true);
        setSmart(null);
        return;
      }
    }
    const fresh = pickTightConnection(busOptions, flightOptions, mode)
      || pickCheapest(busOptions, flightOptions, mode);
    if (fresh) {
      setSel({ busId: fresh.busId, flightId: fresh.flightId });
    } else if (busOptions[0] && flightOptions[0]) {
      setSel({ busId: busOptions[0].id, flightId: flightOptions[0].id });
    }
    setBusStage(0);
    setFlightStage(0);
    setExpanded(defaultExpanded);
    setSmart(null);
  }, [hubData, preSelectBusId, preSelectFlightId]);

  const selBus = busOptions.find((b) => b.id === sel.busId) || null;
  const selFlight = flightOptions.find((f) => f.id === sel.flightId) || null;
  const conn = (selBus && selFlight) ? calcConnection(selBus, selFlight, mode) : null;
  const connStyle = conn ? connStyleFor(conn.level, t) : null;
  const total = (selBus?.price ?? 0) + (selFlight?.price ?? 0);
  const overallMin = totalTripMin(selBus, selFlight, mode);

  // Toggle picks: clicking a row that is already selected clears it. This unlocks
  // any rows that were disabled because they could not connect to that pick —
  // exactly the "tıklanan elemana bir daha tıkladığında, tıklanma kalkıcak ve
  // tıklanamayan elemanlar tıklanabilir olacak" behavior.
  const onPickBus = (id) => { setSel((s) => ({ ...s, busId: s.busId === id ? null : id })); };
  const onPickFlight = (id) => { setSel((s) => ({ ...s, flightId: s.flightId === id ? null : id })); };

  // Per-row badge map (which option belongs to a heuristic pick)
  const badgesFor = (kind, id) => ({
    cheap: cheapestPick && cheapestPick[kind === 'bus' ? 'busId' : 'flightId'] === id,
    fast: fastestPick && fastestPick[kind === 'bus' ? 'busId' : 'flightId'] === id,
  });

  // Wait info for an option in the SECOND leg column, paired with the current
  // first-leg pick. Returns null when the first leg isn't picked yet (so we
  // skip rendering the "X bekleme" line until both sides exist).
  const waitInfoFor = (kind, opt) => {
    const firstSel = mode === 'bus_plus_flight' ? selBus : selFlight;
    if (!firstSel) return null;
    const isSecondLeg = mode === 'bus_plus_flight' ? kind === 'flight' : kind === 'bus';
    if (!isSecondLeg) return null;
    const [bus, flight] = mode === 'bus_plus_flight' ? [firstSel, opt] : [opt, firstSel];
    const c = calcConnection(bus, flight, mode);
    if (c.minutes == null) return null;
    return { minutes: c.minutes, color: connStyleFor(c.level, t).color };
  };

  const noPairing = hubData.minTotal == null;

  const mapRoute = (selBus && selFlight) ? { segments: buildHubMapSegs(hubData, selBus, selFlight) } : null;

  const hasTrains = busOptions.some((b) => b.type === 'Train');
  const groundColIcon = hasTrains ? '🚌🚆' : '🚌';
  const busColTitle = (
    <div className="hub-col-title">
      <span className="hub-col-icon">{groundColIcon}</span>{hasTrains ? t.chooseGroundLabel : t.chooseBusLabel}
    </div>);
  const flightColTitle = (
    <div className="hub-col-title">
      <span className="hub-col-icon">✈</span>{t.chooseFlightLabel}
    </div>);

  // Sort chronologically. Stage-based collapse: 0 → 6, 1 → 6+6, 2 → all.
  const COLLAPSE_BASE = 6;
  const COLLAPSE_STEP = 6;
  const sortedBuses = sortByDep(busOptions);
  const sortedFlights = sortByPrice(flightOptions);

  const stageLimit = (stage, total) => {
    if (stage >= 2) return total;
    if (stage === 1) return Math.min(total, COLLAPSE_BASE + COLLAPSE_STEP);
    return Math.min(total, COLLAPSE_BASE);
  };
  const busLimit = stageLimit(busStage, sortedBuses.length);
  const flightLimit = stageLimit(flightStage, sortedFlights.length);
  const visibleBuses = visibleOptions(sortedBuses, sel.busId, busLimit);
  const visibleFlights = visibleOptions(sortedFlights, sel.flightId, flightLimit);

  // Disable rule: a bus that can't connect to the currently-selected flight (and vice versa)
  // becomes unclickable. Mirrors the 2h rule used by min_total_price.
  const isBusDisabled = (b) => {
    if (!selFlight) return false;
    if (b.id === sel.busId) return false;
    return calcConnection(b, selFlight, mode).level === 'red';
  };
  const isFlightDisabled = (f) => {
    if (!selBus) return false;
    if (f.id === sel.flightId) return false;
    return calcConnection(selBus, f, mode).level === 'red';
  };

  const stepBusStage = () => setBusStage((s) => Math.min(2, s + 1));
  const stepFlightStage = () => setFlightStage((s) => Math.min(2, s + 1));

  const toggleLabel = (stage, total) => {
    if (stage >= 2) return t.showFewer;
    const nextLimit = stageLimit(stage + 1, total);
    return stage + 1 >= 2 || nextLimit >= total
      ? `${t.showAllOptions} (${total})`
      : t.showMoreOptions;
  };

  const busCol = (
    <div className="hub-col">
      {busColTitle}
      {visibleBuses.map((b) => (
        <HubOptionRow key={b.id} kind="bus" opt={b}
          selected={b.id === sel.busId}
          disabled={isBusDisabled(b)}
          onSelect={() => onPickBus(b.id)}
          badges={badgesFor('bus', b.id)}
          waitInfo={waitInfoFor('bus', b)}
          currency={currency} lang={lang} />
      ))}
      {sortedBuses.length > COLLAPSE_BASE && (
        <button
          className={`hub-col-toggle${busStage >= 2 ? ' expanded' : ''}`}
          onClick={() => (busStage >= 2 ? setBusStage(0) : stepBusStage())}
          type="button">
          <span>{toggleLabel(busStage, sortedBuses.length)}</span>
          <span className="arrow">▾</span>
        </button>
      )}
    </div>);
  const flightCol = (
    <div className="hub-col">
      {flightColTitle}
      {visibleFlights.map((f) => {
        const tagIata = f[flightOptionAirportKey];
        // Always show the IATA pill; color it from the palette only when the
        // hub has multi-airport flight options. Single-airport hubs use the
        // section's blue (matches the dest-side pill on flight cards).
        const tagColor = flightAirportColorMap && tagIata ? flightAirportColorMap[tagIata] : '#2563EB';
        const airportTag = tagIata ? { color: tagColor, iata: tagIata } : null;
        return (
          <HubOptionRow key={f.id} kind="flight" opt={f}
            selected={f.id === sel.flightId}
            disabled={isFlightDisabled(f)}
            onSelect={() => onPickFlight(f.id)}
            badges={badgesFor('flight', f.id)}
            waitInfo={waitInfoFor('flight', f)}
            airportTag={airportTag}
            currency={currency} lang={lang} />
        );
      })}
      {sortedFlights.length > COLLAPSE_BASE && (
        <button
          className={`hub-col-toggle${flightStage >= 2 ? ' expanded' : ''}`}
          onClick={() => (flightStage >= 2 ? setFlightStage(0) : stepFlightStage())}
          type="button">
          <span>{toggleLabel(flightStage, sortedFlights.length)}</span>
          <span className="arrow">▾</span>
        </button>
      )}
    </div>);

  // ── AI smart picks (global Top-3 across all valid combos) ──────────────────
  // Walk every (bus,flight) pair, drop ones that don't satisfy the 2h rule, pass
  // a compact summary to the model and ask for a 3-element JSON ranking. We
  // only pass the prompt+combos and let the model attach a label and reason.
  async function getSmartPicks() {
    setSmartLoading(true);
    try {
      const combos = [];
      for (const f of flightOptions) {
        for (const b of busOptions) {
          const c = calcConnection(b, f, mode);
          if (c.minutes == null || c.minutes < PICK_MIN_CONNECTION) continue;
          const overall = totalTripMin(b, f, mode);
          combos.push({
            flight: f, bus: b,
            wait: c.minutes,
            overall,
            price: (b.price ?? 0) + (f.price ?? 0)
          });
        }
      }
      if (combos.length === 0) {
        setSmart([{ label: '⚠️', reason: t.aiPickEmpty, combo: null }]);
        setSmartLoading(false);
        return;
      }
      const summary = combos.map((c) => ({
        f: `${c.flight.airline || ''} ${c.flight.flightNo || ''} ${c.flight.dep}-${c.flight.arr} €${c.flight.price}`.trim(),
        b: `${c.bus.company || 'Bus'} ${c.bus.dep}-${c.bus.arr} €${c.bus.price}`,
        wait: fmtConnMinutes(c.wait), overall: fmtConnMinutes(c.overall), price: `€${c.price}`
      }));
      const prompt = `Sen seyahat asistanısın. Aşağıdaki ${mode === 'flight_plus_bus' ? 'uçak+otobüs' : 'otobüs+uçak'} kombinasyonlarından en iyi 3'ünü seç. Her biri için: bir kategori etiketi (örn: "🌅 Sabah erken", "💰 En ucuz", "⚡ En hızlı", "🌙 Gece yolculuğu") ve 1 cümlelik tavsiye yaz.
Kombinasyonlar:
${summary.map((s, i) => `${i + 1}. ${mode === 'flight_plus_bus' ? `Uçak ${s.f}, sonra Otobüs ${s.b}` : `Otobüs ${s.b}, sonra Uçak ${s.f}`} | bekleme: ${s.wait} | toplam yolculuk: ${s.overall} | toplam: ${s.price}`).join('\n')}

Sadece JSON döndür: [{"index": 1-based, "label": "🌅 Sabah erken", "reason": "..."}]
3 öğe içersin.`;
      let txt = '';
      if (window.claude && typeof window.claude.complete === 'function') {
        txt = await window.claude.complete(prompt);
      } else {
        // Fallback: pick three deterministic combos so the panel is still useful
        // when the page is opened outside the Claude harness.
        const cheap = [...combos].sort((a, b) => a.price - b.price)[0];
        const fast = [...combos].sort((a, b) => a.overall - b.overall)[0];
        const balanced = [...combos].sort((a, b) => (a.price * 2 + a.overall) - (b.price * 2 + b.overall))[0];
        const fakePicks = [];
        const seen = new Set();
        for (const [c, label, reason] of [
          [cheap, '💰 En ucuz', 'Toplam fiyatta en avantajlı seçenek.'],
          [fast, '⚡ En hızlı', 'Toplam yolculuk süresi en kısa.'],
          [balanced, '🎯 Dengeli', 'Fiyat ve süre dengesinde en iyi.']
        ]) {
          if (!c) continue;
          const key = `${c.flight.id}|${c.bus.id}`;
          if (seen.has(key)) continue;
          seen.add(key);
          fakePicks.push({ index: combos.indexOf(c) + 1, label, reason });
        }
        txt = JSON.stringify(fakePicks);
      }
      const jsonMatch = txt.match(/\[[\s\S]*\]/);
      if (jsonMatch) {
        const picks = JSON.parse(jsonMatch[0]);
        setSmart(picks.map((p) => ({ ...p, combo: combos[p.index - 1] })).filter((p) => p.combo));
      } else {
        setSmart([{ label: '⚠️', reason: t.aiError, combo: null }]);
      }
    } catch (e) {
      console.error(e);
      setSmart([{ label: '⚠️', reason: t.aiError, combo: null }]);
    }
    setSmartLoading(false);
  }

  const applySmartCombo = (c) => {
    if (!c) return;
    setSel({ busId: c.bus.id, flightId: c.flight.id });
  };

  // ── Booking links ──────────────────────────────────────────────────────────
  // Bus URL stays as a flat https://www.omio.com — wire the proper Omio search
  // shape later when we know which fields it accepts.
  // Flight leg endpoints depend on the trip shape: in flight_plus_bus the flight
  // goes origin → hub; in bus_plus_flight it goes hub → destination. Prefer the
  // flight option's own fromIata/toIata when present, fall back to hubData.
  const derivedFlightFrom = mode === 'flight_plus_bus' ? hubData.depIata : hub.iata;
  const derivedFlightTo   = mode === 'flight_plus_bus' ? hub.iata        : hubData.arrIata;
  const skyscannerUrl = buildSkyscannerUrl({
    fromIata: selFlight?.fromIata || derivedFlightFrom,
    toIata:   selFlight?.toIata   || derivedFlightTo,
    date,
    depTime:     selFlight?.dep,
    stops:       selFlight?.stops,
    durationMin: selFlight?.durationMin,
  });
  const isTrainGround = selBus?.type === 'Train';
  const groundUrl = selBus?.url || (isTrainGround ? 'https://www.omio.com/' : 'https://www.omio.com/');

  return (
    <div className={`hub-card${noPairing ? ' no-pairing' : ''}${expanded ? ' expanded' : ' collapsed'}`}>
      <div
        className="hub-card-head"
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExpanded((v) => !v); } }}
      >
        <div className="hub-iata-badge">{hub.iata}</div>
        <div className="hub-head-info">
          <div className="hub-head-title">{hub.city} {t.hubViaTitle}</div>
          <div className="hub-head-sub">
            {hub.distanceKm != null ? `${Math.round(hub.distanceKm)} km ${t.hubDistance}` : ''}
            {' · '}{(() => {
              const tc = busOptions.filter((b) => b.type === 'Train').length;
              const bc = busOptions.length - tc;
              const parts = [];
              if (bc > 0) parts.push(`${bc} ${t.hubBusOptions}`);
              if (tc > 0) parts.push(`${tc} ${t.hubTrainOptions}`);
              return parts.join(', ');
            })()} · {flightOptions.length} {t.hubFlightOptions}
          </div>
        </div>
        <div className="hub-head-min">
          <div className="hub-head-min-label">{t.hubMinFromLabel}</div>
          <div className="hub-head-min-price">{hubData.minTotal != null ? fmt(hubData.minTotal) : '—'}</div>
          {noPairing && <div className="hub-head-min-warn">⚠ &lt;2h</div>}
        </div>
        <div className={`hub-card-caret${expanded ? ' open' : ''}`} aria-hidden="true">▾</div>
      </div>

      {noPairing && expanded && <div className="hub-no-pairing-banner">⚠ {t.noValidPairing}</div>}

      {expanded && <>
      {/* Global AI Top-3 picker — replaces the old En ucuz/En hızlı/Sabah erken chips. */}
      <div className="hub-smart-bar">
        {!smart && !smartLoading && (
          <button className="hub-smart-btn" onClick={getSmartPicks}>
            {t.aiTopBtn} <small>{t.aiTopBtnSmall}</small>
          </button>
        )}
        {smartLoading && <div className="hub-smart-loading">{t.aiTopLoading}</div>}
        {smart && (
          <div className="hub-smart-row">
            <div className="hub-smart-title">{t.aiSmartTitle}</div>
            <div className="hub-smart-cards">
              {smart.map((s, i) => (
                <button key={i} className="hub-smart-card"
                  onClick={() => applySmartCombo(s.combo)}
                  disabled={!s.combo}>
                  <div className="hub-smart-label">{s.label}</div>
                  <div className="hub-smart-reason">{s.reason}</div>
                  {s.combo && (
                    <div className="hub-smart-meta">{fmt(s.combo.price)} · {fmtConnMinutes(s.combo.overall)}</div>
                  )}
                </button>
              ))}
            </div>
            <button className="hub-smart-clear" title={t.aiClearTitle} onClick={() => setSmart(null)}>✕</button>
          </div>
        )}
      </div>

      <div className="hub-grid">
        {mode === 'bus_plus_flight' ? <>{busCol}{flightCol}</> : <>{flightCol}{busCol}</>}
      </div>

      {/* SEÇTİĞİN KOMBİNASYON: 3-card timeline (leg1 / wait / leg2) + totals + buy buttons. */}
      <div className="hub-summary">
        <div className="hub-summary-title">{t.summaryHeader}</div>
        <div className="hub-summary-timeline">
          {(() => {
            const flightCarrier = `${selFlight?.airline || ''}${selFlight?.flightNo ? ' ' + selFlight.flightNo : ''}`.trim();
            const busCarrier = selBus?.company || (lang === 'tr' ? 'Otobüs' : 'Bus');
            const flightCard = (
              <div className="hubs-leg">
                <div className="hubs-leg-label">✈ {t.flightLegLabel}</div>
                <div className="hubs-leg-times">
                  {selFlight ? `${selFlight.dep} → ${selFlight.arr}` : '— → —'}
                </div>
                <div className="hubs-leg-meta">
                  {selFlight ? `${flightCarrier} · ${fmt(selFlight.price)}` : '—'}
                </div>
              </div>);
            const isBusGround = !selBus || selBus.type !== 'Train';
            const busCard = (
              <div className="hubs-leg">
                <div className="hubs-leg-label">{isBusGround ? '🚌' : '🚆'} {isBusGround ? t.busLegLabel : t.trainLegLabel}</div>
                <div className="hubs-leg-times">
                  {selBus ? `${selBus.dep} → ${selBus.arr}${selBus.nextDay ? ' (+1)' : ''}` : '— → —'}
                </div>
                <div className="hubs-leg-meta">
                  {selBus ? `${busCarrier} · ${fmt(selBus.price)}` : '—'}
                </div>
              </div>);
            const waitCard = (
              <div className="hubs-wait" style={connStyle ? {
                background: connStyle.bg, color: connStyle.color, borderColor: connStyle.color
              } : {
                background: 'var(--bg)', color: 'var(--lt)', borderColor: 'var(--brd)'
              }}>
                <div className="hubs-wait-icon">⏱</div>
                <div>
                  <div className="hubs-wait-time">
                    {(conn && conn.minutes != null) ? fmtConnMinutes(conn.minutes) : t.waitMissing}
                  </div>
                  <div className="hubs-wait-label">
                    {connStyle ? <><strong>{connStyle.label}</strong> · {hub.iata} {t.waitWord}</> : `${hub.iata} ${t.waitWord}`}
                  </div>
                  {connStyle && (
                    <div className="hubs-wait-desc">
                      {connStyle.desc} · {mode === 'flight_plus_bus' ? t.transferDescF2B : t.transferDescB2F}
                    </div>
                  )}
                </div>
              </div>);
            return mode === 'bus_plus_flight'
              ? <>{busCard}{waitCard}{flightCard}</>
              : <>{flightCard}{waitCard}{busCard}</>;
          })()}
        </div>
        <div className="hub-summary-bottom">
          <div className="hub-summary-stats">
            <div>
              <small>{t.totalTripLabel}</small>
              <span>{overallMin != null ? fmtConnMinutes(overallMin) : '—'}</span>
            </div>
            <div>
              <small>{t.totalPriceLabel}</small>
              <span className="price">{(selBus && selFlight) ? fmt(total) : '—'}</span>
            </div>
          </div>
          <div className="hub-summary-actions">
            <button className="hub-buy-btn map" onClick={() => setShowMap((p) => !p)}>
              🗺 {showMap ? t.mapHideBtn : t.mapShowBtn}
            </button>
            <a className="hub-buy-btn flight" href={skyscannerUrl} target="_blank" rel="noopener noreferrer">
              ✈ {t.bookFlightBtn} ↗
            </a>
            <a className="hub-buy-btn bus" href={groundUrl} target="_blank" rel="noopener noreferrer">
              {isTrainGround ? '🚆' : '🚌'} {isTrainGround ? t.bookTrainBtn : t.bookBusBtn} ↗
            </a>
          </div>
        </div>
        {showMap && mapRoute && (
          <div style={{ marginTop: 12 }}>
            <InlineMap route={mapRoute} onClose={() => setShowMap(false)} lang={lang}
              hubIata={hub.iata}
              hubWait={conn && conn.minutes != null ? { minutes: conn.minutes, color: connStyle?.color, bg: connStyle?.bg, label: t.waitWord } : null} />
          </div>
        )}
      </div>
      </>}
    </div>);
}
