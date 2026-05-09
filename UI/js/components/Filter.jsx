// ── Filter UI: dual-handle range slider, panel, and active chips ─────────────
function DualSlider({ from, to, onChange, min = 0, max = 24, step = 0.5, fmt, ariaLabelFrom, ariaLabelTo }) {
  const span = max - min;
  const fillL = ((from - min) / span) * 100;
  const fillR = ((to - min) / span) * 100;
  return (
    <div>
      <div className="fp-dual">
        <div className="fp-dual-track" />
        <div className="fp-dual-fill" style={{ left: `${fillL}%`, right: `${100 - fillR}%` }} />
        <input type="range" min={min} max={max} step={step} value={from}
          aria-label={ariaLabelFrom}
          onChange={(e) => { const v = +e.target.value; onChange(Math.min(v, to - step), to); }} />
        <input type="range" min={min} max={max} step={step} value={to}
          aria-label={ariaLabelTo}
          onChange={(e) => { const v = +e.target.value; onChange(from, Math.max(v, from + step)); }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--lt)', fontWeight: 600, marginTop: 2 }}>
        <span>{fmt(from)}</span><span>{fmt(to)}</span>
      </div>
    </div>
  );
}

function FilterPanel({ filter, setFilter, lang }) {
  const t = T[lang];
  const set = (k, v) => setFilter({ ...filter, [k]: v });
  return (
    <div className="filter-panel">
      <div className="fp-grid">
        <div className="fp-group">
          <div className="fp-label">
            <span>⏱ {t.fMaxDur}</span>
            <span className="fp-label-val">{filter.maxDurH >= 24 ? t.fAny : `≤ ${filter.maxDurH}${t.fHrs}`}</span>
          </div>
          <input className="fp-slider" type="range" min={1} max={24} step={1}
            aria-label={t.fMaxDur}
            value={filter.maxDurH} onChange={(e) => set('maxDurH', +e.target.value)} />
        </div>
        <div className="fp-group">
          <div className="fp-label">
            <span>🛫 {t.fDepWindow}</span>
            <span className="fp-label-val">{fmtFilterHour(filter.depFromH)} - {fmtFilterHour(filter.depToH)}</span>
          </div>
          <DualSlider from={filter.depFromH} to={filter.depToH}
            onChange={(a, b) => setFilter({ ...filter, depFromH: a, depToH: b })}
            ariaLabelFrom={`${t.fDepWindow} — ${lang === 'en' ? 'earliest' : 'en erken'}`}
            ariaLabelTo={`${t.fDepWindow} — ${lang === 'en' ? 'latest' : 'en geç'}`}
            fmt={fmtFilterHour} />
        </div>
        <div className="fp-group">
          <div className="fp-label">
            <span>🛬 {t.fArrWindow}</span>
            <span className="fp-label-val">{fmtFilterHour(filter.arrFromH)} - {fmtFilterHour(filter.arrToH)}</span>
          </div>
          <DualSlider from={filter.arrFromH} to={filter.arrToH}
            onChange={(a, b) => setFilter({ ...filter, arrFromH: a, arrToH: b })}
            ariaLabelFrom={`${t.fArrWindow} — ${lang === 'en' ? 'earliest' : 'en erken'}`}
            ariaLabelTo={`${t.fArrWindow} — ${lang === 'en' ? 'latest' : 'en geç'}`}
            fmt={fmtFilterHour} />
        </div>
        <div className="fp-group">
          <div className="fp-label"><span>🔁 {t.fMaxTransfers}</span></div>
          <div className="fp-chips">
            {[
              { v: 0, l: t.fDirect },
              { v: 1, l: '≤ 1' },
              { v: 2, l: '≤ 2' },
              { v: -1, l: t.fAny }
            ].map((opt) => (
              <button key={opt.v} className={`fp-chip${filter.maxTransfers === opt.v ? ' active' : ''}`}
                onClick={() => set('maxTransfers', opt.v)}>{opt.l}</button>
            ))}
          </div>
        </div>
        <div className="fp-group" style={{ justifyContent: 'flex-end' }}>
          <div className="fp-label"><span>🌙 {t.fOvernight}</span></div>
          <div className={`fp-toggle${filter.excludeOvernight ? ' on' : ''}`}
            onClick={() => set('excludeOvernight', !filter.excludeOvernight)}>
            <div className="fp-toggle-track"><div className="fp-toggle-thumb" /></div>
            <div className="fp-toggle-label">{filter.excludeOvernight ? (lang === 'tr' ? 'Açık' : 'On') : (lang === 'tr' ? 'Kapalı' : 'Off')}</div>
          </div>
          <div className="fp-help">{t.fOvernightHelp}</div>
        </div>
      </div>
    </div>
  );
}

function ActiveFilterChips({ filter, setFilter, lang }) {
  const t = T[lang];
  const chips = [];
  const set = (k, v) => setFilter({ ...filter, [k]: v });
  if (filter.maxDurH < 24) chips.push({ key: 'dur', label: `≤ ${filter.maxDurH}${t.fHrs}`, clear: () => set('maxDurH', 24) });
  if (filter.depFromH > 0 || filter.depToH < 24) chips.push({ key: 'dep', label: `🛫 ${fmtFilterHour(filter.depFromH)}-${fmtFilterHour(filter.depToH)}`, clear: () => setFilter({ ...filter, depFromH: 0, depToH: 24 }) });
  if (filter.arrFromH > 0 || filter.arrToH < 24) chips.push({ key: 'arr', label: `🛬 ${fmtFilterHour(filter.arrFromH)}-${fmtFilterHour(filter.arrToH)}`, clear: () => setFilter({ ...filter, arrFromH: 0, arrToH: 24 }) });
  if (filter.maxTransfers !== -1) chips.push({ key: 'tx', label: `🔁 ${filter.maxTransfers === 0 ? t.fDirect : '≤ ' + filter.maxTransfers}`, clear: () => set('maxTransfers', -1) });
  if (filter.excludeOvernight) chips.push({ key: 'ov', label: `🌙 ${t.noOvernightChip}`, clear: () => set('excludeOvernight', false) });
  if (!chips.length) return null;
  return (
    <div className="filter-chip-row">
      {chips.map((c) => (
        <span key={c.key} className="filter-chip">
          {c.label}
          <button className="filter-chip-x" onClick={c.clear} title={lang === 'tr' ? 'Kaldır' : 'Remove'}>✕</button>
        </span>
      ))}
      <button className="filter-reset-btn" onClick={() => setFilter(FILTER_DEFAULTS)}>↺ {t.fReset}</button>
    </div>
  );
}
