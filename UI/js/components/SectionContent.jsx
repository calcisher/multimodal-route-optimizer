// ── SectionContent: dispatch one of 5 result lists by section index ─────────
// ── Section content ───────────────────────────────────────────────────────────
function SectionContent({ secIdx, data, currency, lang, date }) {
  const t = T[lang];
  if (!data || data.length === 0) return <div className="no-result"><div className="no-icon">🔍</div>{t.noResult}</div>;
  if (secIdx === 0 || secIdx === 1) {
    return <div className="cards-list">{data.map((d, i) =>
      d.stops > 0 ?
      <TransferCard key={i} d={d} currency={currency} lang={lang} date={date} /> :
      <FlightCard key={i} d={d} currency={currency} lang={lang} date={date} />
      )}</div>;
  }
  if (secIdx === 2 || secIdx === 3) {
    return <div className="cards-list">{data.map((h, i) =>
      <HubMasterCard key={`${h.hub?.iata || i}-${i}`} hubData={h} currency={currency} lang={lang} defaultExpanded={i === 0} date={date} />
    )}</div>;
  }
  if (secIdx === 4) return <div className="cards-list">{data.map((d, i) => <GroundCard key={i} d={d} currency={currency} lang={lang} />)}</div>;
}
