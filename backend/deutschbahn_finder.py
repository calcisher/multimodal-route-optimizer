import requests
from datetime import datetime

BASE = "https://v6.db.transport.rest"

r = requests.get(f"{BASE}/journeys", params={
    "from": "8011160",
    "to": "8000261",
    "departure": datetime.now().replace(hour=6).isoformat(),
    "results": 10,
    "tickets": "true",
    "nationalExpress": "false",
    "national": "false",
    "regionalExp": "true",
    "regional": "true",
    "suburban": "false",
    "subway": "false",
    "tram": "false",
    "bus": "false",
    "ferry": "false",
    "taxi": "false",
})

for j in r.json().get("journeys", []):
    legs = j["legs"]
    tipler = [l.get("line", {}).get("product", "?") for l in legs if l.get("line")]
    aktarma = len(legs) - 1
    dep_dt = datetime.fromisoformat(legs[0]["departure"])
    arr_dt = datetime.fromisoformat(legs[-1]["arrival"])
    sure = int((arr_dt - dep_dt).total_seconds() / 60)

    if j.get("price"):
        fiyat = f"{j['price']['amount']} {j['price']['currency']}"
        dt_notu = ""
    else:
        fiyat = "60€"
        dt_notu = " [Deutschland Ticket]"

    print(f"  ⏰ {dep_dt.strftime('%H:%M')}→{arr_dt.strftime('%H:%M')} | "
          f"🕐 {sure//60}s{sure%60}dk | 🔄 {aktarma} aktarma | "
          f"💶 {fiyat}{dt_notu} | {tipler}")