import requests
import sqlite3
import os
import json
import time
from datetime import datetime, timedelta

import pandas as pd

# ──────────────────────────────────────────────
# Konfigürasyon
# ──────────────────────────────────────────────
DB_PATH = "data/flixbus_europe.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",  # İngilizce sonuçları da yakalayabilmek için
}

os.makedirs("data", exist_ok=True)


# ──────────────────────────────────────────────
# 1. Veritabanı Kurulumu (Gelişmiş Şema)
# ──────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    # Birleştirilmiş DB şemasıyla tam uyum için slug sütunu da eklendi
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cities (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            search_terms TEXT,
            slug         TEXT,
            country      TEXT
        )
    """)
    conn.commit()
    return conn


# ──────────────────────────────────────────────
# 2. Autocomplete API (Dinamik Ülke Destekli)
# ──────────────────────────────────────────────
def fetch_city_id_via_autocomplete(query: str, target_country: str = None) -> tuple[str, str, str] | None:
    """
    Returns: (city_id, official_name, country_code)
    """
    url = "https://global.api.flixbus.com/search/autocomplete/cities"
    params = {
        "q": query,
        "lang": "en",
        "flixbus_cities_only": "true"
    }
    if target_country:
        params["country"] = target_country

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data:
            return None

        best = data[0]
        official_name = best["name"]

        # Global lokasyon filtrelemesi
        if ", TX" in official_name or ", USA" in official_name:
            return None

        country_code = best.get("country_code", target_country or "")
        return best["id"], official_name, country_code
    except Exception:
        return None


# ──────────────────────────────────────────────
# 3. Şehir Arama & Akıllı Öğrenme (Alias Kaydı)
# ──────────────────────────────────────────────
def find_city(conn: sqlite3.Connection, query_name: str) -> tuple[str, str] | None:
    """
    Önce veritabanında resmi isim VEYA arama terimlerine göre arar.
    Bulamazsa API'ye sorar, öğrenir ve İngilizce/farklı ismi alias olarak DB'ye ekler.
    """
    cur = conn.cursor()
    query_lower = query_name.lower()

    # 1. DB'de tam veya search_terms (alias) içinde ara
    cur.execute("""
        SELECT id, name FROM cities 
        WHERE LOWER(name) = ? OR LOWER(search_terms) LIKE ?
    """, (query_lower, f"%{query_lower}%"))

    row = cur.fetchone()
    if row:
        return row

    # 2. DB'de yoksa API'den global arama yap
    print(f"  🔍 '{query_name}' DB'de yok, API'ye danışılıyor...")
    result = fetch_city_id_via_autocomplete(query_name)

    if result:
        city_id, official_name, country_code = result

        # 3. Bulunan sonucu DB'ye ekle veya varsa search_terms sütununu güncelle
        cur.execute("""
            INSERT INTO cities (id, name, search_terms, country) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET 
                search_terms = IFNULL(search_terms, '') || ',' || excluded.search_terms
            WHERE IFNULL(search_terms, '') NOT LIKE ?
        """, (city_id, official_name, query_lower, country_code, f"%{query_lower}%"))

        conn.commit()
        return city_id, official_name

    return None


# ──────────────────────────────────────────────
# 5. Bilet Arama
# ──────────────────────────────────────────────
def search_tickets(from_id: str, to_id: str, date: str) -> list[dict]:
    url = "https://global.api.flixbus.com/search/service/v4/search"
    params = {
        "from_city_id": from_id,
        "to_city_id": to_id,
        "departure_date": date,
        "products": '{"adult":1}',
        "currency": "EUR",
        "locale": "en_US",
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
            dep = detail.get("departure", {}).get("date")
            arr = detail.get("arrival", {}).get("date")
            dur = detail.get("duration", {})
            seats = detail.get("available", {}).get("seats")
            buy_url = f"https://shop.global.flixbus.com/search?departureCity={from_id}&arrivalCity={to_id}&rideDate={date}&adult=1"

            results.append({
                "tarih": date,
                "kalkış": dep,
                "varış": arr,
                "süre": f"{dur.get('hours', '0')}s {dur.get('minutes', '0')}dk",
                "fiyat": price,
                "koltuk": seats,
                "url": buy_url,
            })

    results.sort(key=lambda x: x["fiyat"] or 9999)
    return results


def search_range(from_id: str, to_id: str, start: str, end: str) -> list[dict]:
    s = datetime.strptime(start, "%d-%m-%Y")
    e = datetime.strptime(end, "%d-%m-%Y")
    all_tickets = []

    current = s
    while current <= e:
        d = current.strftime("%d.%m.%Y")
        tickets = search_tickets(from_id, to_id, d)
        all_tickets.extend(tickets)
        if tickets:
            print(f"  {d}: {len(tickets)} sefer | En ucuz: {tickets[0]['fiyat']} EUR")
        else:
            print(f"  {d}: Sefer bulunamadı.")
        current += timedelta(days=1)
        time.sleep(0.5)

    all_tickets.sort(key=lambda x: x["fiyat"] or 9999)
    return all_tickets


# ──────────────────────────────────────────────
# 6. Tek Metod ile Bilet Arama
# ──────────────────────────────────────────────
def get_trips(
    origin: str,
    destination: str,
    date: str,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """
    origin/destination: sehir adi (ornek: "Berlin", "Venice")
    date: "DD.MM.YYYY" veya "YYYY-MM-DD" formatinda
    conn: None verilirse fonksiyon kendi baglantisini acar
    Returns: fiyata gore sirali pandas DataFrame, bulunamazsa bos DataFrame
    """
    if conn is None:
        conn = init_db()

    # Tarih formatini normalize et
    if len(date) == 10 and date[4] == "-":
        date = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")

    from_city = find_city(conn, origin)
    to_city = find_city(conn, destination)

    if not from_city or not to_city:
        return pd.DataFrame()

    from_id, from_name = from_city
    to_id, to_name = to_city

    tickets = search_tickets(from_id, to_id, date)
    if not tickets:
        return pd.DataFrame()

    rows = []
    for t in tickets:
        dur_parts = t["süre"].split()  # "8s 45dk"
        hours = int(dur_parts[0].replace("s", "")) if len(dur_parts) > 0 else 0
        minutes = int(dur_parts[1].replace("dk", "")) if len(dur_parts) > 1 else 0

        rows.append({
            "origin": from_name,
            "destination": to_name,
            "date": date,
            "departure_dt": pd.to_datetime(t["kalkış"], errors="coerce"),
            "arrival_dt": pd.to_datetime(t["varış"], errors="coerce"),
            "duration_min": hours * 60 + minutes,
            "price_eur": float(t["fiyat"]) if t["fiyat"] is not None else None,
            "seats_available": t["koltuk"],
            "url": t["url"],
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("price_eur").reset_index(drop=True)
    return df


# ──────────────────────────────────────────────
# 7. Ana Çalıştırıcı
# ──────────────────────────────────────────────
if __name__ == "__main__":
    conn = init_db()

    print("🚄 FlixBus Avrupa Akıllı Arama Motoru")
    print("-" * 40)

    # Şehirleri bul
    c1 = find_city(conn, "Venice")
    c2 = find_city(conn, "Munich")

    if not c1 or not c2:
        print("❌ Şehirler eşleştirilemedi.")
        exit(1)

    print(f"\n🏙️  Kalkış: {c1[1]} (ID: {c1[0]})")
    print(f"🏙️  Varış:  {c2[1]} (ID: {c2[0]})")

    # 📅 TARİH ARALIĞI ARAMASI (Örn: 1 Haziran - 5 Haziran 2026)
    # Not: search_range fonksiyonumuz tarihleri "DD-MM-YYYY" formatında bekliyor.
    baslangic_tarihi = "01-06-2026"
    bitis_tarihi = "05-06-2026"

    print(f"\n📅 {baslangic_tarihi} ile {bitis_tarihi} arası taranıyor...")

    # Tüm aralıktaki biletleri çeker ve kendi içinde fiyata göre sıralar
    best_tickets = search_range(c1[0], c2[0], baslangic_tarihi, bitis_tarihi)

    print(f"\n🏆 TÜM ARAMANIN EN UCUZ 5 BİLETİ:")
    print("-" * 40)

    if not best_tickets:
        print("Bu tarih aralığında hiç sefer bulunamadı.")
    else:
        for i, t in enumerate(best_tickets[:5], 1):
            # API'den gelen kalkış/varış saatleri genelde ISO formatındadır (2026-06-01T21:00:00)
            # Daha temiz görünmesi için sadece saat kısmını (11. ve 16. karakterler arası) alıyoruz.
            saat_kalkis = t['kalkış'][11:16]
            saat_varis = t['varış'][11:16]

            print(
                f"{i}. Tarih: {t['tarih']} | {saat_kalkis} -> {saat_varis} | Süre: {t['süre']} | Fiyat: {t['fiyat']} EUR")
            print(f"   🔗 Bilet Linki: {t['url']}\n")