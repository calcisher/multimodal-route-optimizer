import requests
import sqlite3
import os
import json
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────
# Konfigürasyon
# ──────────────────────────────────────────────
DB_PATH = "data/flixbus_de.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}

os.makedirs("data", exist_ok=True)

# ──────────────────────────────────────────────
# 1. Veritabanı kurulumu
# ──────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cities (
            id   TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT
        )
    """)
    conn.commit()
    return conn


# ──────────────────────────────────────────────
# 2. Almanya şehirlerini scrape et (sadece isimler)
# ──────────────────────────────────────────────
def scrape_germany_city_names() -> list[str]:
    """flixbus.com/bus/germany sayfasından şehir isimlerini çeker."""

    url = "https://www.flixbus.com/bus/germany"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "html.parser")
    names = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for a in soup.select(f"#{letter} > ul:nth-child(2) > li > a"):
            names.append(a.text.strip())

    print(f"✅ Almanya'da {len(names)} şehir bulundu.")
    return names


# ──────────────────────────────────────────────
# 3. Autocomplete API ile şehir ID'si bul
# ──────────────────────────────────────────────
def fetch_city_id_via_autocomplete(city_name: str) -> tuple[str, str] | None:
    """
    Flixbus autocomplete API'si ile şehir ID'si döner.
    Flixbus autocomplete API'si ile şehir ID'si döner.
    Returns: (city_id, matched_name) veya None
    """
    url = "https://global.api.flixbus.com/search/autocomplete/cities"
    params = {
        "q": city_name,
        "lang": "de",
        "country": "DE",
        "flixbus_cities_only": "false",
        "include_stations": "false",
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data:
            return None
        # İlk sonucu al (en alakalı)
        best = data[0]
        return best["id"], best["name"]
    except Exception:
        return None


# ──────────────────────────────────────────────
# 4. Tüm Almanya şehirlerini DB'ye kaydet
# ──────────────────────────────────────────────
def populate_germany_cities(conn: sqlite3.Connection):
    """Sadece DB boşsa çalışır."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM cities")
    count = cur.fetchone()[0]
    if count > 0:
        print(f"📦 DB'de {count} Almanya şehri mevcut, scrape atlanıyor.")
        return

    city_names = scrape_germany_city_names()
    total = len(city_names)
    found = 0

    for i, name in enumerate(city_names, 1):
        result = fetch_city_id_via_autocomplete(name)
        if result:
            city_id, matched_name = result
            cur.execute(
                "INSERT OR IGNORE INTO cities (id, name) VALUES (?, ?)",
                (city_id, matched_name)
            )
            conn.commit()
            found += 1
            print(f"  [{i}/{total}] ✅ {matched_name} → {city_id}")
        else:
            print(f"  [{i}/{total}] ⚠️  '{name}' için ID bulunamadı")

        time.sleep(0.15)  # rate limit'e takılmamak için

    print(f"\n✅ {found}/{total} şehir kaydedildi.")


# ──────────────────────────────────────────────
# 5. Şehir arama (DB'den)
# ──────────────────────────────────────────────
def find_city(conn: sqlite3.Connection, name: str) -> tuple[str, str] | None:
    """
    Önce tam eşleşme, sonra LIKE ile arar.
    Returns: (city_id, city_name) veya None
    """
    cur = conn.cursor()

    # Tam eşleşme
    cur.execute("SELECT id, name FROM cities WHERE LOWER(name) = LOWER(?)", (name,))
    row = cur.fetchone()
    if row:
        return row

    # Kısmi eşleşme
    cur.execute("SELECT id, name FROM cities WHERE LOWER(name) LIKE LOWER(?)", (f"%{name}%",))
    rows = cur.fetchall()
    if rows:
        print(f"  💡 '{name}' için benzer şehirler: {[r[1] for r in rows[:5]]}")
        return rows[0]

    # DB'de yoksa API'den dene
    print(f"  🔍 '{name}' DB'de yok, API'den aranıyor...")
    result = fetch_city_id_via_autocomplete(name)
    if result:
        city_id, matched_name = result
        conn.execute("INSERT OR IGNORE INTO cities (id, name) VALUES (?, ?)", (city_id, matched_name))
        conn.commit()
        return city_id, matched_name

    return None


# ──────────────────────────────────────────────
# 6. Bilet arama
# ──────────────────────────────────────────────
def search_tickets(from_id: str, to_id: str, date: str) -> list[dict]:
    """
    date: "DD.MM.YYYY" formatında
    Returns: fiyata göre sıralı bilet listesi
    """
    url = "https://global.api.flixbus.com/search/service/v4/search"
    params = {
        "from_city_id": from_id,
        "to_city_id": to_id,
        "departure_date": date,
        "products": '{"adult":1}',
        "currency": "EUR",
        "locale": "de_DE",
        "search_by": "cities",
        "include_after_midnight_rides": "1",
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        return []

    data = resp.json()
    results = []
    for trip in data.get("trips", []):
        for detail in trip.get("results", {}).values():
            price = detail.get("price", {}).get("total")
            dep   = detail.get("departure", {}).get("date")
            arr   = detail.get("arrival", {}).get("date")
            dur   = detail.get("duration", {})
            seats = detail.get("available", {}).get("seats")
            # Bilet satın alma URL'si
            buy_url = (
                f"https://shop.global.flixbus.com/search"
                f"?departureCity={from_id}&arrivalCity={to_id}"
                f"&rideDate={date}&adult=1"
            )
            results.append({
                "tarih":   date,
                "kalkış":  dep,
                "varış":   arr,
                "süre":    f"{dur.get('hours','?')}s {dur.get('minutes','?')}dk",
                "fiyat":   price,
                "koltuk":  seats,
                "url":     buy_url,
            })

    results.sort(key=lambda x: x["fiyat"] or 9999)
    return results


def search_range(from_id: str, to_id: str, start: str, end: str) -> list[dict]:
    """
    start / end: "DD-MM-YYYY"
    """
    s = datetime.strptime(start, "%d-%m-%Y")
    e = datetime.strptime(end,   "%d-%m-%Y")
    all_tickets = []

    current = s
    while current <= e:
        d = current.strftime("%d.%m.%Y")
        tickets = search_tickets(from_id, to_id, d)
        all_tickets.extend(tickets)
        print(f"  {d}: {len(tickets)} sefer bulundu" + (f" | en ucuz: {tickets[0]['fiyat']} EUR" if tickets else ""))
        current += timedelta(days=1)
        time.sleep(0.2)

    all_tickets.sort(key=lambda x: x["fiyat"] or 9999)
    return all_tickets


# ──────────────────────────────────────────────
# 7. Ana kullanım
# ──────────────────────────────────────────────
if __name__ == "__main__":
    conn = init_db()

    # Almanya şehirlerini DB'ye yükle (ilk çalıştırmada ~2-3 dk)
    populate_germany_cities(conn)

    # Tüm şehirleri listele
    rows = conn.execute("SELECT name, id FROM cities ORDER BY name").fetchall()
    print(f"\n📍 DB'deki Almanya şehirleri ({len(rows)} adet):")
    for name, cid in rows[:20]:
        print(f"  {name:30s} → {cid}")
    if len(rows) > 20:
        print(f"  ... ve {len(rows)-20} tane daha")

    # Şehirleri bul
    berlin = find_city(conn, "Berlin")
    munich = find_city(conn, "Munich")

    if not berlin or not munich:
        print("❌ Şehir bulunamadı.")
        exit(1)

    print(f"\n🏙️  {berlin[1]} → ID: {berlin[0]}")
    print(f"🏙️  {munich[1]} → ID: {munich[0]}")

    # Belirli bir gün
    print("\n🎫 01.06.2026 | Berlin → München:")
    tickets = search_tickets(berlin[0], munich[0], "01.06.2026")
    for t in tickets[:5]:
        print(f"  {t['kalkış']} → {t['varış']} | {t['süre']} | {t['fiyat']} EUR | {t['koltuk']} koltuk")
        print(f"  🔗 {t['url']}")

    # Tarih aralığı
    print("\n📅 1–5 Haziran 2026 | Berlin → München | En ucuzlar:")
    best = search_range(berlin[0], munich[0], "01-06-2026", "05-06-2026")
    for t in best[:5]:
        print(f"  {t['tarih']} | {t['kalkış']} → {t['varış']} | {t['fiyat']} EUR")