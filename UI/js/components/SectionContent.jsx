// ── SectionContent: dispatch one of 5 result lists by section index ─────────
// flashId for hub sections uses compound format: {prefix}-{hubIata}-{busId}-{flightId}
// e.g. "fpb-FRA-fb3-ff1" or "bpf-VRN-vb2-vf1"
function parseHubFlashId(flashId, prefix) {
  if (!flashId || !flashId.startsWith(prefix + '-')) return null;
  // Strip prefix: "FRA-fb3-ff1"
  const rest = flashId.slice(prefix.length + 1);
  const firstDash = rest.indexOf('-');
  if (firstDash === -1) return null;
  const hubIata = rest.slice(0, firstDash);
  const remainder = rest.slice(firstDash + 1); // "fb3-ff1"
  const lastDash = remainder.lastIndexOf('-');
  if (lastDash === -1) return null;
  const busId = remainder.slice(0, lastDash);
  const flightId = remainder.slice(lastDash + 1);
  return { hubIata, busId, flightId };
}

function SectionContent({ secIdx, data, currency, lang, date, flashId, arrColorMap, depColorMap }) {
  const t = T[lang];
  if (!data || data.length === 0) {
    if (secIdx === 3) return (
      <div className="no-result no-result-rich">
        <div className="no-icon">🗺</div>
        <div className="no-result-title">{t.noResultBusFlightTitle}</div>
        <div className="no-result-hint">{t.noResultBusFlightHint}</div>
      </div>
    );
    return <div className="no-result"><div className="no-icon">🔍</div>{t.noResult}</div>;
  }

  if (secIdx === 0 || secIdx === 1) {
    const prefix = secIdx === 0 ? 'bf' : 'cf';
    return <div className="cards-list">{data.map((d, i) => {
      const depKey = d.dep || d.legs?.[0]?.dep || '';
      const id = `${prefix}-${d.flightNo || d.airline}-${depKey}`;
      const flash = id === flashId;
      const arrColor = arrColorMap ? arrColorMap[d.arrIata] : null;
      const depColor = depColorMap ? depColorMap[d.depIata] : null;
      return d.stops > 0 ?
        <TransferCard key={i} d={d} currency={currency} lang={lang} date={date} cardId={id} flash={flash} arrColor={arrColor} depColor={depColor} /> :
        <FlightCard key={i} d={d} currency={currency} lang={lang} date={date} cardId={id} flash={flash} arrColor={arrColor} depColor={depColor} />;
    })}</div>;
  }

  if (secIdx === 2 || secIdx === 3) {
    const prefix = secIdx === 2 ? 'fpb' : 'bpf';
    const parsed = parseHubFlashId(flashId, prefix);
    return <div className="cards-list">{data.map((h, i) => {
      const isTarget = parsed != null && h.hub?.iata === parsed.hubIata;
      return (
        <HubMasterCard
          key={`${h.hub?.iata || i}-${i}`}
          hubData={h}
          currency={currency}
          lang={lang}
          defaultExpanded={i === 0}
          date={date}
          preSelectBusId={isTarget ? parsed.busId : null}
          preSelectFlightId={isTarget ? parsed.flightId : null}
          autoExpand={isTarget}
        />
      );
    })}</div>;
  }

  if (secIdx === 4) {
    return <div className="cards-list">{data.map((p, i) => (
      <BusFlightBusCard
        key={`${p.originHub?.iata || ''}-${p.destHub?.iata || ''}-${i}`}
        pair={p}
        currency={currency}
        lang={lang}
        defaultExpanded={i === 0}
      />
    ))}</div>;
  }

  if (secIdx === 5) {
    const sorted = [...data].sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
    return <div className="cards-list">{sorted.map((d, i) => {
      const id = `bg-${d.company}-${d.dep}`;
      return <GroundCard key={i} d={d} currency={currency} lang={lang} cardId={id} flash={id === flashId} />;
    })}</div>;
  }
}
