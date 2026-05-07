// ── Trip-quality badges: BEST VALUE, FASTEST, NON-STOP, LOW CO2 ──────────────
// Computes a single best badge per item (max 1 per card to avoid clutter).

function computeBadgeMap(items, getPrice, getDurMin, getStops, getMode) {
  if (!Array.isArray(items) || items.length === 0) return {};
  const map = {};
  // Find anchors
  let cheapestIdx = -1, fastestIdx = -1, bestValueIdx = -1, ecoIdx = -1;
  let minPrice = Infinity, minDur = Infinity, bestScore = Infinity;
  items.forEach((it, i) => {
    const p = getPrice(it), d = getDurMin(it);
    if (p != null && p < minPrice) { minPrice = p; cheapestIdx = i; }
    if (d != null && d < minDur) { minDur = d; fastestIdx = i; }
  });
  // Best value = lowest (price * duration) score
  items.forEach((it, i) => {
    const p = getPrice(it), d = getDurMin(it);
    if (p == null || d == null) return;
    // Normalize: price/minPrice + dur/minDur, lower is better
    const score = (p / minPrice) + (d / minDur);
    if (score < bestScore) { bestScore = score; bestValueIdx = i; }
  });
  // Eco = lowest CO2 (ground-only or short flights). Prefer items where mode != flight.
  items.forEach((it, i) => {
    const m = getMode ? getMode(it) : null;
    if (m === 'ground' && ecoIdx === -1) ecoIdx = i;
  });

  // Priority: best value > fastest > non-stop > eco. Each badge once.
  const used = new Set();
  if (bestValueIdx >= 0) { map[bestValueIdx] = { kind: 'value', label: 'BEST VALUE', icon: '🏆' }; used.add(bestValueIdx); }
  if (fastestIdx >= 0 && !used.has(fastestIdx)) { map[fastestIdx] = { kind: 'fast', label: 'FASTEST', icon: '⚡' }; used.add(fastestIdx); }
  // Non-stop: first card with stops===0 that isn't already badged
  items.forEach((it, i) => {
    if (used.has(i)) return;
    const s = getStops ? getStops(it) : null;
    if (s === 0 && !map[i]) { map[i] = { kind: 'nonstop', label: 'NON-STOP', icon: '🎯' }; used.add(i); }
  });
  if (ecoIdx >= 0 && !used.has(ecoIdx)) { map[ecoIdx] = { kind: 'eco', label: 'LOW CO₂', icon: '🌱' }; used.add(ecoIdx); }
  return map;
}

function TripBadge({ badge }) {
  if (!badge) return null;
  return (
    <span className={`trip-badge ${badge.kind}`}>
      <span>{badge.icon}</span>{badge.label}
    </span>
  );
}
