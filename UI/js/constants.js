// ── App constants: tweaks, mock data, geo lookup tables ─────────────────────
const TWEAK_DEFAULTS = {
  theme: "light",
  currency: "EUR",
  cardStyle: "comfortable"
};


// ── Mock Data ────────────────────────────────────────────────────────────────
const MOCK = {
  bestFlights: [
  { airline: "Air France", flightNo: "AF 1327", price: 113, dep: "05:55", arr: "07:50", via: "CDG", viaName: "Paris", depIata: "VCE", arrIata: "NUE", duration: "4h 25m", stops: 0 },
  { airline: "Air France", flightNo: "AF 1427", price: 113, dep: "12:45", arr: "14:40", via: "CDG", viaName: "Paris", depIata: "VCE", arrIata: "NUE", duration: "5h 10m", stops: 0 },
  { airline: "Air Dolomiti", flightNo: "EN 8205", price: 186, dep: "17:15", arr: "18:20", via: "MUC", viaName: "Munich", depIata: "VCE", arrIata: "NUE", duration: "4h 15m", stops: 0 },
  { airline: "KLM", flightNo: "KL 1630", price: 220, dep: "11:45", arr: "13:40", via: "AMS", viaName: "Amsterdam", depIata: "VCE", arrIata: "NUE", duration: "5h 55m", stops: 0 },
  // Transfer example:
  { airline: "Lufthansa", flightNo: "LH 325+LH 179", price: 231, depIata: "VCE", arrIata: "NUE", stops: 1, totalDuration: "5h 40m",
    legs: [
    { dep: "10:55", arr: "12:25", from: "VCE", to: "FRA", duration: "1h 30m", flightNo: "LH 325" },
    { dep: "13:55", arr: "15:35", from: "FRA", to: "NUE", duration: "1h 40m", flightNo: "LH 179" }],

    layover: { airport: "FRA", city: "Frankfurt", duration: "1h 30m" } }],

  cheapFlights: [
  { airline: "Air France", flightNo: "AF 1727", price: 132, dep: "14:15", arr: "16:05", via: "CDG", viaName: "Paris", depIata: "VCE", arrIata: "NUE", duration: "8h 00m", stops: 0 },
  { airline: "KLM", flightNo: "KL 1628", price: 189, dep: "06:00", arr: "07:50", via: "AMS", viaName: "Amsterdam", depIata: "VCE", arrIata: "NUE", duration: "7h 40m", stops: 0 },
  { airline: "Vueling", flightNo: "VY 6401+VY 102", price: 189, depIata: "VCE", arrIata: "NUE", stops: 1, totalDuration: "9h 20m",
    legs: [
    { dep: "09:10", arr: "11:10", from: "VCE", to: "BCN", duration: "2h 00m", flightNo: "VY 6401" },
    { dep: "13:30", arr: "16:30", from: "BCN", to: "NUE", duration: "3h 00m", flightNo: "VY 102" }],

    layover: { airport: "BCN", city: "Barcelona", duration: "2h 20m" } },
  { airline: "KLM", flightNo: "KL 1634", price: 263, dep: "17:00", arr: "18:55", via: "AMS", viaName: "Amsterdam", depIata: "VCE", arrIata: "NUE", duration: "5h 35m", stops: 0 },
  { airline: "Austrian", flightNo: "OS 550+OS 211", price: 305, depIata: "VCE", arrIata: "NUE", stops: 1, totalDuration: "7h 05m",
    legs: [
    { dep: "07:00", arr: "08:05", from: "VCE", to: "VIE", duration: "1h 05m", flightNo: "OS 550" },
    { dep: "09:50", arr: "11:05", from: "VIE", to: "NUE", duration: "1h 15m", flightNo: "OS 211" }],

    layover: { airport: "VIE", city: "Vienna", duration: "1h 45m" } }],

  flightPlusBus: [
  { flight: { airline: "Air France", flightNo: "AF 1301", price: 61, dep: "05:55", arr: "08:30", toIata: "FRA", toCity: "Frankfurt", duration: "2h 35m" },
    ground: { type: "Bus", company: "FlixBus", price: 11.9, dep: "22:00", arr: "01:10", nextDay: true, from: "Frankfurt, DE", to: "Nürnberg", duration: "3h 10m" },
    total: 72.9, depIata: "VCE", arrIata: "NUE", via: "FRA" },
  { flight: { airline: "Air France", flightNo: "AF 1309", price: 61, dep: "05:55", arr: "08:00", toIata: "ZRH", toCity: "Zürich", duration: "2h 05m" },
    ground: { type: "Bus", company: "FlixBus", price: 22.8, dep: "07:30", arr: "15:15", nextDay: false, from: "Zurich, CH", to: "Nürnberg", duration: "7h 45m" },
    total: 83.8, depIata: "VCE", arrIata: "NUE", via: "ZRH" }],

  busPlusFlight: [
  { ground: { type: "Bus", company: "FlixBus", price: 8, dep: "05:00", arr: "06:40", from: "Venice", to: "Verona, IT", duration: "1h 40m" },
    flight: { airline: "Ryanair", flightNo: "FR 2401", price: 87, dep: "09:30", arr: "11:10", fromIata: "VRN", toCity: "Nürnberg", duration: "1h 40m" },
    total: 95, depIata: "VCE", arrIata: "NUE", via: "VRN" },
  { ground: { type: "Bus", company: "FlixBus", price: 5, dep: "04:30", arr: "05:15", from: "Venice", to: "Treviso, IT", duration: "0h 45m" },
    flight: { airline: "Wizz Air", flightNo: "W6 3201", price: 89, dep: "07:00", arr: "09:30", fromIata: "TSF", toCity: "Nürnberg", duration: "2h 30m" },
    total: 94, depIata: "VCE", arrIata: "NUE", via: "TSF" }],

  busOrTrain: [
  { type: "Bus", company: "FlixBus", price: 45, dep: "06:00", arr: "14:30", duration: "8h 30m", transfers: 1, via: "Munich" },
  { type: "Bus", company: "Eurolines", price: 52, dep: "08:00", arr: "18:00", duration: "10h 00m", transfers: 1, via: "Innsbruck" },
  { type: "Train", company: "Trenitalia + DB", price: 89, dep: "07:12", arr: "15:04", duration: "7h 52m", transfers: 2, via: "Verona, Munich" }]

};

// ── City lat/lng for Leaflet map ──────────────────────────────────────────────
const LATLNG = {
  VCE: [45.5053, 12.3519], NUE: [49.4987, 11.0669],
  FRA: [50.0379, 8.5622], MUC: [48.3538, 11.7861],
  CDG: [49.0097, 2.5479], AMS: [52.3105, 4.7683],
  ZRH: [47.4647, 8.5492], VIE: [48.1103, 16.5697],
  BCN: [41.2974, 2.0833], VRN: [45.3957, 10.8885],
  TSF: [45.6484, 12.1944]
};
const LINE_COLOR = { flight: "#2563EB", bus: "#F4611E", train: "#16A34A" };

const CITY_NAMES = { VCE: 'Venice', NUE: 'Nürnberg', FRA: 'Frankfurt', MUC: 'Munich', CDG: 'Paris', AMS: 'Amsterdam', ZRH: 'Zürich', VIE: 'Vienna', BCN: 'Barcelona', VRN: 'Verona', TSF: 'Treviso' };

// ── Advanced filter defaults ─────────────────────────────────────────────────
const FILTER_DEFAULTS = {
  maxDurH: 24,
  depFromH: 0,
  depToH: 24,
  arrFromH: 0,
  arrToH: 24,
  maxTransfers: -1,
  excludeOvernight: false
};
