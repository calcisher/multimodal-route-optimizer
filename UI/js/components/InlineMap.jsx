// ── InlineMap: Leaflet-rendered route preview shown inside cards ─────────────
// `hubIata` + `hubWait` are optional. When present, the marker for that IATA
// gets a permanent badge below the city tooltip showing layover time/colour.
function InlineMap({ route, onClose, lang, hubIata, hubWait }) {
  const t = T[lang];
  const containerRef = useRef(null);
  const mapRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || typeof L === 'undefined') return;

    const theme = document.documentElement.getAttribute('data-theme') || 'light';
    const tileUrl = theme === 'dark' ?
      'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png' :
      'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';

    const map = L.map(containerRef.current, {
      zoomControl: true,
      scrollWheelZoom: false,
      attributionControl: true
    });

    L.tileLayer(tileUrl, {
      maxZoom: 18,
      subdomains: 'abcd',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
    }).addTo(map);

    const ICON = { flight: '✈', bus: '🚌', train: '🚆' };
    const LABEL = (type) => type === 'flight' ? t.flightLeg : type === 'bus' ? t.busLeg : t.trainLeg;

    const bounds = [];
    const used = new Set();

    route.segments.forEach((seg) => {
      const a = LATLNG[seg.from], b = LATLNG[seg.to];
      if (!a || !b) return;
      used.add(seg.from); used.add(seg.to);
      const color = LINE_COLOR[seg.type] || '#999';
      const pts = seg.type === 'flight' ? arcLatLng(a, b) : [a, b];
      const line = L.polyline(pts, {
        color, weight: 4, opacity: .92,
        dashArray: seg.type === 'bus' ? '10,8' : null
      }).addTo(map);

      const pill = seg.duration ? `${ICON[seg.type]} ${seg.duration}` : `${ICON[seg.type]} ${LABEL(seg.type)}`;
      line.bindTooltip(pill, {
        permanent: true,
        interactive: true,
        direction: 'top',
        offset: [0, -4],
        className: `map-seg-label ${seg.type}`
      });

      const fromCity = CITY_NAMES[seg.from] || seg.from;
      const toCity = CITY_NAMES[seg.to] || seg.to;
      const popupHtml =
        `<div class="map-popup-body">` +
        `<div class="map-popup-head" style="color:${color}">${ICON[seg.type]} <span>${LABEL(seg.type).toUpperCase()}</span></div>` +
        (seg.carrier ? `<div class="map-popup-carrier">${seg.carrier}</div>` : '') +
        `<div class="map-popup-route">${fromCity} → ${toCity}</div>` +
        (seg.duration ? `<div class="map-popup-dur">⏱ ${seg.duration}</div>` : '') +
        `</div>`;
      line.bindPopup(popupHtml, { closeButton: true, autoPan: true, maxWidth: 280 });

      pts.forEach((p) => bounds.push(p));
    });

    used.forEach((iata) => {
      const c = LATLNG[iata];
      if (!c) return;
      const isOrigin = route.segments[0].from === iata;
      const color = isOrigin ? '#F4611E' : '#2563EB';
      const cityName = CITY_NAMES[iata] || iata;
      // The visible pin + permanent city tooltip stays minimal so it never
      // collides with neighbouring segment labels.
      L.circleMarker(c, {
        radius: 7, fillColor: '#fff', color, weight: 3, fillOpacity: 1
      }).addTo(map).bindTooltip(cityName, {
        permanent: true,
        direction: 'top',
        offset: [0, -8],
        className: 'map-city-label' + (isOrigin ? ' origin' : '')
      });
      // Hub-only: stack a second, invisible hit-area marker on the same point
      // and bind a NON-permanent tooltip with the layover badge. Leaflet only
      // allows one tooltip per layer, so this two-marker trick is what lets us
      // keep the city name always visible while showing the wait badge only
      // on hover (avoids the overlap users were hitting in dense maps).
      const isHub = hubIata && hubWait && hubWait.minutes != null && iata === hubIata;
      if (isHub) {
        const css = `color:${hubWait.color || '#5C4A3D'};border-color:${hubWait.color || '#E8DDD8'};background:${hubWait.bg || 'rgba(255,255,255,.95)'}`;
        const html = `<span class="map-hub-wait" style="${css}">⏱ ${fmtConnMinutes(hubWait.minutes, lang)} ${hubWait.label || ''}</span>`;
        L.marker(c, {
          icon: L.divIcon({
            className: 'map-hub-hit',
            html: '',
            iconSize: [28, 28],
            iconAnchor: [14, 14]
          }),
          interactive: true,
          keyboard: false
        }).addTo(map).bindTooltip(html, {
          direction: 'bottom',
          offset: [0, 14],
          className: 'map-hub-wait-tip',
          sticky: false,
          opacity: 1
        });
      }
      bounds.push(c);
    });

    if (bounds.length) map.fitBounds(bounds, { padding: [60, 60] });
    setTimeout(() => map.invalidateSize(), 300);

    mapRef.current = map;
    return () => {
      if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; }
    };
  }, [route, lang]);

  return (
    <div className="inline-map">
      <div className="inline-map-inner" style={{ position: 'relative' }}>
        <button className="inline-map-close" onClick={(e) => { e.stopPropagation(); onClose(); }}>✕</button>
        <div ref={containerRef} className="leaflet-host" onClick={(e) => e.stopPropagation()} />
      </div>
      <div className="map-legend">
        <div className="legend-item"><div className="legend-line" style={{ background: '#2563EB' }} />{t.flightLeg}</div>
        <div className="legend-item"><div className="legend-line" style={{ background: '#F4611E', backgroundImage: 'repeating-linear-gradient(90deg,#F4611E 0,#F4611E 8px,transparent 8px,transparent 13px)' }} />{t.busLeg}</div>
        <div className="legend-item"><div className="legend-line" style={{ background: '#16A34A' }} />{t.trainLeg}</div>
        <div style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--lt)' }}>🔴 {lang === 'tr' ? 'Kalkış' : 'Orig.'} · 🔵 {lang === 'tr' ? 'Varış' : 'Dest.'}</div>
      </div>
    </div>);

}
