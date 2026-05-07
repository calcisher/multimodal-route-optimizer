// ── CityInput: city autocomplete with keyboard nav ───────────────────────────
// Search is city-name-only; the backend resolve_iata maps the city to an IATA.
// IATA-typed queries (e.g. "FCO") still surface their parent city in the
// dropdown, but the field never displays an airport code.

function CityInput({ label, value, onChange, lang, airports }) {
  const [focused, setFocused] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const t = T[lang];
  const suggestions = buildCitySuggestions(airports, value, 8);
  const showDrop = focused && value.trim().length > 0;

  useEffect(() => { setActiveIdx(0); }, [value]);

  const pickCity = (s) => {
    onChange(s.city);
    setFocused(false);
  };

  const onKeyDown = (e) => {
    if (!showDrop || suggestions.length === 0) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx((i) => (i + 1) % suggestions.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx((i) => (i - 1 + suggestions.length) % suggestions.length); }
    else if (e.key === 'Enter') { e.preventDefault(); pickCity(suggestions[activeIdx]); }
    else if (e.key === 'Escape') { setFocused(false); }
  };

  return (
    <div className="sf-field">
      <div className="sf-label">{label}</div>
      <div className="sf-input-wrap">
        <input className="sf-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 180)}
          onKeyDown={onKeyDown}
          autoComplete="off"
          placeholder={label === T[lang].from ? "Rome, Berlin..." : "Milan, Munich..."} />
      </div>
      {showDrop &&
        <div className="ap-dropdown">
          {suggestions.length === 0 ?
            <div className="ap-empty">{t.noAirportMatch}</div> :
            suggestions.map((s, i) => {
              const active = i === activeIdx ? ' active' : '';
              return (
                <div key={`city-${_norm(s.city)}`}
                  className={`ap-item city${active}`}
                  onMouseDown={() => pickCity(s)}
                  onMouseEnter={() => setActiveIdx(i)}>
                  <span className="ap-iata">★</span>
                  <div>
                    <div className="ap-name">{s.city}</div>
                    <div className="ap-city">{s.country || ''}</div>
                  </div>
                </div>);
            })
          }
        </div>
      }
    </div>);
}
