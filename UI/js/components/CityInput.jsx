// ── CityInput: city/airport autocomplete with keyboard nav and chips ─────────
// ── CityInput ─────────────────────────────────────────────────────────────────
// Normalize for fuzzy matching: lowercase + strip diacritics so "Nurnberg"
// matches "Nürnberg" and "Munchen" matches "München".

function CityInput({ label, value, onChange, onAirportSelect, selectedAirport, lang, airports }) {
  const [focused, setFocused] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const t = T[lang];
  const suggestions = buildAirportSuggestions(airports, value, 8);
  const showDrop = focused && value.trim().length > 0;

  useEffect(() => { setActiveIdx(0); }, [value]);

  const pickAirport = (ap) => {
    onChange(ap.city);
    onAirportSelect(ap.iata);
    setFocused(false);
  };
  const pickCity = (s) => {
    onChange(s.city);
    onAirportSelect(null);
    setFocused(false);
  };
  const pickSuggestion = (s) => s.type === 'city' ? pickCity(s) : pickAirport(s.a);

  const onKeyDown = (e) => {
    if (!showDrop || suggestions.length === 0) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx((i) => (i + 1) % suggestions.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx((i) => (i - 1 + suggestions.length) % suggestions.length); }
    else if (e.key === 'Enter') { e.preventDefault(); pickSuggestion(suggestions[activeIdx]); }
    else if (e.key === 'Escape') { setFocused(false); }
  };

  return (
    <div className="sf-field">
      <div className="sf-label">{label}</div>
      <div className="sf-input-wrap">
        <input className="sf-input"
          style={selectedAirport ? { paddingRight: 56 } : {}}
          value={value}
          onChange={(e) => { onChange(e.target.value); onAirportSelect(null); }}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 180)}
          onKeyDown={onKeyDown}
          autoComplete="off"
          placeholder={label === T[lang].from ? "Roma, Berlin, FCO..." : "Milano, München, MUC..."} />
        {selectedAirport && <span className="sf-ap-badge">{selectedAirport}</span>}
      </div>
      {showDrop &&
        <div className="ap-dropdown">
          {suggestions.length === 0 ?
            <div className="ap-empty">{t.noAirportMatch}</div> :
            suggestions.map((s, i) => {
              const active = i === activeIdx ? ' active' : '';
              if (s.type === 'city') {
                return (
                  <div key={`city-${_norm(s.city)}`}
                    className={`ap-item city${active}`}
                    onMouseDown={() => pickSuggestion(s)}
                    onMouseEnter={() => setActiveIdx(i)}>
                    <span className="ap-iata">★</span>
                    <div>
                      <div className="ap-name">{s.city} — {t.allAirports}</div>
                      <div className="ap-city">{s.airports.length} {t.airportsCount} · {s.country || ''}</div>
                    </div>
                  </div>);
              }
              return (
                <div key={`ap-${s.a.iata}-${s.child ? 'c' : 'r'}`}
                  className={`ap-item${s.child ? ' child' : ''}${active}`}
                  onMouseDown={() => pickSuggestion(s)}
                  onMouseEnter={() => setActiveIdx(i)}>
                  <span className="ap-iata">{s.a.iata}</span>
                  <div>
                    <div className="ap-name">{s.a.name}</div>
                    <div className="ap-city">{s.a.city}{s.a.country ? ` · ${s.a.country}` : ''}</div>
                  </div>
                </div>);
            })
          }
        </div>
      }
    </div>);
}
