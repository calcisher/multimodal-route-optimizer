// ── AI Suggestion panel ───────────────────────────────────────────────────────
// Builds a flat catalog of every realistic option, then calls /api/ai-suggest
// which proxies xAI. The AI returns 3 honest picks with short reasons; clicking
// one jumps to that section and flashes the matching card.
function AiSuggestPanel({ results, currency, lang, initialCache, onResult, onPick, onClose }) {
  const [loading, setLoading] = useState(!initialCache);
  const [picks, setPicks] = useState(initialCache?.picks ?? null);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(initialCache?.summary ?? '');

  const fmt = (n) => formatPrice(n, currency);

  useEffect(() => { if (!initialCache) ask(); }, []);

  // For each hub, enumerate all valid bus×flight combos (connection >= 120 min)
  // and pass a small representative slice so the prompt stays manageable.
  function hubCombos(hubData, secIdx, prefix, maxPerHub = 8) {
    const { busOptions = [], flightOptions = [], hub, mode } = hubData;
    const combos = [];
    for (const bus of busOptions) {
      for (const flight of flightOptions) {
        const c = calcConnection(bus, flight, mode);
        if (c.minutes == null || c.minutes < PICK_MIN_CONNECTION) continue;
        const totalPrice = (bus.price ?? 0) + (flight.price ?? 0);
        const tripMin = totalTripMin(bus, flight, mode);
        combos.push({
          cat: secIdx === 2 ? 'Flight+Bus' : 'Bus+Flight',
          secIdx,
          id: `${prefix}-${hub.iata}-${bus.id}-${flight.id}`,
          hub: hub.city,
          flightAirline: flight.airline || '',
          flightDep: flight.dep || '',
          flightArr: flight.arr || '',
          flightPrice: flight.price,
          busDep: bus.dep || '',
          busArr: bus.arr || '',
          busPrice: bus.price,
          layoverH: c.minutes != null ? Math.round(c.minutes / 6) / 10 : null,
          totalTripH: tripMin != null ? Math.round(tripMin / 6) / 10 : null,
          totalPrice,
        });
      }
    }
    if (combos.length === 0) return [];
    const byPrice = [...combos].sort((a, b) => a.totalPrice - b.totalPrice);
    const byDur = [...combos].sort((a, b) => (a.totalTripH ?? 999) - (b.totalTripH ?? 999));
    const result = [];
    const seen = new Set();
    if (byPrice[0]) { seen.add(byPrice[0].id); result.push(byPrice[0]); }
    if (byDur[0] && !seen.has(byDur[0].id)) { seen.add(byDur[0].id); result.push(byDur[0]); }
    for (const c of byPrice) {
      if (result.length >= maxPerHub) break;
      if (!seen.has(c.id)) { seen.add(c.id); result.push(c); }
    }
    return result;
  }

  async function ask() {
    setLoading(true); setError(null); setPicks(null); setSummary('');

    const flat = [];

    // Multi-leg flights only have dep/arr inside legs[]; single-leg has them at top level.
    const flightDep = (d) => d.dep || d.legs?.[0]?.dep || '';
    const flightArr = (d) => d.arr || d.legs?.[d.legs.length - 1]?.arr || '';
    const flightDur = (d) => d.duration || d.totalDuration || '';

    (results.bestFlights || []).forEach((d) => flat.push({
      cat: 'Best Flight', secIdx: 0,
      id: `bf-${d.flightNo || d.airline}-${flightDep(d)}`,
      airline: d.airline, dep: flightDep(d), arr: flightArr(d), dur: flightDur(d),
      price: d.price, stops: d.stops || 0,
    }));
    (results.cheapFlights || []).forEach((d) => flat.push({
      cat: 'Cheapest Flight', secIdx: 1,
      id: `cf-${d.flightNo || d.airline}-${flightDep(d)}`,
      airline: d.airline, dep: flightDep(d), arr: flightArr(d), dur: flightDur(d),
      price: d.price, stops: d.stops || 0,
    }));

    (results.flightPlusBus || []).forEach((h) => {
      hubCombos(h, 2, 'fpb').forEach((c) => flat.push(c));
    });
    (results.busPlusFlight || []).forEach((h) => {
      hubCombos(h, 3, 'bpf').forEach((c) => flat.push(c));
    });

    (results.busOrTrain || []).forEach((d) => flat.push({
      cat: 'Bus/Train', secIdx: 4,
      id: `bg-${d.company}-${d.dep}`,
      company: d.company, dep: d.dep, arr: d.arr, dur: d.duration,
      price: d.price, transfers: d.transfers || 0,
    }));

    if (flat.length === 0) {
      setError(lang === 'tr'
        ? 'Henüz sonuç yok, arama tamamlanınca tekrar dene.'
        : 'No results yet — try again once the search completes.');
      setLoading(false);
      return;
    }

    try {
      const resp = await fetch('/api/ai-suggest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ catalog: flat, lang }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      // Defensive: strip wrapping brackets/quotes/whitespace from the model's id.
      const cleanId = (s) => (s || '').replace(/^[\s\[\]"`'<>]+|[\s\[\]"`'<>]+$/g, '');
      const enriched = (data.picks || []).map((p) => {
        const pid = cleanId(p.id);
        const item = flat.find((f) => f.id === pid);
        return item ? { id: pid, reason: p.reason, item } : null;
      }).filter(Boolean);
      if (enriched.length === 0) throw new Error('no valid picks');
      setPicks(enriched);
      setSummary(data.summary || '');
      onResult?.(enriched, data.summary || '');
    } catch (e) {
      // Fallback when AI is unreachable: just show the 3 cheapest options
      // with a neutral reason, no forced cheap/fast/best framing.
      const scored = flat
        .filter((f) => (f.price != null || f.totalPrice != null))
        .map((f) => ({ ...f, _p: f.totalPrice ?? f.price ?? Infinity }))
        .sort((a, b) => a._p - b._p);
      const fallback = scored.slice(0, 3).map((it) => ({
        id: it.id,
        reason: lang === 'tr' ? 'Listedeki en uygun fiyatlı seçenek.' : 'Among the lowest-priced options available.',
        item: it,
      }));
      if (fallback.length === 0) {
        setError(lang === 'tr'
          ? 'AI önerisi alınamadı, manuel seçim yapabilirsiniz.'
          : 'Could not get AI picks. Please choose manually.');
        setLoading(false);
        return;
      }
      setPicks(fallback);
      setSummary('');
      onResult?.(fallback, '');
    } finally {
      setLoading(false);
    }
  }

  const priceOf = (item) => item.totalPrice ?? item.price;

  const titleOf = (item) => {
    if (item.cat === 'Flight+Bus' || item.cat === 'Bus+Flight')
      return `${item.cat} via ${item.hub}`;
    if (item.cat === 'Bus/Train')
      return `${item.company} ${item.dep}→${item.arr}`;
    return `${item.airline} ${item.dep}→${item.arr}`;
  };

  if (loading) {
    return (
      <div className="ai-suggest-panel loading">
        <span className="ai-suggest-spin" />
        {lang === 'tr' ? 'AI seferleri inceliyor...' : 'AI is analyzing options...'}
      </div>
    );
  }

  if (error) {
    return (
      <div className="ai-suggest-panel">
        <button className="ai-suggest-close" onClick={onClose} aria-label="close">✕</button>
        <div className="ai-suggest-error">{error}</div>
      </div>
    );
  }

  if (!picks) return null;

  return (
    <div className="ai-suggest-panel">
      <button className="ai-suggest-close" onClick={onClose} aria-label="close">✕</button>
      <div className="ai-suggest-head">
        <span className="ai-suggest-title">✨ {lang === 'tr' ? 'AI Önerileri' : 'AI Picks'}</span>
      </div>
      {summary && <div className="ai-suggest-summary">{summary}</div>}
      <div className="ai-suggest-cards">
        {picks.map((p, i) => {
          const it = p.item;
          const price = priceOf(it);
          return (
            <button key={p.id || i} className="ai-suggest-card"
              onClick={() => onPick(it.secIdx, it.id)}>
              <div className="ai-suggest-card-title">{titleOf(it)}</div>
              <div className="ai-suggest-card-meta">{p.reason}</div>
              {price != null && <div className="ai-suggest-card-price">{fmt(price)}</div>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
