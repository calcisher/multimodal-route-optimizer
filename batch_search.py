"""Batch driver for the final report's Results section.
Runs candidate routes against all five live endpoints in parallel, extracts the
cheapest price per category and the total door-to-door duration of that cheapest
option, identifies the best multimodal option, and flags complete routes.
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

BASE = "http://127.0.0.1:5001"
DATE = "2026-07-09"
TIMEOUT = 200

# Longer / pricier corridors where combining modes is most likely to win, plus a
# few medium ones. The complete ones get merged with the earlier batch.
CANDIDATES = [
    ("Frankfurt", "Bologna"),
    ("Cologne", "Milan"),
    ("Frankfurt", "Venice"),
    ("Cologne", "Rome"),
    ("Berlin", "Florence"),
    ("Hamburg", "Naples"),
    ("Cologne", "Naples"),
    ("Dusseldorf", "Milan"),
    ("Hamburg", "Bari"),
    ("Munich", "Naples"),
    ("Berlin", "Naples"),
    ("Milan", "Nuremberg"),
    ("Dusseldorf", "Catania"),
    ("Stuttgart", "Bari"),
]

EPS = ["flights", "flight-plus-bus", "bus-plus-flight", "bus-flight-bus", "trains"]


def post(ep, frm, to):
    try:
        r = requests.post(
            f"{BASE}/api/{ep}",
            json={"from_city": frm, "to_city": to, "date": DATE},
            timeout=TIMEOUT,
        )
        return ep, r.json()
    except Exception as e:  # noqa: BLE001
        return ep, {"error": f"{type(e).__name__}: {e}"}


def iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def mins(a, b):
    da, db = iso(a), iso(b)
    if da and db:
        return int((db - da).total_seconds() // 60)
    return None


def pm(s):
    if not s:
        return None
    m = re.match(r"(\d+)h\s*(\d+)m", s)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def fmt_dur(m):
    if m is None:
        return None
    return f"{m // 60}h{m % 60:02d}"


def flight_dur(f):
    for k in ("totalDuration", "duration"):
        if f.get(k):
            return pm(f[k])
    legs = f.get("legs") or []
    if len(legs) == 1:
        return pm(legs[0].get("duration"))
    if legs:
        tot = sum((pm(l.get("duration")) or 0) for l in legs)
        lo = f.get("layover")
        if isinstance(lo, dict):
            tot += pm(lo.get("duration")) or 0
        return tot or None
    return None


def cheapest_direct(js):
    allf = [f for f in (js.get("bestFlights") or []) + (js.get("cheapFlights") or [])
            if f.get("price") is not None]
    if not allf:
        return None
    c = min(allf, key=lambda f: f["price"])
    return round(c["price"], 2), flight_dur(c), c.get("stops")


def fpb_min(hubs):
    best = None
    for h in hubs or []:
        for f in h.get("flightOptions", []):
            fp, fa, fd = f.get("price"), f.get("arrISO"), f.get("depISO")
            if fp is None or not fa:
                continue
            fa_dt = iso(fa)
            for b in h.get("busOptions", []):
                bp, bdep, barr = b.get("price"), b.get("depISO"), b.get("arrISO")
                if bp is None or not bdep:
                    continue
                bdep_dt = iso(bdep)
                if fa_dt and bdep_dt and (bdep_dt - fa_dt).total_seconds() >= 2 * 3600:
                    tot = fp + bp
                    if best is None or tot < best[0]:
                        best = (round(tot, 2), mins(fd, barr))
    return best


def bpf_min(hubs):
    best = None
    for h in hubs or []:
        for b in h.get("busOptions", []):
            bp, barr, bdep = b.get("price"), b.get("arrISO"), b.get("depISO")
            if bp is None or not barr:
                continue
            barr_dt = iso(barr)
            for f in h.get("flightOptions", []):
                fp, fdep, farr = f.get("price"), f.get("depISO"), f.get("arrISO")
                if fp is None or not fdep:
                    continue
                fdep_dt = iso(fdep)
                if barr_dt and fdep_dt and (fdep_dt - barr_dt).total_seconds() >= 2 * 3600:
                    tot = bp + fp
                    if best is None or tot < best[0]:
                        best = (round(tot, 2), mins(bdep, farr))
    return best


def bfb_min(rows):
    best = None
    for h in rows or []:
        mt = h.get("minTotal")
        if mt is None:
            continue
        dur = None
        dt = h.get("defaultTrio")
        if isinstance(dt, dict):
            b1 = (h.get("bus1PrevOptions") if dt.get("bus1Source") == "prev"
                  else h.get("bus1Options")) or []
            b2 = (h.get("bus2NextOptions") if dt.get("bus2Source") == "next"
                  else h.get("bus2Options")) or []
            try:
                dur = mins(b1[dt["bus1Idx"]].get("depISO"),
                           b2[dt["bus2Idx"]].get("arrISO"))
            except (IndexError, KeyError, TypeError):
                dur = None
        if best is None or mt < best[0]:
            best = (round(mt, 2), dur)
    return best


def overland_min(trains):
    best = None
    for t in trains or []:
        p = t.get("price")
        if p is None:
            continue
        if best is None or p < best[0]:
            best = (round(p, 2), t.get("durationMin"), t.get("type"))
    return best


def run_route(frm, to):
    out = {"route": f"{frm}->{to}", "from": frm, "to": to}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(post, ep, frm, to) for ep in EPS]
        data = {}
        for fu in as_completed(futs):
            ep, js = fu.result()
            data[ep] = js

    d = cheapest_direct(data.get("flights", {}))
    out["direct"], out["direct_dur"], out["direct_stops"] = (
        (d[0], fmt_dur(d[1]), d[2]) if d else (None, None, None))

    fpb = fpb_min(data.get("flight-plus-bus", {}).get("flightPlusBus"))
    out["fpb"], out["fpb_dur"] = (fpb[0], fmt_dur(fpb[1])) if fpb else (None, None)
    bpf = bpf_min(data.get("bus-plus-flight", {}).get("busPlusFlight"))
    out["bpf"], out["bpf_dur"] = (bpf[0], fmt_dur(bpf[1])) if bpf else (None, None)
    bfb = bfb_min(data.get("bus-flight-bus", {}).get("busFlightBus"))
    out["bfb"], out["bfb_dur"] = (bfb[0], fmt_dur(bfb[1])) if bfb else (None, None)
    ovl = overland_min(data.get("trains", {}).get("trains"))
    out["overland"], out["overland_dur"], out["overland_type"] = (
        (ovl[0], fmt_dur(ovl[1]), ovl[2]) if ovl else (None, None, None))

    # best multimodal option among the three combined patterns
    multi = [(out["fpb"], out["fpb_dur"], "Flight+bus"),
             (out["bpf"], out["bpf_dur"], "Bus+flight"),
             (out["bfb"], out["bfb_dur"], "Bus-flight-bus")]
    multi = [m for m in multi if m[0] is not None]
    if multi:
        bm = min(multi, key=lambda m: m[0])
        out["multi_best"], out["multi_best_dur"], out["multi_best_kind"] = bm
    else:
        out["multi_best"] = out["multi_best_dur"] = out["multi_best_kind"] = None

    cats = [out["direct"], out["fpb"], out["bpf"], out["bfb"], out["overland"]]
    out["complete"] = all(c is not None for c in cats)
    out["errors"] = {ep: data[ep]["error"] for ep in EPS if data.get(ep, {}).get("error")}
    return out


def main():
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run_route, frm, to): (frm, to) for frm, to in CANDIDATES}
        for fu in as_completed(futs):
            r = fu.result()
            results.append(r)
            tag = "COMPLETE" if r["complete"] else "partial "
            print(f"[{tag}] {r['route']:22s} dir={r['direct']}/{r['direct_dur']} "
                  f"F+B={r['fpb']} B+F={r['bpf']} BFB={r['bfb']} OVL={r['overland']} "
                  f"| multiBest={r['multi_best']} ({r['multi_best_kind']})"
                  + (f" ERR={list(r['errors'])}" if r["errors"] else ""), flush=True)

    results.sort(key=lambda r: r["route"])
    json.dump(results, open("/tmp/multiroute_batch2.json", "w"), indent=2)
    comp = [r for r in results if r["complete"]]
    print(f"\n{len(comp)}/{len(results)} complete:", [r["route"] for r in comp])


if __name__ == "__main__":
    main()
