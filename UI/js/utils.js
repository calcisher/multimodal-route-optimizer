// ── Geo helpers ───────────────────────────────────────────────────────────────
function arcLatLng(a, b, steps = 48) {
  const midLat = (a[0] + b[0]) / 2, midLng = (a[1] + b[1]) / 2;
  const dLat = b[0] - a[0], dLng = b[1] - a[1];
  const dist = Math.sqrt(dLat * dLat + dLng * dLng);
  const ctrl = [midLat + dist * 0.18, midLng];
  const out = [];
  for (let i = 0; i <= steps; i++) {
    const tt = i / steps;
    const lat = (1 - tt) * (1 - tt) * a[0] + 2 * (1 - tt) * tt * ctrl[0] + tt * tt * b[0];
    const lng = (1 - tt) * (1 - tt) * a[1] + 2 * (1 - tt) * tt * ctrl[1] + tt * tt * b[1];
    out.push([lat, lng]);
  }
  return out;
}

// ── Layover helper ────────────────────────────────────────────────────────────
function calcLayover(prev, next) {
  if (!prev?.arr || !next?.dep) return null;
  const [ph, pm] = prev.arr.split(':').map(Number);
  const [nh, nm] = next.dep.split(':').map(Number);
  if ([ph, pm, nh, nm].some(Number.isNaN)) return null;
  let mins = nh * 60 + nm - (ph * 60 + pm);
  if (mins < 0) mins += 24 * 60;
  const h = Math.floor(mins / 60), m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ── Airport text utils ────────────────────────────────────────────────────────
function _norm(s) {
  return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').trim();
}

// Build dropdown suggestions from the airport list. Matches IATA exact (top),
// then city startsWith, city contains, airport-name contains. Multi-airport
// cities are grouped under a single "city" pseudo-entry that picks the
// preferred airport server-side via resolve_iata's _PREFERRED_IATA map.
function buildAirportSuggestions(airports, query, max = 8) {
  if (!Array.isArray(airports) || airports.length === 0) return [];
  const q = _norm(query);
  if (!q) return [];

  // Group by normalized city.
  const cityMap = new Map();
  for (const a of airports) {
    if (!a.iata || !a.city) continue;
    const key = _norm(a.city);
    if (!cityMap.has(key)) cityMap.set(key, { city: a.city, country: a.country || '', airports: [] });
    cityMap.get(key).airports.push(a);
  }

  // Score one entry per city. Multi-airport cities use a "city-group" entry
  // that expands into a city pseudo + its airports as nested children.
  // Single-airport cities use a flat airport entry.
  const scored = [];
  for (const [key, group] of cityMap) {
    if (group.airports.length >= 2) {
      let s = Infinity;
      if (key === q) s = 0;
      else if (key.startsWith(q)) s = 1;
      else if (key.includes(q)) s = 3;
      // An IATA match on any child should still surface the whole city group.
      for (const a of group.airports) {
        const ai = _norm(a.iata);
        const an = _norm(a.name);
        if (ai === q) s = Math.min(s, 0.5);
        else if (ai.startsWith(q)) s = Math.min(s, 2);
        else if (an.includes(q)) s = Math.min(s, 4.5);
      }
      if (s < Infinity) scored.push({
        score: s, kind: 'group', city: group.city, country: group.country, airports: group.airports,
      });
    } else {
      const a = group.airports[0];
      const iata = _norm(a.iata);
      const name = _norm(a.name);
      let s = Infinity;
      if (iata === q) s = 0;
      else if (key === q) s = 1;
      else if (key.startsWith(q)) s = 2;
      else if (iata.startsWith(q)) s = 2.5;
      else if (key.includes(q)) s = 4;
      else if (name.includes(q)) s = 5;
      if (s < Infinity) scored.push({ score: s, kind: 'single', a });
    }
  }

  scored.sort((a, b) => a.score - b.score);

  // Flatten into render entries. Each city-group emits a `city` entry followed
  // by `airport` entries marked `child:true` so the row renders indented.
  const out = [];
  for (const s of scored) {
    if (s.kind === 'group') {
      out.push({ type: 'city', city: s.city, country: s.country, airports: s.airports });
      for (const a of s.airports) out.push({ type: 'airport', a, child: true });
    } else {
      out.push({ type: 'airport', a: s.a });
    }
    if (out.length >= max) break;
  }
  return out.slice(0, max);
}

// ── Filter utils ──────────────────────────────────────────────────────────────
function isFilterActive(f) {
  return f.maxDurH < 24 || f.depFromH > 0 || f.depToH < 24 ||
    f.arrFromH > 0 || f.arrToH < 24 || f.maxTransfers !== -1 || f.excludeOvernight;
}

function activeFilterCount(f) {
  let n = 0;
  if (f.maxDurH < 24) n++;
  if (f.depFromH > 0 || f.depToH < 24) n++;
  if (f.arrFromH > 0 || f.arrToH < 24) n++;
  if (f.maxTransfers !== -1) n++;
  if (f.excludeOvernight) n++;
  return n;
}

function parseDur(s) {
  if (!s) return 0;
  const str = String(s);
  const h = str.match(/(\d+)\s*h/i);
  const m = str.match(/(\d+)\s*m/i);
  return (h ? +h[1] * 60 : 0) + (m ? +m[1] : 0);
}

function timeToHour(value) {
  if (!value) return null;
  if (typeof value === 'string' && value.includes('T')) {
    const d = parseISO(value);
    return d ? d.getHours() + d.getMinutes() / 60 : null;
  }
  const m = String(value).match(/^(\d{1,2}):(\d{2})/);
  if (!m) return null;
  return (+m[1]) + (+m[2]) / 60;
}

function durationOfItem(item) {
  if (!item) return 0;
  if (typeof item.durationMin === 'number') return item.durationMin;
  return parseDur(item.totalDuration || item.duration);
}

function itemNextDay(item) {
  if (!item) return false;
  if (item.nextDay) return true;
  if (item.depDate && item.arrDate && item.arrDate > item.depDate) return true;
  const dep = parseISO(item.depISO), arr = parseISO(item.arrISO);
  return !!(dep && arr && arr.toDateString() !== dep.toDateString());
}

function passesTripFilter(item, f) {
  if (!item) return false;
  if (f.excludeOvernight && itemNextDay(item)) return false;
  const dur = durationOfItem(item);
  if (f.maxDurH < 24 && dur > f.maxDurH * 60) return false;
  const depH = timeToHour(item.depISO || item.dep);
  if (depH != null && (depH < f.depFromH || depH > f.depToH)) return false;
  const arrH = timeToHour(item.arrISO || item.arr);
  if (arrH != null && (arrH < f.arrFromH || arrH > f.arrToH)) return false;
  return true;
}

function flightPasses(d, f) {
  const stops = typeof d.stops === 'number' ? d.stops : Array.isArray(d.legs) ? Math.max(0, d.legs.length - 1) : 0;
  if (f.maxTransfers !== -1 && stops > f.maxTransfers) return false;
  if (Array.isArray(d.legs) && d.legs.length) {
    const first = d.legs[0], last = d.legs[d.legs.length - 1];
    return passesTripFilter({
      dep: first.dep,
      arr: last.arr,
      depISO: d.depISO,
      arrISO: d.arrISO,
      duration: d.totalDuration || d.duration,
      durationMin: d.durationMin,
      nextDay: d.nextDay
    }, f);
  }
  return passesTripFilter(d, f);
}

function groundPasses(d, f) {
  if (f.maxTransfers !== -1 && (d.transfers || 0) > f.maxTransfers) return false;
  return passesTripFilter(d, f);
}

function minHubTotal(busOptions, flightOptions, mode) {
  let min = null;
  for (const b of busOptions) {
    for (const fl of flightOptions) {
      const c = calcConnection(b, fl, mode);
      if (c.minutes == null || c.minutes < PICK_MIN_CONNECTION) continue;
      if (b.price == null || fl.price == null) continue;
      const total = b.price + fl.price;
      if (min == null || total < min) min = total;
    }
  }
  return min;
}

function filterHubData(hubData, f) {
  const busOptions = (hubData.busOptions || []).filter((b) => groundPasses(b, f));
  const flightOptions = (hubData.flightOptions || []).filter((fl) => flightPasses(fl, f));
  if (!busOptions.length || !flightOptions.length) return null;
  const minTotal = minHubTotal(busOptions, flightOptions, hubData.mode);
  return { ...hubData, busOptions, flightOptions, minTotal };
}

function countHubOptions(hubs) {
  return (hubs || []).reduce((sum, h) => sum + (h.busOptions?.length || 0) + (h.flightOptions?.length || 0), 0);
}

function applyFilters(results, f) {
  const empty = { filtered: null, removed: [0, 0, 0, 0, 0] };
  if (!results) return empty;
  if (!isFilterActive(f)) return { filtered: results, removed: [0, 0, 0, 0, 0] };

  const bestFlights = (results.bestFlights || []).filter((d) => flightPasses(d, f));
  const cheapFlights = (results.cheapFlights || []).filter((d) => flightPasses(d, f));
  const flightPlusBus = (results.flightPlusBus || []).map((h) => filterHubData(h, f)).filter(Boolean);
  const busPlusFlight = (results.busPlusFlight || []).map((h) => filterHubData(h, f)).filter(Boolean);
  const busOrTrain = (results.busOrTrain || []).filter((d) => groundPasses(d, f));

  return {
    filtered: { ...results, bestFlights, cheapFlights, flightPlusBus, busPlusFlight, busOrTrain },
    removed: [
      (results.bestFlights || []).length - bestFlights.length,
      (results.cheapFlights || []).length - cheapFlights.length,
      countHubOptions(results.flightPlusBus) - countHubOptions(flightPlusBus),
      countHubOptions(results.busPlusFlight) - countHubOptions(busPlusFlight),
      (results.busOrTrain || []).length - busOrTrain.length
    ]
  };
}

function fmtFilterHour(h) {
  const hh = Math.floor(h);
  const mm = Math.round((h - hh) * 60);
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

// ── Connection / hub picker utils ────────────────────────────────────────────
function parseISO(s) {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function fmtConnMinutes(mins) {
  if (mins == null) return '—';
  const sign = mins < 0 ? '-' : '';
  const abs = Math.abs(mins);
  const h = Math.floor(abs / 60), m = abs % 60;
  return sign + (h > 0 ? `${h}s ${String(m).padStart(2, '0')}dk` : `${m}dk`);
}

// Map a calcConnection level → palette + i18n labels for the new summary panel
// and the per-row wait line. Uses CSS color tokens so themes propagate.
function connStyleFor(level, t) {
  switch (level) {
    case 'green':  return { color: 'var(--green)',  bg: 'var(--green-s)',  label: t.connComfort, desc: t.connComfortDesc };
    case 'yellow': return { color: 'var(--yellow)', bg: 'var(--yellow-s)', label: t.connTight,   desc: t.connTightDesc };
    case 'gray':   return { color: 'var(--slate)',  bg: 'var(--slate-s)',  label: t.connLong,    desc: t.connLongDesc };
    default:       return { color: 'var(--red)',    bg: 'var(--red-s)',    label: t.connRisk,    desc: t.connRiskDesc };
  }
}

// Convert "2026-05-05" → "260505" for the Skyscanner URL shape.
function ymdToYYMMDD(date) {
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return '';
  return date.slice(2, 4) + date.slice(5, 7) + date.slice(8, 10);
}

// Build a pre-filtered Skyscanner URL. Lives in the UI layer so the backend
// stays free of presentation concerns; if Skyscanner changes their URL shape,
// this is the single edit point.
//   filters applied (each one optional — drops cleanly if data missing):
//     departure-times → ±30 min window around depTime
//     stops           → exact stop count via Skyscanner's exclusion params
//     duration        → max trip duration in minutes
function buildSkyscannerUrl({ fromIata, toIata, date, depTime, stops, durationMin, currency }) {
  const fallback = 'https://www.skyscanner.com.tr/';
  if (!fromIata || !toIata) return fallback;
  const yymmdd = ymdToYYMMDD(date);
  if (!yymmdd) return fallback;
  const dep = String(fromIata).toLowerCase();
  const arr = String(toIata).toLowerCase();
  const cur = currency || 'EUR';
  const fixed = 'adultsv2=1&cabinclass=economy&childrenv2=&ref=home&rtn=0&outboundaltsenabled=false&inboundaltsenabled=false';
  const parts = [`${fixed}&currency=${cur}`];
  if (Number.isFinite(durationMin)) parts.push(`duration=${Math.round(durationMin)}`);
  if (typeof depTime === 'string' && /^\d{1,2}:\d{2}/.test(depTime)) {
    const [h, m] = depTime.slice(0, 5).split(':').map(Number);
    if (Number.isFinite(h) && Number.isFinite(m)) {
      const tMin = h * 60 + m;
      parts.push(`departure-times=${Math.max(0, tMin - 30)}-${Math.min(1440, tMin + 30)}`);
    }
  }
  if (Number.isFinite(stops)) {
    if (stops === 0)      parts.push('stops=!oneStop,!twoPlusStops');
    else if (stops === 1) parts.push('stops=!direct,!twoPlusStops');
    else                  parts.push('stops=!direct,!oneStop');
  }
  return `https://www.skyscanner.com.tr/tasima/ucak-bileti/${dep}/${arr}/${yymmdd}/?${parts.join('&')}`;
}

// Total trip duration (first leg dep → second leg arr) in minutes.
function totalTripMin(selBus, selFlight, mode) {
  if (!selBus || !selFlight) return null;
  const startISO = mode === 'bus_plus_flight' ? selBus.depISO : selFlight.depISO;
  const endISO   = mode === 'bus_plus_flight' ? selFlight.arrISO : selBus.arrISO;
  const a = parseISO(startISO), b = parseISO(endISO);
  if (!a || !b) return null;
  return Math.round((b.getTime() - a.getTime()) / 60000);
}

function calcConnection(bus, flight, mode) {
  // mode='bus_plus_flight': bus arrives, then flight departs
  // mode='flight_plus_bus': flight arrives, then bus departs
  if (!bus || !flight) return { minutes: null, level: 'red', valid: false };
  const fromISO = mode === 'bus_plus_flight' ? bus.arrISO : flight.arrISO;
  const toISO = mode === 'bus_plus_flight' ? flight.depISO : bus.depISO;
  const a = parseISO(fromISO), b = parseISO(toISO);
  if (!a || !b) return { minutes: null, level: 'red', valid: false };
  const minutes = Math.round((b.getTime() - a.getTime()) / 60000);
  let level = 'red';
  if (minutes < 0) level = 'red';
  else if (minutes >= CONN_LONG_MIN) level = 'gray';
  else if (minutes >= CONN_GREEN_MIN) level = 'green';
  else if (minutes >= CONN_RED_MIN) level = 'yellow';
  return { minutes, level, valid: minutes >= 0 };
}

function pickCheapest(buses, flights, mode) {
  let best = null;
  for (const b of buses) for (const f of flights) {
    if (b.price == null || f.price == null) continue;
    const c = calcConnection(b, f, mode);
    if (c.minutes == null || c.minutes < PICK_MIN_CONNECTION) continue;
    const total = b.price + f.price;
    if (!best || total < best.total) best = { busId: b.id, flightId: f.id, total, conn: c };
  }
  return best;
}

function pickFastest(buses, flights, mode) {
  let best = null;
  for (const b of buses) for (const f of flights) {
    const c = calcConnection(b, f, mode);
    if (c.minutes == null || c.minutes < PICK_MIN_CONNECTION) continue;
    const bd = b.durationMin ?? 0, fd = f.durationMin ?? 0;
    const total = bd + c.minutes + fd;
    if (!best || total < best.total) best = { busId: b.id, flightId: f.id, total, conn: c };
  }
  return best;
}

function hourFromISO(iso) {
  const d = parseISO(iso);
  return d ? d.getHours() : null;
}

function pickEarliest(buses, flights, mode) {
  // Earliest "first leg" departure between 05:00 and 11:59, paired with first valid second leg.
  const firstPool = mode === 'bus_plus_flight' ? buses : flights;
  const secondPool = mode === 'bus_plus_flight' ? flights : buses;
  const sortedFirst = [...firstPool].sort((a, b) => {
    const ha = parseISO(a.depISO)?.getTime() ?? Infinity;
    const hb = parseISO(b.depISO)?.getTime() ?? Infinity;
    return ha - hb;
  });
  for (const first of sortedFirst) {
    const h = hourFromISO(first.depISO);
    if (h == null || h < 5 || h >= 12) continue;
    for (const second of secondPool) {
      const [bus, flight] = mode === 'bus_plus_flight' ? [first, second] : [second, first];
      const c = calcConnection(bus, flight, mode);
      if (c.minutes == null || c.minutes < PICK_MIN_CONNECTION) continue;
      return { busId: bus.id, flightId: flight.id, conn: c };
    }
  }
  return null;
}

// ── Sort utils ────────────────────────────────────────────────────────────────
function sortByDep(options) {
  return [...options].sort((a, b) => {
    const ta = parseISO(a.depISO)?.getTime() ?? Infinity;
    const tb = parseISO(b.depISO)?.getTime() ?? Infinity;
    return ta - tb;
  });
}

// Sort options by ascending price (missing prices sink to bottom).
function sortByPrice(options) {
  return [...options].sort((a, b) => {
    const pa = a.price ?? Infinity;
    const pb = b.price ?? Infinity;
    return pa - pb;
  });
}

// Slice the sorted list according to stage (0=collapsed, 1=mid, 2=all). The selected
// option always stays visible — if it falls outside the slice we swap it into the
// last visible slot so the user never loses their pick when the list collapses.
function visibleOptions(sortedOpts, selectedId, limit, totalLen) {
  if (limit >= sortedOpts.length) return sortedOpts;
  const top = sortedOpts.slice(0, limit);
  if (!selectedId || top.some((o) => o.id === selectedId)) return top;
  const sel = sortedOpts.find((o) => o.id === selectedId);
  return sel ? [sel, ...top.slice(0, limit - 1)] : top;
}

// ── Hub journey builders ──────────────────────────────────────────────────────
function buildHubJourney(hubData, bus, flight, lang) {
  const mode = hubData.mode;
  const hub = hubData.hub;
  const flightLegs = Array.isArray(flight.legs) && flight.legs.length > 0 ? flight.legs : [{
    dep: flight.dep, arr: flight.arr,
    from: flight.fromIata, to: flight.toIata,
    fromName: '', toName: '',
    duration: flight.duration, flightNo: flight.flightNo, airline: flight.airline,
  }];
  const flightFirstDep = flightLegs[0].dep;
  const flightLastArr = flightLegs[flightLegs.length - 1].arr;
  const flightFirstFrom = flightLegs[0].from;
  const flightLastTo = flightLegs[flightLegs.length - 1].to;
  const layoverNodes = flightLegs.slice(1).map((l, i) => {
    const lay = (flight.layovers || [])[i] || {};
    return {
      iata: l.from,
      name: lay.city || CITY_NAMES[l.from] || l.from,
      arr: flightLegs[i].arr,
      dep: l.dep,
      layover: lay.duration || calcLayover({ arr: flightLegs[i].arr }, { dep: l.dep })
    };
  });
  const flightSegs = flightLegs.map((l) => ({
    type: 'flight', duration: l.duration,
    carrier: l.airline || flight.airline, ref: l.flightNo
  }));
  const groundSeg = { type: bus.type?.toLowerCase() || 'bus', duration: bus.duration, carrier: bus.company || 'FlixBus' };

  let nodes, segs;
  if (mode === 'bus_plus_flight') {
    // depCity (bus dep) → hub (bus arr / flight dep) → [layovers] → arrIata (flight arr)
    const viaLayover = calcLayover({ arr: bus.arr }, { dep: flightFirstDep });
    nodes = [
      { iata: hubData.depIata, name: bus.from, dep: bus.dep },
      { iata: flightFirstFrom, name: hub.city, arr: bus.arr, dep: flightFirstDep, layover: viaLayover },
      ...layoverNodes,
      { iata: flightLastTo, arr: flightLastArr },
    ];
    segs = [groundSeg, ...flightSegs];
  } else {
    // flight_plus_bus: depIata (flight dep) → [layovers] → hub (flight arr / bus dep) → arrCity (bus arr)
    const viaLayover = calcLayover({ arr: flightLastArr }, { dep: bus.dep });
    nodes = [
      { iata: flightFirstFrom, dep: flightFirstDep },
      ...layoverNodes,
      { iata: flightLastTo, name: hub.city, arr: flightLastArr, dep: bus.dep, layover: viaLayover },
      { iata: hubData.arrIata, name: bus.to, arr: bus.arr, arrNextDay: !!bus.nextDay },
    ];
    segs = [...flightSegs, groundSeg];
  }
  return { nodes, segs };
}

function buildHubDetailSegs(hubData, bus, flight) {
  const mode = hubData.mode;
  const flightLegs = Array.isArray(flight.legs) && flight.legs.length > 0 ? flight.legs : [{
    dep: flight.dep, arr: flight.arr,
    from: flight.fromIata, to: flight.toIata,
    fromName: '', toName: '',
    duration: flight.duration, flightNo: flight.flightNo, airline: flight.airline,
  }];
  const flightFirstFrom = flightLegs[0].from;
  const flightLastTo = flightLegs[flightLegs.length - 1].to;
  const flightDetail = flightLegs.map((l, i) => ({
    type: 'flight',
    from: l.from, to: l.to,
    fromName: l.fromName || CITY_NAMES[l.from] || l.from,
    toName: l.toName || CITY_NAMES[l.to] || l.to,
    carrier: l.airline || flight.airline,
    ref: l.flightNo,
    dep: l.dep, arr: l.arr, duration: l.duration,
    price: i === 0 ? flight.price : null,
    buyUrl: flight.link || `https://www.google.com/travel/flights?q=${flightFirstFrom}+to+${flightLastTo}`,
  }));
  const groundDetail = {
    type: bus.type?.toLowerCase() || 'bus', from: bus.from, to: bus.to,
    fromName: bus.from, toName: bus.to,
    carrier: bus.company || 'FlixBus', ref: '',
    dep: bus.dep, arr: bus.arr, nextDay: bus.nextDay,
    duration: bus.duration, price: bus.price,
    buyUrl: bus.url || (bus.type === 'Train' ? 'https://www.omio.com' : 'https://www.flixbus.com'),
  };
  return mode === 'bus_plus_flight' ? [groundDetail, ...flightDetail] : [...flightDetail, groundDetail];
}

function buildHubMapSegs(hubData, bus, flight) {
  const mode = hubData.mode;
  const flightLegs = Array.isArray(flight.legs) && flight.legs.length > 0 ? flight.legs : [{
    from: flight.fromIata, to: flight.toIata,
    duration: flight.duration, airline: flight.airline,
  }];
  const flightSegs = flightLegs.map((l) => ({
    from: l.from, to: l.to, type: 'flight',
    carrier: l.airline || flight.airline, duration: l.duration,
  }));
  const hubIata = hubData.hub.iata;
  const gType = bus.type?.toLowerCase() || 'bus';
  const groundSeg = mode === 'bus_plus_flight' ?
    { from: hubData.depIata, to: hubIata, type: gType, carrier: bus.company || 'FlixBus', duration: bus.duration } :
    { from: hubIata, to: hubData.arrIata, type: gType, carrier: bus.company || 'FlixBus', duration: bus.duration };
  return mode === 'bus_plus_flight' ? [groundSeg, ...flightSegs] : [...flightSegs, groundSeg];
}
