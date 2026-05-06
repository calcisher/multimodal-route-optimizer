// ── Trip detail rendering: stop diagram + per-segment grid ───────────────────
// ── Journey Diagram ───────────────────────────────────────────────────────────
const SEG_COLOR = { flight: '#2563EB', bus: '#F4611E', train: '#16A34A' };

function JourneyDiagram({ nodes, segs, lang }) {
  const t = T[lang] || T.tr;
  return (
    <div className="jd-wrap">
      {nodes.map((node, i) => {
        const isOrigin = i === 0;
        const isDest = i === nodes.length - 1;
        return (
          <React.Fragment key={i}>
            <div className="jd-node">
              <div className="jd-dot-ring" style={{ background: isOrigin ? '#FEE4D8' : isDest ? '#DBEAFE' : '#F3F4F6' }}>
                <div className="jd-dot" style={{ background: isOrigin ? '#F4611E' : '#2563EB' }} />
              </div>
              <div className="jd-city">{node.name || CITY_NAMES[node.iata] || node.iata}</div>
              <div className="jd-iata">{node.iata}</div>
              {(node.arr || node.dep) &&
              <div className="jd-node-times">
                  {node.arr &&
                <span className="jd-tm-arr">
                      {node.arr}
                      {node.arrNextDay && <span className="jd-tm-nd">+1</span>}
                    </span>
                }
                  {node.arr && node.dep && <span className="jd-tm-sep">→</span>}
                  {node.dep && <span className="jd-tm-dep">{node.dep}</span>}
                </div>
              }
              {node.layover &&
              <div className="jd-node-layover">⏱ {node.layover} {t.layover}</div>
              }
            </div>
            {i < segs.length &&
            <div className="jd-conn">
                <div className="jd-bar">
                  <div className="jd-line-thick" style={{
                  background: SEG_COLOR[segs[i].type],
                  backgroundImage: segs[i].type === 'bus' ? `repeating-linear-gradient(90deg,${SEG_COLOR.bus} 0,${SEG_COLOR.bus} 10px,transparent 10px,transparent 16px)` : 'none'
                }} />
                  <div className="jd-arrowhead" style={{ borderLeft: `10px solid ${SEG_COLOR[segs[i].type]}` }} />
                </div>
                <div className="jd-seg-carrier">
                  {segs[i].carrier || (segs[i].type === 'flight' ? t.flightLeg : segs[i].type === 'bus' ? t.busLeg : t.trainLeg)}
                  {segs[i].ref ? ` · ${segs[i].ref}` : ''}
                </div>
                <div className="jd-dur">{segs[i].duration}</div>
              </div>
            }
          </React.Fragment>);

      })}
    </div>);

}

// ── Detail List (segment cards) ───────────────────────────────────────────────
const MODE_TINT = { flight: 'var(--blue-s)', bus: 'var(--or-s)', train: 'var(--green-s)' };


function DetailGrid({ segs, currency, lang, flightPackage }) {
  const fmt = (n) => currency === 'USD' ? `$${n}` : `€${n}`;
  const t = T[lang] || T.tr;
  const skyCopy = lang === 'tr' ? "Skyscanner'da Ara" : 'Search on Skyscanner';
  return (
    <div className="dg2-wrap">
      {segs.map((seg, i) => {
        const isFlight = seg.type === 'flight';
        const prev = i > 0 ? segs[i - 1] : null;
        const next = i < segs.length - 1 ? segs[i + 1] : null;
        const isFlightGroupStart = isFlight && (i === 0 || prev.type !== 'flight');
        const isFlightGroupEnd = isFlight && (i === segs.length - 1 || next.type !== 'flight');
        const color = SEG_COLOR[seg.type];
        const modeLabel = (isFlight ? t.flightLeg : seg.type === 'bus' ? t.busLeg : t.trainLeg).toUpperCase();
        const icon = isFlight ? '✈' : seg.type === 'bus' ? '🚌' : '🚆';
        const fromName = seg.fromName && seg.fromName !== seg.from ? seg.fromName : null;
        const toName = seg.toName && seg.toName !== seg.to ? seg.toName : null;
        const isFollowUpFlightInPkg = isFlight && flightPackage && flightPackage.legCount > 1 && !isFlightGroupStart;
        const showLayover = i > 0;
        return (
          <React.Fragment key={i}>
            {showLayover && (() => {
              const dur = calcLayover(prev, seg);
              const iata = prev.to && prev.to.length === 3 && prev.to === prev.to.toUpperCase() ? prev.to : null;
              const cityName = prev.toName || (iata && CITY_NAMES[iata]) || prev.to;
              const where = iata && cityName && cityName !== iata ? `${cityName} (${iata})` : cityName || iata;
              return (
                <div className="dg2-layover">
                  <span className="dg2-layover-pill">
                    ⏱ {dur ? `${dur} ${t.layover}` : t.layover}{where ? ` · ${where}` : ''}
                  </span>
                </div>);
            })()}

            {isFlightGroupStart && flightPackage && flightPackage.legCount > 1 &&
              <div className="dg2-pkg" style={{ borderColor: SEG_COLOR.flight, background: MODE_TINT.flight }}>
                <div className="dg2-pkg-left">
                  <div className="dg2-pkg-tag" style={{ color: SEG_COLOR.flight }}>
                    <span className="dg2-pkg-icon">✈</span>{t.flightPackage.toUpperCase()}
                  </div>
                  <div className="dg2-pkg-meta">{flightPackage.airline} · {flightPackage.legCount} {t.flightPackageLegs}</div>
                </div>
                <div className="dg2-pkg-right">
                  <div className="dg2-pkg-price" style={{ color: SEG_COLOR.flight }}>{fmt(flightPackage.price)}</div>
                  <div className="dg2-pkg-sub">{t.flightPackageTotal}</div>
                </div>
              </div>
            }

            <div className={`dg3-leg ${seg.type}`} style={{ borderLeftColor: color }}>
              <div className="dg3-leg-id">
                <div className="dg3-leg-tag" style={{ color }}>
                  <span className="dg3-leg-tag-icon">{icon}</span>
                  <span>{modeLabel}</span>
                </div>
                <div className="dg3-leg-carrier">{seg.carrier}</div>
                {seg.ref && <div className="dg3-leg-ref">{seg.ref}</div>}
              </div>

              <div className="dg3-leg-timeline">
                <div className="dg3-leg-end">
                  <div className="dg3-leg-time">{seg.dep}</div>
                  <div className="dg3-leg-place">
                    <span className="dg3-leg-iata">{seg.from}</span>{fromName || ''}
                  </div>
                </div>
                <div className="dg3-leg-track">
                  <div className="dg3-track-dur">{seg.duration}</div>
                  <div className="dg3-track-bar">
                    <div className="dg3-track-line" />
                    <span className="dg3-track-icon" style={{ color }}>{icon}</span>
                    <div className="dg3-track-line" />
                  </div>
                </div>
                <div className="dg3-leg-end right">
                  <div className="dg3-leg-time">
                    {seg.arr}{seg.nextDay && <span className="dg2-nextday">+1</span>}
                  </div>
                  <div className="dg3-leg-place">
                    <span className="dg3-leg-iata">{seg.to}</span>{toName || ''}
                  </div>
                </div>
              </div>

              <div className="dg3-leg-price" style={{ color: seg.price != null ? color : 'var(--lt)' }}>
                {isFollowUpFlightInPkg ?
                  <span className="dg3-leg-price-incl">{t.includedInFlight}</span> :
                  seg.price == null ?
                    <span>—</span> :
                    <span>{fmt(seg.price)}</span>
                }
              </div>
            </div>

            {isFlightGroupEnd && seg.buyUrl &&
              <div className="dg3-cta-row">
                <a href={seg.buyUrl} target="_blank" rel="noopener noreferrer"
                  className="dg3-sky-btn"
                  onClick={(e) => e.stopPropagation()}>
                  ✈ {skyCopy} <span className="dg3-sky-btn-arrow">↗</span>
                </a>
              </div>
            }

            {!isFlight && seg.buyUrl &&
              <div className="dg3-cta-row">
                <a href={seg.buyUrl} target="_blank" rel="noopener noreferrer"
                  className="dg3-buy-btn"
                  style={{ borderColor: color, color }}
                  onClick={(e) => e.stopPropagation()}>
                  {icon} {seg.type === 'bus' ? (lang === 'tr' ? "FlixBus'ta Ara" : 'Search on FlixBus') : (lang === 'tr' ? "DB'de Ara" : 'Search on DB')} <span className="dg3-sky-btn-arrow">↗</span>
                </a>
              </div>
            }
          </React.Fragment>);

      })}
    </div>);

}
