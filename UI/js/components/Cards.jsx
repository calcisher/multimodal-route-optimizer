// ── Result cards: Flight, Transfer, Combo (flight+ground), and Ground ────────
// ── Flight card (direct) ──────────────────────────────────────────────────────
function FlightCard({ d, currency, lang, date }) {
  const fmt = (n) => currency === "USD" ? `$${n}` : `€${n}`;
  const t = T[lang || 'tr'];
  const route = { segments: [{ from: d.depIata, to: d.arrIata, type: 'flight', carrier: d.airline, duration: d.duration }] };
  const buyUrl = buildSkyscannerUrl({
    fromIata: d.depIata, toIata: d.arrIata, date, depTime: d.dep, stops: d.stops,
  });
  const detailSegs = [{
    type: 'flight', from: d.depIata, to: d.arrIata, fromName: CITY_NAMES[d.depIata], toName: CITY_NAMES[d.arrIata],
    carrier: d.airline, ref: d.flightNo, dep: d.dep, arr: d.arr, duration: d.duration, price: d.price,
    buyUrl,
  }];
  const header =
  <>
      <div className="card-airline">{d.airline}<span>{d.flightNo}</span></div>
      <div className="card-times" style={{ flex: 1 }}>
        <div className="card-dep">{d.dep}</div>
        <div className="card-via" style={{ flex: 1 }}>
          <div className="via-row"><div className="via-dot" style={{ background: 'var(--or)' }} /><div className="via-line" /><span style={{ fontSize: 13 }}>✈</span><div className="via-line" /><div className="via-dot" style={{ background: 'var(--blue)' }} /></div>
          <div className="card-via-text">{d.via ? `via ${d.viaName || ''} (${d.via})` : t.nonstop}</div>
        </div>
        <div className="card-arr">{d.arr}</div>
      </div>
      <div className="card-dur">{d.duration}<small>{d.via ? `via ${d.via}` : d.stops === 0 ? t.nonstop : '1 ' + t.transfer}</small></div>
      <div className="card-price">{fmt(d.price)}<small>/ kişi</small></div>
    </>;

  return <FlightOperationalDetailShell header={header} mapRoute={route} lang={lang || 'tr'} detailSegs={detailSegs}
    totalPrice={d.price} totalDuration={d.duration} stops={d.stops || 0} currency={currency} buyUrl={buyUrl} />;
}

// ── Transfer flight card ──────────────────────────────────────────────────────
function TransferCard({ d, currency, lang, date }) {
  const t = T[lang];
  const fmt = (n) => currency === "USD" ? `$${n}` : `€${n}`;
  const route = { segments: d.legs.map((l) => ({ from: l.from, to: l.to, type: 'flight', carrier: d.airline, duration: l.duration })) };
  const buyUrl = buildSkyscannerUrl({
    fromIata: d.depIata, toIata: d.arrIata, date, depTime: d.legs[0]?.dep || d.dep, stops: d.stops,
  });
  const detailSegs = d.legs.map((l, i) => ({
    type: 'flight', from: l.from, to: l.to, fromName: CITY_NAMES[l.from], toName: CITY_NAMES[l.to],
    carrier: d.airline, ref: l.flightNo, dep: l.dep, arr: l.arr, duration: l.duration,
    price: i === 0 ? d.price : null, buyUrl,
  }));
  const header =
  <>
      <div className="card-airline">{d.airline}<span style={{ fontSize: 10, lineHeight: 1.4, color: 'var(--lt)' }}>{d.legs[0].flightNo}<br />{d.legs[1].flightNo}</span></div>
      <div className="transfer-legs" style={{ flex: 1 }}>
        <div className="tleg"><div className="tleg-time">{d.legs[0].dep}</div><div className="tleg-iata">{d.legs[0].from}</div></div>
        <div className="tleg-sep" style={{ flex: 1 }}><div className="tleg-flight-bar"><div className="tleg-seg-line" /><span className="tleg-plane">✈</span><div className="tleg-seg-line" /></div><div className="tleg-meta">{d.legs[0].duration}</div></div>
        <div className="tleg" style={{ flexShrink: 0 }}>
          <div className="tleg-time" style={{ fontSize: 14 }}>{d.legs[0].arr}</div><div className="tleg-iata">{d.legs[0].to}</div>
          <div style={{ marginTop: 3 }}><span className="layover-badge">⏱ {d.layover.duration} {t.layover}</span></div>
          <div className="tleg-iata" style={{ marginTop: 2 }}>{d.legs[1].dep}</div>
        </div>
        <div className="tleg-sep" style={{ flex: 1 }}><div className="tleg-flight-bar"><div className="tleg-seg-line" /><span className="tleg-plane">✈</span><div className="tleg-seg-line" /></div><div className="tleg-meta">{d.legs[1].duration}</div></div>
        <div className="tleg"><div className="tleg-time">{d.legs[1].arr}</div><div className="tleg-iata">{d.legs[1].to}</div></div>
      </div>
      <div className="card-dur">{d.totalDuration}<small>{d.stops} {t.transfer}</small></div>
      <div className="card-price">{fmt(d.price)}<small>/ kişi</small></div>
    </>;

  return <FlightOperationalDetailShell header={header} mapRoute={route} lang={lang} detailSegs={detailSegs}
    totalPrice={d.price} totalDuration={d.totalDuration || d.duration} stops={d.stops || Math.max(0, d.legs.length - 1)}
    currency={currency} buyUrl={buyUrl} />;
}

// ── Combo card ────────────────────────────────────────────────────────────────
function ComboCard({ d, currency, mode, lang }) {
  const t = T[lang];
  const fmt = (n) => currency === "USD" ? `$${n}` : `€${n}`;
  const isBus = d.ground.type === 'Bus';
  const gColor = isBus ? 'var(--or)' : 'var(--green)';
  const gBg = isBus ? 'var(--or-s)' : 'var(--green-s)';
  const gType = d.ground.type.toLowerCase();
  const flightAirline = d.flight.airline || '';

  // Normalize flight legs so a direct flight and a multi-stop one share the
  // same rendering path. Each leg is { dep, arr, from, to, fromName, toName,
  // duration, flightNo, airline }.
  const hasMultiLeg = Array.isArray(d.flight.legs) && d.flight.legs.length > 0;
  const fallbackFlightFrom = d.flight.fromIata || (mode === 'flightFirst' ? d.depIata : d.via);
  const fallbackFlightTo = d.flight.toIata || (mode === 'flightFirst' ? d.via : d.arrIata);
  const flightLegs = hasMultiLeg ? d.flight.legs : [{
    dep: d.flight.dep, arr: d.flight.arr,
    from: fallbackFlightFrom, to: fallbackFlightTo,
    fromName: '', toName: '',
    duration: d.flight.duration,
    flightNo: d.flight.flightNo,
    airline: flightAirline,
  }];
  const flightLayovers = Array.isArray(d.flight.layovers) ? d.flight.layovers : [];
  const stopsCount = (typeof d.flight.stops === 'number' ? d.flight.stops : flightLegs.length - 1);
  const flightFirstDep = flightLegs[0].dep;
  const flightLastArr = flightLegs[flightLegs.length - 1].arr;

  // Map segments — every flight leg is its own arc, plus the ground leg.
  const flightMapSegs = flightLegs.map((l) => ({
    from: l.from, to: l.to, type: 'flight',
    carrier: l.airline || flightAirline, duration: l.duration }));

  const groundMapSeg = mode === 'flightFirst' ?
  { from: d.via, to: d.arrIata, type: gType, carrier: d.ground.company, duration: d.ground.duration } :
  { from: d.depIata, to: d.via, type: gType, carrier: d.ground.company, duration: d.ground.duration };
  const mapSegs = mode === 'flightFirst' ? [...flightMapSegs, groundMapSeg] : [groundMapSeg, ...flightMapSegs];

  // Layover nodes for stops between consecutive flight legs.
  const flightLayoverNodes = flightLegs.slice(1).map((l, i) => {
    const lay = flightLayovers[i] || {};
    return {
      iata: l.from,
      name: lay.city || CITY_NAMES[l.from] || l.from,
      arr: flightLegs[i].arr,
      dep: l.dep,
      layover: lay.duration || calcLayover({ arr: flightLegs[i].arr }, { dep: l.dep })
    };
  });

  let nodes;
  if (mode === 'flightFirst') {
    const viaLayover = calcLayover({ arr: flightLastArr }, { dep: d.ground.dep });
    nodes = [
    { iata: d.depIata, dep: flightFirstDep },
    ...flightLayoverNodes,
    { iata: d.via, name: CITY_NAMES[d.via], arr: flightLastArr, dep: d.ground.dep, layover: viaLayover },
    { iata: d.arrIata, arr: d.ground.arr, arrNextDay: !!d.ground.nextDay }];

  } else {
    const viaLayover = calcLayover({ arr: d.ground.arr }, { dep: flightFirstDep });
    nodes = [
    { iata: d.depIata, dep: d.ground.dep },
    { iata: d.via, name: CITY_NAMES[d.via], arr: d.ground.arr, dep: flightFirstDep, layover: viaLayover },
    ...flightLayoverNodes,
    { iata: d.arrIata, arr: flightLastArr }];

  }

  const flightJourneySegs = flightLegs.map((l) => ({
    type: 'flight', duration: l.duration,
    carrier: l.airline || flightAirline, ref: l.flightNo }));

  const groundJourneySeg = { type: gType, duration: d.ground.duration, carrier: d.ground.company };
  const journey = {
    nodes,
    segs: mode === 'flightFirst' ? [...flightJourneySegs, groundJourneySeg] : [groundJourneySeg, ...flightJourneySegs]
  };

  // Detail panel — one card per real leg (flight legs split out individually).
  const flightFirstFromIata = flightLegs[0].from;
  const flightLastToIata = flightLegs[flightLegs.length - 1].to;
  const flightDetailSegs = flightLegs.map((l, i) => ({
    type: 'flight',
    from: l.from, to: l.to,
    fromName: l.fromName || CITY_NAMES[l.from] || l.from,
    toName: l.toName || CITY_NAMES[l.to] || l.to,
    carrier: l.airline || flightAirline,
    ref: l.flightNo,
    dep: l.dep, arr: l.arr,
    duration: l.duration,
    price: i === 0 ? d.flight.price : null,
    buyUrl: `https://www.google.com/travel/flights?q=${flightFirstFromIata}+to+${flightLastToIata}`
  }));
  const groundDetailSeg = {
    type: gType, from: d.ground.from, to: d.ground.to,
    fromName: d.ground.from, toName: d.ground.to,
    carrier: d.ground.company, ref: '',
    dep: d.ground.dep, arr: d.ground.arr, nextDay: d.ground.nextDay,
    duration: d.ground.duration, price: d.ground.price,
    buyUrl: 'https://www.omio.com'
  };
  const detailSegs = mode === 'flightFirst' ? [...flightDetailSegs, groundDetailSeg] : [groundDetailSeg, ...flightDetailSegs];

  const flightPackage = flightLegs.length > 1 ? {
    price: d.flight.price,
    legCount: flightLegs.length,
    airline: flightAirline
  } : null;

  const stopsSuffix = stopsCount > 0 ? ` · ${stopsCount} ${t.transfer}` : '';
  const seg1 = mode === 'flightFirst' ?
  { icon: '✈', color: 'var(--blue)', bg: 'var(--blue-s)', label: t.flightLeg, main: flightAirline, sub: `${flightFirstDep}→${flightLastArr} · ${d.flight.toCity ?? ''}${stopsSuffix}` } :
  { icon: isBus ? '🚌' : '🚆', color: gColor, bg: gBg, label: isBus ? t.busLeg : t.trainLeg, main: d.ground.company, sub: `${d.ground.dep}→${d.ground.arr} · ${d.ground.from}` };
  const seg2 = mode === 'flightFirst' ?
  { icon: isBus ? '🚌' : '🚆', color: gColor, bg: gBg, label: isBus ? t.busLeg : t.trainLeg, main: d.ground.company, sub: `${d.ground.dep}→${d.ground.arr}${d.ground.nextDay ? ' (+1)' : ''}` } :
  { icon: '✈', color: 'var(--blue)', bg: 'var(--blue-s)', label: t.flightLeg, main: flightAirline, sub: `${flightFirstDep}→${flightLastArr} · ${d.flight.fromIata ?? d.via}${stopsSuffix}` };

  const header =
  <>
      <div className="combo-seg" style={{ flex: 1 }}>
        <div className="combo-mode" style={{ background: seg1.bg, color: seg1.color }}>{seg1.icon}</div>
        <div className="combo-seg-info"><div className="combo-seg-label">{seg1.label}</div><div className="combo-seg-main">{seg1.main}</div><div className="combo-seg-sub">{seg1.sub}</div></div>
      </div>
      <div className="combo-arrow">→</div>
      <div className="combo-seg" style={{ flex: 1 }}>
        <div className="combo-mode" style={{ background: seg2.bg, color: seg2.color }}>{seg2.icon}</div>
        <div className="combo-seg-info"><div className="combo-seg-label">{seg2.label}</div><div className="combo-seg-main">{seg2.main}</div><div className="combo-seg-sub">{seg2.sub}</div></div>
      </div>
      <div className="combo-total"><div className="combo-total-price">{fmt(d.total)}</div><div className="combo-total-sub">{t.total}</div></div>
    </>;

  return <CardDetailShell header={header} mapRoute={{ segments: mapSegs }} lang={lang} journey={journey} detailSegs={detailSegs} total={d.total} currency={currency} flightPackage={flightPackage} />;
}

// ── Ground card ───────────────────────────────────────────────────────────────
function GroundCard({ d, currency, lang }) {
  const t = T[lang];
  const isBus = d.type === 'Bus';
  const fmt = (n) => currency === "USD" ? `$${n}` : `€${n}`;
  const route = { segments: [{ from: 'VCE', to: 'NUE', type: d.type.toLowerCase(), carrier: d.company, duration: d.duration }] };
  const journey = { nodes: [{ iata: 'VCE', dep: d.dep }, { iata: 'NUE', arr: d.arr }], segs: [{ type: d.type.toLowerCase(), duration: d.duration, carrier: d.company }] };
  const detailSegs = [{ type: d.type.toLowerCase(), from: 'Venice', to: 'Nürnberg', fromName: 'Venice', toName: 'Nürnberg',
    carrier: d.company, ref: '', dep: d.dep, arr: d.arr, duration: d.duration, price: d.price, buyUrl: 'https://www.omio.com' }];
  const header =
  <>
      <div className="ground-mode" style={{ background: isBus ? 'var(--or-s)' : 'var(--green-s)', color: isBus ? 'var(--or)' : 'var(--green)' }}>{isBus ? '🚌' : '🚆'}</div>
      <div className="ground-info" style={{ flex: 1 }}>
        <div className="ground-company">{d.company}</div>
        <div className="ground-via">via {d.via} · {d.transfers} {t.transfer}</div>
        <div className="ground-times">{d.dep}<span>→</span>{d.arr}</div>
      </div>
      <div className="ground-dur">{d.duration}</div>
      <div className="ground-price">{fmt(d.price)}</div>
    </>;

  return <CardDetailShell header={header} mapRoute={route} lang={lang} journey={journey} detailSegs={detailSegs} currency={currency} />;
}
