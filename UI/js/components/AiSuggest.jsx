// ── AI Suggestion panel ───────────────────────────────────────────────────────
// Builds a rich flat catalog (individual hub combos, not just hub summaries)
// and calls the Flask /api/ai-suggest endpoint which proxies xAI structured output.
function AiSuggestPanel({ results, currency, lang, initialCache, onResult, onPick, onClose }) {
  const [loading, setLoading] = useState(!initialCache);
  const [picks, setPicks] = useState(initialCache?.picks ?? null);
  const [error, setError] = useState(null);
  const [summary, setSummary] = useState(initialCache?.summary ?? '');

  const fmt = (n) => formatPrice(n, currency);

  useEffect(() => { if (!initialCache) ask(); }, []);

  // For each hub, enumerate all valid bus×flight combos (connection >= 120 min).
  // Returns top N sorted by price so the context stays manageable.
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
    // Always guarantee cheapest AND fastest are in the catalog, then fill with next-cheapest
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

    (results.bestFlights || []).forEach((d) => flat.push({
      cat: 'Best Flight', secIdx: 0,
      id: `bf-${d.flightNo || d.airline}-${d.dep}`,
      airline: d.airline, dep: d.dep, arr: d.arr, dur: d.duration,
      price: d.price, stops: d.stops || 0,
    }));
    (results.cheapFlights || []).forEach((d) => flat.push({
      cat: 'Cheapest Flight', secIdx: 1,
      id: `cf-${d.flightNo || d.airline}-${d.dep}`,
      airline: d.airline, dep: d.dep, arr: d.arr, dur: d.duration,
      price: d.price, stops: d.stops || 0,
    }));

    // Hub combos — full cross-product per hub (filtered & capped per hub)
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
      const enriched = (data.picks || []).map((p) => {
        const item = flat.find((f) => f.id === p.id);
        return item ? { ...p, item } : null;
      }).filter(Boolean);
      if (enriched.length === 0) throw new Error('no valid picks');
      setPicks(enriched);
      setSummary(data.summary || '');
      onResult?.(enriched, data.summary || '');
    } catch (e) {
      // Deterministic fallback: cheapest + fastest without AI
      const scored = flat
        .filter((f) => (f.price != null || f.totalPrice != null))
        .map((f) => ({ ...f, _p: f.totalPrice ?? f.price ?? Infinity, _h: f.totalTripH ?? 99 }));
      const byPrice = [...scored].sort((a, b) => a._p - b._p);
      const byDur   = [...scored].sort((a, b) => a._h - b._h);
      const fallbackPicks = [];
      const seen = new Set();
      for (const [kind, item] of [['cheap', byPrice[0]], ['fast', byDur[0]]]) {
        if (item && !seen.has(item.id)) {
          seen.add(item.id);
          fallbackPicks.push({
            kind, id: item.id,
            reason: kind === 'cheap'
              ? (lang === 'tr' ? 'En düşük toplam fiyat' : 'Lowest total price')
              : (lang === 'tr' ? 'En kısa yolculuk süresi' : 'Shortest trip time'),
            item,
          });
        }
      }
      if (fallbackPicks.length === 0) {
        setError(lang === 'tr'
          ? 'AI önerisi alınamadı, manuel seçim yapabilirsiniz.'
          : 'Could not get AI picks. Please choose manually.');
        setLoading(false);
        return;
      }
      setPicks(fallbackPicks);
      setSummary('');
      onResult?.(fallbackPicks, '');
    } finally {
      setLoading(false);
    }
  }

  const tagLabel = (kind) => {
    if (lang === 'tr') return kind === 'cheap' ? '💰 En Ucuz' : kind === 'fast' ? '⚡ En Hızlı' : '⭐ En İyi';
    return kind === 'cheap' ? '💰 Cheapest' : kind === 'fast' ? '⚡ Fastest' : '⭐ Best overall';
  };

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
        {picks.map((p) => {
          const it = p.item;
          const price = priceOf(it);
          return (
            <button key={p.kind} className={`ai-suggest-card ${p.kind}`}
              onClick={() => onPick(it.secIdx, it.id)}>
              <div className="ai-suggest-card-tag">{tagLabel(p.kind)}</div>
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
