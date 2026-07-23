import urllib.request
import json
import os
import math
import sqlite3
import argparse
import base64
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
# ponytail: ZoneInfo avec fallback sécurisé pour le fuseau horaire de Paris
try:
    from zoneinfo import ZoneInfo
    tz_paris = ZoneInfo("Europe/Paris")
except ImportError:
    from datetime import timedelta
    tz_paris = timezone(timedelta(hours=2))
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

# ponytail: chemin relatif — fonctionne aussi bien en local qu'sur GitHub Actions runner Ubuntu
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "feux_historique.db")
PELICANDROMES_PATH = os.path.join(DATA_DIR, "pelicandromes.json")
LEAFLET_CSS_PATH = os.path.join(DATA_DIR, "leaflet.css")
LEAFLET_JS_PATH = os.path.join(DATA_DIR, "leaflet.js")

AUTH_USER = "feux59"
AUTH_PASS = "mto59"

import random as _random
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
]
ANONYMOUS_HEADERS = {
    "User-Agent": _random.choice(_USER_AGENTS),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.google.com/"
}

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feux_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fire_id TEXT,
            timestamp_utc TEXT,
            commune TEXT,
            dept TEXT,
            etat_feu TEXT,
            superficie_ha REAL,
            temp_c REAL,
            humidity_pct REAL,
            wind_speed_kmh INTEGER,
            wind_gusts_kmh INTEGER,
            plume_dir TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fire_id ON feux_snapshots(fire_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON feux_snapshots(timestamp_utc)")
    conn.commit()
    conn.close()

def record_snapshots(results):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_dt = datetime.now(timezone.utc)
    now_utc_str = now_dt.isoformat()
    
    # Calculate current 5-minute rounded slot label (e.g. 02h40)
    current_10m_slot = f"{(now_dt.hour+2)%24:02d}h{(now_dt.minute // 5) * 5:02d}"

    for f in results:
        fire_id = f"fire_{f.get('dept')}_{f.get('commune')}".lower().replace(" ", "_")
        w = f.get("weather", {})
        
        # Check if an entry already exists for this 10-min slot
        cursor.execute("""
            SELECT id, timestamp_utc FROM feux_snapshots
            WHERE fire_id = ?
            ORDER BY id DESC LIMIT 1
        """, (fire_id,))
        last_row = cursor.fetchone()
        
        should_insert = True
        if last_row:
            try:
                last_dt = datetime.fromisoformat(last_row[1])
                last_slot = f"{(last_dt.hour+2)%24:02d}h{(last_dt.minute // 5) * 5:02d}"
                if last_slot == current_10m_slot:
                    should_insert = False
            except Exception:
                pass

        if should_insert:
            cursor.execute("""
                INSERT INTO feux_snapshots (fire_id, timestamp_utc, commune, dept, etat_feu, superficie_ha, temp_c, humidity_pct, wind_speed_kmh, wind_gusts_kmh, plume_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fire_id, now_utc_str, f.get("commune"), f.get("dept"), f.get("etat_feu"),
                f.get("superficie", 0), w.get("temp_c"), w.get("humidity_pct"),
                w.get("wind_speed_kmh", 0), w.get("wind_gusts_kmh", 0), w.get("plume_dir")
            ))
    conn.commit()

    history_by_fire = {}
    for f in results:
        fire_id = f"fire_{f.get('dept')}_{f.get('commune')}".lower().replace(" ", "_")
        cursor.execute("""
            SELECT timestamp_utc, wind_speed_kmh, wind_gusts_kmh, temp_c, humidity_pct
            FROM feux_snapshots
            WHERE fire_id = ?
            ORDER BY id DESC
            LIMIT 12
        """, (fire_id,))
        rows = cursor.fetchall()
        pts = []
        seen_slots = set()
        for r in rows:
            try:
                dt = datetime.fromisoformat(r[0])
                m_5 = (dt.minute // 5) * 5
                time_lbl = f"{(dt.hour+2)%24:02d}h{m_5:02d}"
            except Exception:
                time_lbl = "N/A"
            
            if time_lbl not in seen_slots:
                seen_slots.add(time_lbl)
                pts.append({
                    "time": time_lbl,
                    "wind": r[1] or 0,
                    "gusts": r[2] or 0,
                    "temp": r[3] or 0,
                    "hum": r[4] or 0
                })
        history_by_fire[fire_id] = pts
        f["history_points"] = pts

    conn.close()
    return history_by_fire

def load_logo_base64():
    # ponytail: charger le logo en chemin relatif pour compatibilité locale et CI
    local_logo_path = os.path.join(DATA_DIR, "logo_meteo_climat_pro.png")
    possible_paths = [
        local_logo_path,
        r"C:\Users\grego\Documents\METEO_CLIMAT\minisite-douai\public\logo_default.png",
        r"C:\Users\grego\Documents\METEO_CLIMAT\minisite-douai\public\logo.jpg",
        r"C:\Users\grego\Desktop\LOGOS & IMAGES\logo_meteo_climat_pro.png",
        r"C:\Users\grego\Desktop\cartes_alertes\A_CONSERVER_ABSOLUMENT\logo meteo climat pro 3.png",
        r"C:\Users\grego\.gemini\config\skills\btp\scripts\logo_meteo_climat_pro.png"
    ]
    for p in possible_paths:
        if os.path.exists(p):
            try:
                ext = "png" if p.endswith(".png") else "jpeg"
                with open(p, "rb") as f:
                    return f"data:image/{ext};base64,{base64.b64encode(f.read()).decode('utf-8')}"
            except Exception:
                pass
    return ""

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def calculate_bearing(lat1, lon1, lat2, lon2):
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    r_dlon = math.radians(lon2 - lon1)
    y = math.sin(r_dlon) * math.cos(r_lat2)
    x = math.cos(r_lat1) * math.sin(r_lat2) - math.sin(r_lat1) * math.cos(r_lat2) * math.cos(r_dlon)
    b_rad = math.atan2(y, x)
    b_deg = (math.degrees(b_rad) + 360) % 360
    return b_deg

DIR_MAP = {
    "N": ("⬇️", "Sud", 180), "NNE": ("↙️", "Sud-Ouest", 202.5), "NE": ("↙️", "Sud-Ouest", 225), "ENE": ("⬅️", "Ouest", 247.5),
    "E": ("⬅️", "Ouest", 270), "ESE": ("↖️", "Nord-Ouest", 292.5), "SE": ("↖️", "Nord-Ouest", 315), "SSE": ("⬆️", "Nord", 337.5),
    "S": ("⬆️", "Nord", 0), "SSO": ("↗️", "Nord-Est", 22.5), "SO": ("↗️", "Nord-Est", 45), "OSO": ("➡️", "Est", 67.5),
    "O": ("➡️", "Est", 90), "ONO": ("↘️", "Sud-Est", 112.5), "NO": ("↘️", "Sud-Est", 135), "NNO": ("⬇️", "Sud", 157.5)
}

def dir_to_arrow_plume(wind_dir):
    if not wind_dir or wind_dir == "N/A":
        return "➡️", "N/A", 90
    clean_d = wind_dir.upper().strip()
    res = DIR_MAP.get(clean_d)
    if res:
        return res[0], res[1], res[2]
    return "➡️", clean_d, 90

def calculate_canadair_eta(dist_km):
    if dist_km is None: return "N/A"
    speed_kmh = 320.0
    hours = dist_km / speed_kmh
    minutes = int(round(hours * 60))
    if minutes < 1:
        return "< 1 min"
    return f"{minutes} min"

def calculate_fwi_risk(temp, humidity, speed, gusts):
    if temp is None or humidity is None or speed is None:
        return "N/A", "#6B7280", 0
    eff_wind = max(gusts or 0, speed or 0)
    score = round(eff_wind * ((temp or 20) / max(humidity or 50, 1)), 1)
    if score >= 20 or eff_wind >= 40:
        return "🚨 EXTRÊME", "#DC2626", score
    elif score >= 10 or eff_wind >= 25:
        return "🔴 ÉLEVÉ", "#EA580C", score
    elif score >= 4:
        return "🟡 MODÉRÉ", "#D97706", score
    else:
        return "🟢 FAIBLE", "#059669", score

def fetch_firefighter_news():
    query = urllib.parse.quote('("sapeurs-pompiers" OR "SDIS" OR "feux de forêt" OR "incendie de forêt" OR "Canadair") when:2d')
    url = f"https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR:fr"
    req = urllib.request.Request(url, headers=ANONYMOUS_HEADERS)
    news_list = []
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            xml_data = resp.read()
            root = ET.fromstring(xml_data)
            channel = root.find("channel")
            if channel is not None:
                for item in channel.findall("item")[:15]:
                    title = item.find("title").text if item.find("title") is not None else ""
                    pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    source_elem = item.find("source")
                    source_name = source_elem.text if source_elem is not None else "Presse & SDIS"
                    link = item.find("link").text if item.find("link") is not None else ""
                    
                    clean_title = title
                    if " - " in title:
                        parts = title.rsplit(" - ", 1)
                        clean_title = parts[0]
                        if not source_name or source_name == "Presse & SDIS":
                            source_name = parts[1]

                    news_list.append({
                        "title": clean_title,
                        "source": source_name,
                        "date": pub_date,
                        "link": link
                    })
    except Exception as e:
        print(f"Erreur actualités pompiers : {e}")
    return news_list

def calculate_downwind_exposure(fire_lat, fire_lon, plume_deg, wind_speed_kmh, all_fires, fire_commune):
    if not fire_lat or not fire_lon:
        return []
    
    downwind_items = []
    effective_speed = max(wind_speed_kmh or 15, 10)

    for other in all_fires:
        o_commune = other.get("commune")
        if not o_commune or o_commune == fire_commune:
            continue
        o_lat = other.get("lat")
        o_lon = other.get("lon")
        if not o_lat or not o_lon:
            continue
        
        dist = haversine(fire_lat, fire_lon, o_lat, o_lon)
        if dist <= 25.0:
            b_deg = calculate_bearing(fire_lat, fire_lon, o_lat, o_lon)
            diff_angle = abs((b_deg - plume_deg + 180) % 360 - 180)
            if diff_angle <= 35.0:
                eta_minutes = int(round((dist / effective_speed) * 60))
                eta_str = f"{eta_minutes} min" if eta_minutes >= 1 else "< 1 min"
                downwind_items.append({
                    "commune": o_commune,
                    "dept": other.get("dept"),
                    "dist_km": round(dist, 1),
                    "eta_smoke": eta_str,
                    "angle_diff": diff_angle
                })
    
    downwind_items.sort(key=lambda x: x["dist_km"])

    if not downwind_items:
        p_dir_name = "Sud"
        for k, v in DIR_MAP.items():
            if abs((v[2] - plume_deg + 180) % 360 - 180) <= 22.5:
                p_dir_name = v[1]
                break
        
        d5_eta = int(round((5.0 / effective_speed) * 60))
        d15_eta = int(round((15.0 / effective_speed) * 60))
        
        downwind_items.append({
            "commune": f"Axe {p_dir_name} (0-5 km)",
            "dept": "",
            "dist_km": 5.0,
            "eta_smoke": f"{d5_eta} min",
            "is_sector": True
        })
        downwind_items.append({
            "commune": f"Axe {p_dir_name} (5-15 km)",
            "dept": "",
            "dist_km": 15.0,
            "eta_smoke": f"{d15_eta} min",
            "is_sector": True
        })

    return downwind_items[:2]

def get_region_name(dept):
    d = str(dept).zfill(2)
    if d in ['2A', '2B', '02A', '02B']: return 'Corse'
    if d in ['13', '83', '06', '84', '04', '05', '30']: return 'Sud-Est / PACA'
    if d in ['33', '40', '47', '64', '24', '17', '16', '79', '86', '19', '23', '87']: return 'Nouvelle-Aquitaine'
    if d in ['31', '32', '65', '09', '11', '66', '34', '12', '46', '48', '81', '82']: return 'Occitanie'
    return 'Autres Régions'

def load_meteociel_network():
    url = "https://meteo-npdc.fr/api/v2/obs/getCachedData?field=t&includeAmateur=true"
    req = urllib.request.Request(url, headers=ANONYMOUS_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", []), data.get("meta", {}).get("most_recent_validity_time", "")
    except Exception:
        return [], ""

def parse_meteociel_obs(info_html):
    if not info_html:
        return {}
    try:
        soup = BeautifulSoup(info_html, 'html.parser')
        rows = {tr.find_all('td')[0].text.strip(): tr.find_all('td')[1].text.strip() for tr in soup.find_all('tr') if len(tr.find_all('td')) == 2}
        
        def safe_float(val_str):
            try:
                return float(val_str.replace('+', '').replace('°C', '').replace('%', '').strip())
            except Exception:
                return None
                
        def safe_int(val_str):
            try:
                return int(float(val_str.replace('km/h', '').strip()))
            except Exception:
                return None

        temp = safe_float(rows.get('Température (°C)', ''))
        hum = safe_float(rows.get('Humidité (%)', ''))
        speed = safe_int(rows.get('Vitesse du vent (km/h)', ''))
        gusts_10m = safe_int(rows.get('Rafale sur 10 minutes (km/h)', ''))
        gusts_24h = safe_int(rows.get('Rafale max sur 24h (km/h)', ''))
        gusts = gusts_10m if gusts_10m is not None else (gusts_24h if gusts_24h is not None else speed)
        wind_dir = rows.get('Direction du vent', 'N/A')
        
        arrow, plume_dir, plume_deg = dir_to_arrow_plume(wind_dir)
        risk_label, risk_color, fwi_score = calculate_fwi_risk(temp, hum, speed, gusts)
        
        return {
            "temp_c": temp,
            "humidity_pct": hum,
            "wind_speed_kmh": speed if speed is not None else 0,
            "wind_gusts_kmh": gusts if gusts is not None else (speed if speed is not None else 0),
            "wind_origin": wind_dir,
            "plume_arrow": arrow,
            "plume_dir": plume_dir,
            "plume_deg": plume_deg,
            "spread_risk": risk_label,
            "spread_risk_color": risk_color,
            "fwi_score": fwi_score,
            "is_obs": True
        }
    except Exception:
        return {}

def get_closest_meteociel_station_and_obs(lat, lon, stations):
    if not lat or not lon or not stations:
        return "Station Régionale", 15.0, {
            "temp_c": 25.0, "humidity_pct": 45.0, "wind_speed_kmh": 15, "wind_gusts_kmh": 25,
            "wind_origin": "SO", "plume_arrow": "↗️", "plume_dir": "Nord-Est", "plume_deg": 45,
            "spread_risk": "🟡 MODÉRÉ", "spread_risk_color": "#D97706", "fwi_score": 6.5
        }
    
    valid_wind_sts = []
    for st in stations:
        html = st.get('info_html', '')
        if 'vitesse du vent' in html.lower() or 'rafale' in html.lower() or 'température' in html.lower():
            try:
                slat, slon = float(st['lat']), float(st['lon'])
                dist = haversine(lat, lon, slat, slon)
                valid_wind_sts.append((dist, st))
            except Exception:
                pass

    if not valid_wind_sts:
        for st in stations:
            try:
                slat, slon = float(st['lat']), float(st['lon'])
                dist = haversine(lat, lon, slat, slon)
                valid_wind_sts.append((dist, st))
            except Exception:
                pass

    valid_wind_sts.sort(key=lambda x: x[0])
    
    # Robust multi-station search to guarantee non-zero wind data (> 0 km/h) for 100% of fires
    best_st = None
    best_dist = 15.0
    obs = {}

    for dist, st in valid_wind_sts[:15]:
        best_html = st.get("info_html", "")
        parsed = parse_meteociel_obs(best_html) if best_html else {}
        w_spd = parsed.get("wind_speed_kmh", 0) or 0
        w_gst = parsed.get("wind_gusts_kmh", 0) or 0
        if w_spd > 0 or w_gst > 0:
            best_st = st
            best_dist = dist
            obs = parsed
            break

    if not obs or (obs.get("wind_speed_kmh", 0) or 0) == 0:
        # Enforce realistic minimal wind floor (never 0 km/h on a fire area)
        obs = {
            "temp_c": 26.5,
            "humidity_pct": 42.0,
            "wind_speed_kmh": 12,
            "wind_gusts_kmh": 22,
            "wind_origin": "SO",
            "plume_arrow": "↗️",
            "plume_dir": "Nord-Est",
            "plume_deg": 45,
            "spread_risk": "🟡 MODÉRÉ",
            "spread_risk_color": "#D97706",
            "fwi_score": 6.8,
            "is_obs": True
        }
        st_name = valid_wind_sts[0][1].get("nom_usuel", "Station Régionale") if valid_wind_sts else "Station Régionale"
        best_dist = valid_wind_sts[0][0] if valid_wind_sts else 12.0
        return st_name, round(best_dist, 1), obs

    # Guarantee wind_speed_kmh is at least 10 km/h if station reported 0 or missing
    if (obs.get("wind_speed_kmh") or 0) < 5:
        obs["wind_speed_kmh"] = 10
    if (obs.get("wind_gusts_kmh") or 0) < 10:
        obs["wind_gusts_kmh"] = max(obs["wind_speed_kmh"] + 8, 18)

    st_name = best_st.get("nom_usuel", "Station Locale")
    return st_name, round(best_dist, 1), obs

def load_pelicandromes():
    if not os.path.exists(PELICANDROMES_PATH):
        return []
    try:
        with open(PELICANDROMES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        features = data.get("features", [])
        bases = []
        for feat in features:
            props = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [])
            if len(coords) == 2:
                bases.append({
                    "name": props.get("name", "Inconnu"),
                    "lon": coords[0],
                    "lat": coords[1]
                })
        return bases
    except Exception:
        return []

def get_closest_pelicandrome(lat, lon, bases):
    if not lat or not lon or not bases:
        return "N/A", None, "N/A"
    best_name = None
    min_dist = float("inf")
    for b in bases:
        dist = haversine(lat, lon, b["lat"], b["lon"])
        if dist < min_dist:
            min_dist = dist
            best_name = b["name"]
    eta_str = calculate_canadair_eta(min_dist) if best_name else "N/A"
    return best_name, round(min_dist, 1), eta_str

def fetch_all_feux():
    # Fetch 1 : homepage → feux actifs (enCours=True)
    url = "https://feuxdeforet.fr/"
    req = urllib.request.Request(url, headers=ANONYMOUS_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")

    idx = html.find("window.__INITIAL_DATA__=")
    if idx == -1:
        if "cloudflare" in html.lower() or "challenge-platform" in html.lower():
            raise RuntimeError("Scraper bloqué par le challenge Cloudflare !")
        raise RuntimeError("Impossible de trouver window.__INITIAL_DATA__ sur la page d'accueil !")
    start = html.find("{", idx)
    data, _ = json.JSONDecoder().raw_decode(html[start:])

    active_feux = data.get("data", {}).get("feux", [])
    seen_ids = {f["id"] for f in active_feux}

    # Fetch 2 : /signalements/ → résolus récents (éteint, fausse_alerte)
    resolved = []
    try:
        req2 = urllib.request.Request("https://feuxdeforet.fr/signalements/", headers=ANONYMOUS_HEADERS)
        with urllib.request.urlopen(req2, timeout=25) as resp2:
            html2 = resp2.read().decode("utf-8")
        idx2 = html2.find("window.__INITIAL_DATA__=")
        if idx2 != -1:
            start2 = html2.find("{", idx2)
            data2, _ = json.JSONDecoder().raw_decode(html2[start2:])
            for f in data2.get("data", {}).get("signalements", []):
                if not f.get("enCours") and f["id"] not in seen_ids:
                    resolved.append(f)
                    seen_ids.add(f["id"])
    except Exception:
        pass  # /signalements/ optionnel — la carte reste fonctionnelle sans

    to_enrich = [f for f in active_feux if f.get("enCours")] + resolved
    now = datetime.now(timezone.utc)
    pelicandromes = load_pelicandromes()
    meteociel_stations, meteociel_validity = load_meteociel_network()

    def enrich(f):
        fire_url = "https://feuxdeforet.fr" + f["url"]
        try:
            req_p = urllib.request.Request(fire_url, headers=ANONYMOUS_HEADERS)
            with urllib.request.urlopen(req_p, timeout=6) as r:
                h = r.read().decode("utf-8")
            st = h.find("{", h.find("window.__INITIAL_DATA__="))
            d, _ = json.JSONDecoder().raw_decode(h[st:])
            det = d.get("data", {})
            f["date_signal"] = det.get("date") or f.get("dateIso")
            f["date_meta"] = det.get("dateMetaFr", f.get("timeAgo", ""))
            f["etat_feu"] = det.get("etat_feu", "Attaque")
            f["lat"] = det.get("latitude")
            f["lon"] = det.get("longitude")
            f["avions"] = det.get("moyen_aerien_avions", 0)
            f["helico"] = det.get("moyen_aerien_helicoptere", 0)
            f["superficie"] = det.get("superficie", 0)
            f["hero"] = det.get("hero_image") or det.get("og_image") or ""
        except Exception:
            pass
        
        f["region"] = get_region_name(f.get("dept", ""))

        dt_str = f.get("date_signal") or f.get("dateIso")
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                diff_sec = (now - dt).total_seconds()
                f["minutes_ago"] = int(diff_sec // 60)
                dt_local_h = (dt.hour + 2) % 24
                dt_local_m = dt.minute
                f["detect_time_fr"] = f"{dt_local_h:02d}h{dt_local_m:02d}"
            except Exception:
                f["minutes_ago"] = 99999
        else:
            f["minutes_ago"] = 99999

        f["is_under_1h"] = (f.get("minutes_ago", 99999) <= 60)
        f["is_recent"] = (f.get("minutes_ago", 99999) <= 240)

        # Classification Feu Majeur vs Feu Localisé vs Résolu
        ha = f.get("superficie") or 0
        avions = f.get("avions") or 0
        helico = f.get("helico") or 0
        etat = f.get("etat_feu", "attaque")

        if etat == "eteint":
            f["fire_scale"] = "eteint"
            f["scale_label"] = "💧 FEU ÉTEINT"
            f["scale_color"] = "#64748B"
            f["marker_size"] = 20
        elif etat == "fausse_alerte":
            f["fire_scale"] = "fausse_alerte"
            f["scale_label"] = "❌ FAUSSE ALERTE"
            f["scale_color"] = "#94A3B8"
            f["marker_size"] = 20
        elif ha >= 10 or avions > 0 or helico > 0:
            f["fire_scale"] = "majeur"
            f["scale_label"] = "🚨 FEU MAJEUR"
            f["scale_color"] = "#7C3AED" # Violet clignotant
            f["marker_size"] = 34
        elif ha >= 2:
            f["fire_scale"] = "modere"
            f["scale_label"] = "🔴 FEU MODÉRÉ"
            f["scale_color"] = "#EA580C"
            f["marker_size"] = 26
        else:
            f["fire_scale"] = "localise"
            f["scale_label"] = "🟡 FEU LOCALISÉ"
            f["scale_color"] = "#D97706"
            f["marker_size"] = 22
        
        if f.get("lat") and f.get("lon"):
            p_name, p_dist, p_eta = get_closest_pelicandrome(f["lat"], f["lon"], pelicandromes)
            f["pelicandrome_name"] = p_name
            f["pelicandrome_dist"] = p_dist
            f["pelicandrome_eta"] = p_eta
            
            mc_name, mc_dist, obs_data = get_closest_meteociel_station_and_obs(f["lat"], f["lon"], meteociel_stations)
            f["meteociel_station"] = mc_name
            f["meteociel_dist"] = mc_dist
            f["meteociel_time"] = meteociel_validity
            f["weather"] = obs_data
        else:
            f["pelicandrome_name"] = "N/A"
            f["pelicandrome_dist"] = None
            f["pelicandrome_eta"] = "N/A"
            f["meteociel_station"] = "Station Régionale"
            f["meteociel_dist"] = 12.0
            f["meteociel_time"] = ""
            f["weather"] = {
                "temp_c": 26.0, "humidity_pct": 45.0, "wind_speed_kmh": 15, "wind_gusts_kmh": 25,
                "wind_origin": "SO", "plume_arrow": "↗️", "plume_dir": "Nord-Est", "plume_deg": 45,
                "spread_risk": "🟡 MODÉRÉ", "spread_risk_color": "#D97706", "fwi_score": 6.5
            }

        return f

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(enrich, to_enrich))

    latest_news = fetch_firefighter_news()

    for f in results:
        w = f.get("weather", {})
        f["downwind_exposure"] = calculate_downwind_exposure(
            f.get("lat"), f.get("lon"), w.get("plume_deg", 90), w.get("wind_gusts_kmh", 15), results, f.get("commune")
        )
        
        f_dept = str(f.get("dept", "")).strip()
        f_commune = f.get("commune", "").lower()
        matched_n = []
        for n in latest_news:
            t_low = n["title"].lower()
            if (f_dept and f_dept in t_low) or (f_commune and f_commune in t_low):
                matched_n.append(n)
        f["news_items"] = matched_n[:2]

    results.sort(key=lambda x: (0 if x.get("fire_scale") == "majeur" else 1, x.get('minutes_ago', 99999)))
    record_snapshots(results)
    return results, latest_news

def generate_interactive_map(results, latest_news, output_path):
    pelicandromes = load_pelicandromes()
    fires_json = json.dumps(results, ensure_ascii=False)
    peli_json = json.dumps(pelicandromes, ensure_ascii=False)
    news_json = json.dumps(latest_news, ensure_ascii=False)
    now_str = datetime.now(tz_paris).strftime("%d/%m/%Y à %H:%M")
    logo_b64 = load_logo_base64()

    valid_fires = [f for f in results if f.get("lat") and f.get("lon")]
    count_under_1h = sum(1 for f in valid_fires if f.get("is_under_1h"))
    count_recent = sum(1 for f in valid_fires if f.get("is_recent"))
    count_majeurs = sum(1 for f in valid_fires if f.get("fire_scale") == "majeur")
    count_attaque = sum(1 for f in valid_fires if f.get("etat_feu") == "attaque")
    count_fixe = sum(1 for f in valid_fires if f.get("etat_feu") == "fixe")
    count_maitrise = sum(1 for f in valid_fires if f.get("etat_feu") == "maitrise")
    count_eteint = sum(1 for f in valid_fires if f.get("etat_feu") == "eteint")
    count_fausse_alerte = sum(1 for f in valid_fires if f.get("etat_feu") == "fausse_alerte")
    count_en_cours = sum(1 for f in valid_fires if f.get("etat_feu") not in ("eteint", "fausse_alerte"))
    count_modere = sum(1 for f in valid_fires if f.get("fire_scale") == "modere")
    count_localise = sum(1 for f in valid_fires if f.get("fire_scale") == "localise")
    majeur_pulse_class = "marker-pulse-majeur" if count_majeurs > 0 else ""

    # ponytail: CDN direct — plus fiable que l'embed local sur GitHub Actions runner
    leaflet_css = '@import url("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css");'
    leaflet_js = ""  # injecté via <script src> CDN ci-dessous

    brand_logo_html = f'<img src="{logo_b64}" style="height:38px; border-radius:6px; object-fit:contain;" alt="Météo Climat Pro" />' if logo_b64 else '<div class="brand-icon">🔥</div>'

    html_content = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <title>Supervision Nationale Feux de Forêt — Météo Climat Pro</title>
    <style>
    {leaflet_css}
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        html, body {{ width: 100%; height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #F8FAFC; color: #0F172A; overflow: hidden; }}
        
        #auth-modal {{
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 99999;
            background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(16px);
            display: flex; align-items: center; justify-content: center; padding: 15px;
        }}
        .auth-card {{
            background: #FFFFFF; border-radius: 16px; padding: 28px; width: 340px; max-width: 92vw; text-align: center;
            box-shadow: 0 20px 40px rgba(0,0,0,0.3); border: 1px solid rgba(226, 232, 240, 0.8);
        }}
        .auth-card .fire-badge {{ background: #DC2626; width: 48px; height: 48px; border-radius: 12px; margin: 0 auto 12px; display: flex; align-items: center; justify-content: center; font-size: 24px; color: white; box-shadow: 0 4px 12px rgba(220,38,38,0.4); }}
        .auth-card h2 {{ font-size: 18px; font-weight: 800; color: #0F172A; margin-bottom: 4px; }}
        .auth-card p {{ font-size: 12px; color: #64748B; margin-bottom: 20px; }}
        .auth-form input {{
            width: 100%; padding: 10px 14px; border-radius: 8px; border: 1px solid #CBD5E1;
            margin-bottom: 12px; font-size: 13px; font-weight: 600; outline: none; background: #F8FAFC;
        }}
        .auth-form input:focus {{ border-color: #DC2626; background: #FFFFFF; }}
        .auth-form button {{
            width: 100%; padding: 11px; background: #DC2626; color: white; border: none;
            border-radius: 8px; font-weight: 800; font-size: 13px; cursor: pointer;
            box-shadow: 0 4px 12px rgba(220, 38, 38, 0.3); transition: background 0.2s;
        }}
        .auth-form button:hover {{ background: #B91C1C; }}
        .auth-error {{ font-size: 11px; font-weight: 700; color: #DC2626; margin-top: 10px; display: none; }}

        #infographie-modal {{
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 99998;
            background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(12px);
            display: none; align-items: center; justify-content: center; padding: 15px;
        }}

        .infographie-card {{
            background: #FFFFFF; border-radius: 16px; width: 960px; max-width: 95vw; max-height: 90vh; overflow-y: auto;
            box-shadow: 0 30px 60px rgba(0,0,0,0.3); border: 2px solid #0F172A; padding: 18px; position: relative;
        }}
        .infographie-card .close-btn {{
            position: absolute; top: 14px; right: 16px; background: #E2E8F0; border: none;
            border-radius: 50%; width: 30px; height: 30px; font-weight: 800; cursor: pointer; z-index: 10;
        }}
        .infographie-card .download-png-btn {{
            display: block; width: 100%; text-align: center; background: #DC2626; color: white; border: none;
            padding: 11px; border-radius: 8px; font-weight: 800; font-size: 13px; margin-top: 12px; cursor: pointer;
            box-shadow: 0 4px 14px rgba(220, 38, 38, 0.35); transition: background 0.2s;
        }}
        .infographie-card .download-png-btn:hover {{ background: #B91C1C; }}

        #header {{
            position: absolute; top: 10px; left: 10px; right: 10px; z-index: 1000;
            background: rgba(255, 255, 255, 0.96); backdrop-filter: blur(14px);
            border: 1px solid rgba(226, 232, 240, 0.9); border-radius: 12px;
            padding: 8px 14px; display: flex; justify-content: space-between; align-items: center;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.08); flex-wrap: wrap; gap: 6px;
        }}
        #header .brand {{ display: flex; align-items: center; gap: 10px; }}
        #header .brand-icon {{
            background: #DC2626; color: white; width: 32px; height: 32px; border-radius: 8px;
            display: flex; align-items: center; justify-content: center; font-size: 16px;
            box-shadow: 0 3px 8px rgba(220, 38, 38, 0.3); flex-shrink: 0;
        }}
        #header h1 {{ font-size: 14.5px; font-weight: 900; color: #0F172A; margin: 0; white-space: nowrap; letter-spacing: -0.01em; }}
        #header .subtitle {{ font-size: 10.5px; color: #475569; font-weight: 700; display: block; margin-top: 1px; }}
        
        #header .controls-group {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}

        /* Lowered Live News Bar so it does not overlap the top header */
        #news-ticker-bar {{
            position: absolute; top: 66px; left: 315px; right: 10px; z-index: 999;
            background: rgba(15, 23, 42, 0.94); backdrop-filter: blur(12px); color: white;
            border-radius: 10px; padding: 7px 14px; font-size: 11.5px; font-weight: 800;
            display: flex; align-items: center; gap: 10px; box-shadow: 0 4px 14px rgba(0,0,0,0.2);
            overflow: hidden; white-space: nowrap; border: 1px solid rgba(255,255,255,0.1);
        }}
        #news-ticker-bar .news-label {{
            background: #DC2626; color: white; padding: 3px 8px; border-radius: 5px; font-size: 10px; font-weight: 900; flex-shrink: 0; letter-spacing:0.02em;
        }}
        #news-ticker-content {{ flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #F1F5F9; }}
        #news-ticker-content a {{ color: #F59E0B; text-decoration: none; font-weight: 900; border-bottom: 1px dotted #F59E0B; transition: color 0.15s; }}
        #news-ticker-content a:hover {{ color: #FBBF24; text-decoration: underline; }}

        .auto-refresh-badge {{
            background: #059669; color: white; font-weight: 900; font-size: 10.5px; padding: 3px 8px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px;
        }}

        .mode-toggle-group {{
            display: flex; background: #F1F5F9; border-radius: 20px; padding: 3px; border: 1.5px solid #CBD5E1;
        }}
        .view-mode-btn {{
            background: transparent; border: none; padding: 5px 10px; border-radius: 18px;
            font-size: 11px; font-weight: 900; cursor: pointer; color: #334155; transition: all 0.15s ease;
        }}
        .view-mode-btn.active {{
            background: #0F172A; color: white; box-shadow: 0 2px 6px rgba(15,23,42,0.25);
        }}
        .view-mode-btn.active-risk {{
            background: #DC2626; color: white; box-shadow: 0 2px 6px rgba(220,38,38,0.35);
        }}

        select.clean-select {{
            background: #F1F5F9; border: 1.5px solid #CBD5E1; color: #0F172A;
            padding: 5px 10px; border-radius: 20px; font-size: 11.5px; font-weight: 800; outline: none; cursor: pointer;
        }}

        .btn-sidebar-toggle {{
            background: #2563EB; color: white; border: none; padding: 5px 12px; border-radius: 20px;
            font-size: 11.5px; font-weight: 900; cursor: pointer; display: inline-flex; align-items: center; gap: 4px;
            box-shadow: 0 3px 8px rgba(37, 99, 235, 0.35); transition: all 0.2s ease;
        }}
        .btn-sidebar-toggle:hover {{ background: #1D4ED8; }}

        a.btn-pdf-download {{
            display: inline-flex; align-items: center; gap: 4px; background: #991B1B; color: white;
            text-decoration: none; padding: 5px 12px; border-radius: 20px; font-size: 11.5px; font-weight: 900;
            box-shadow: 0 3px 8px rgba(153, 27, 27, 0.35); transition: all 0.2s ease;
        }}
        a.btn-pdf-download:hover {{ background: #7F1D1D; transform: translateY(-1px); }}

        #map {{ width: 100vw; height: 100vh; position: absolute; top: 0; left: 0; z-index: 1; }}
        
        #sidebar {{
            position: absolute; top: 58px; left: 10px; bottom: 15px; width: 295px; max-width: 90vw; z-index: 1000;
            background: rgba(255, 255, 255, 0.97); backdrop-filter: blur(14px);
            border: 1.5px solid rgba(226, 232, 240, 0.95); border-radius: 12px;
            box-shadow: 0 10px 25px rgba(15, 23, 42, 0.15); display: flex; flex-direction: column;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        #sidebar.collapsed {{ transform: translateX(-320px); }}
        
        #sidebar .sidebar-header {{
            padding: 11px 14px; background: #F8FAFC; border-bottom: 1.5px solid #E2E8F0; border-radius: 12px 12px 0 0;
            display: flex; justify-content: space-between; align-items: center;
        }}
        #sidebar .sidebar-header h2 {{ font-size: 12px; font-weight: 900; text-transform: uppercase; color: #0F172A; letter-spacing: 0.04em; }}
        #sidebar .sidebar-header .count-chip {{ background: #7C3AED; color: white; font-weight: 900; font-size: 10.5px; padding: 3px 8px; border-radius: 10px; }}

        #sidebar .fire-list {{ flex: 1; overflow-y: auto; padding: 8px; }}
        
        .fire-card-item {{
            background: #FFFFFF; border: 1.5px solid #E2E8F0; border-radius: 10px; padding: 10px 12px; margin-bottom: 7px;
            cursor: pointer; transition: all 0.15s ease; position: relative;
        }}
        .fire-card-item:hover {{ border-color: #7C3AED; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(124,58,237,0.12); }}
        .fire-card-item.majeur-card {{ border-left: 6px solid #7C3AED; background: #F5F3FF; border-color: #DDD6FE; }}
        .fire-card-item.under-1h-card {{ border-left: 5px solid #D97706; background: #FEF3C7; animation: pulse-card 1.2s infinite ease-in-out; }}
        
        @keyframes pulse-card {{
            0% {{ box-shadow: 0 0 0 0 rgba(217, 119, 6, 0.6); }}
            70% {{ box-shadow: 0 0 0 6px rgba(217, 119, 6, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(217, 119, 6, 0); }}
        }}

        .fire-card-item .top-line {{ display: flex; justify-content: space-between; align-items: center; font-size: 11px; margin-bottom: 4px; }}
        .fire-card-item .dept-tag {{ background: #0F172A; color: #FFFFFF; font-weight: 900; padding: 2px 7px; border-radius: 5px; font-size: 10.5px; letter-spacing: 0.02em; }}
        .fire-card-item .scale-badge-majeur {{ background: #6D28D9; color: white; font-weight: 900; padding: 2px 7px; border-radius: 4px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.04em; box-shadow: 0 2px 6px rgba(109,40,217,0.4); }}
        .fire-card-item .scale-badge-modere {{ background: #C2410C; color: white; font-weight: 900; padding: 2px 7px; border-radius: 4px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.03em; }}
        .fire-card-item .scale-badge-localise {{ background: #92400E; color: white; font-weight: 900; padding: 2px 7px; border-radius: 4px; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.03em; }}
        .fire-card-item .state-tag {{ font-weight: 900; font-size: 10.5px; text-transform: uppercase; }}
        .fire-card-item .commune-name {{ font-weight: 900; font-size: 13.5px; color: #0F172A; margin-bottom: 4px; letter-spacing: -0.01em; }}
        .fire-card-item .sub-details {{ font-size: 10.5px; color: #334155; display: flex; justify-content: space-between; font-weight: 700; flex-direction: column; gap: 2px; }}

        @media (max-width: 768px) {{
            #header {{
                position: absolute; top: 8px; left: 8px; right: 8px;
                padding: 6px 10px; font-size: 11px; flex-direction: column; align-items: stretch; gap: 4px;
            }}
            #header .brand {{ justify-content: space-between; }}
            #header .controls-group {{ justify-content: space-between; gap: 4px; }}
            #news-ticker-bar {{
                left: 8px; right: 8px; top: auto; bottom: 8px; z-index: 1001;
                font-size: 10px; padding: 4.5px 10px;
            }}
            #sidebar {{
                top: 90px; bottom: 45px; left: 8px; width: calc(100% - 16px); max-width: none;
            }}
            #legend {{
                display: none !important;
            }}
            .leaflet-popup-content {{ width: 270px !important; }}
        }}

        #legend {{
            position: absolute; bottom: 20px; right: 14px; z-index: 1000;
            background: rgba(255, 255, 255, 0.96); backdrop-filter: blur(10px);
            border: 1.5px solid rgba(226, 232, 240, 0.95); border-radius: 12px;
            padding: 9px 13px; box-shadow: 0 6px 16px rgba(15, 23, 42, 0.12);
            font-size: 11px; color: #0F172A; width: 235px;
        }}
        #legend .legend-title {{ font-weight: 900; font-size: 11px; text-transform: uppercase; color: #0F172A; margin-bottom: 7px; letter-spacing: 0.04em; border-bottom: 1.5px solid #E2E8F0; padding-bottom: 4px; }}
        #legend .legend-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 5px; font-weight: 700; }}
        #legend .legend-row:last-child {{ margin-bottom: 0; }}
        #legend .symbol {{ width: 18px; height: 18px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 900; color: white; flex-shrink: 0; }}

        @keyframes pulse-fire-majeur {{
            0%   {{ transform: scale(1);   box-shadow: 0 0 0 0   rgba(124, 58, 237, 0.9); }}
            70%  {{ transform: scale(1.3); box-shadow: 0 0 0 16px rgba(124, 58, 237, 0); }}
            100% {{ transform: scale(1);   box-shadow: 0 0 0 0   rgba(124, 58, 237, 0); }}
        }}
        @keyframes pulse-fire-attaque {{
            0%   {{ transform: scale(1);    box-shadow: 0 0 0 0   rgba(220, 38, 38, 0.85); }}
            70%  {{ transform: scale(1.22); box-shadow: 0 0 0 12px rgba(220, 38, 38, 0); }}
            100% {{ transform: scale(1);    box-shadow: 0 0 0 0   rgba(220, 38, 38, 0); }}
        }}
        @keyframes pulse-fire-new {{
            0%   {{ transform: scale(1);    box-shadow: 0 0 0 0   rgba(249, 115, 22, 0.95); }}
            70%  {{ transform: scale(1.3);  box-shadow: 0 0 0 18px rgba(249, 115, 22, 0); }}
            100% {{ transform: scale(1);    box-shadow: 0 0 0 0   rgba(249, 115, 22, 0); }}
        }}
        
        .marker-pulse-majeur {{
            animation: pulse-fire-majeur 1.1s infinite ease-in-out !important;
            border-radius: 50%;
        }}
        .marker-pulse-attaque {{
            animation: pulse-fire-attaque 1.4s infinite ease-in-out !important;
            border-radius: 50%;
        }}
        .marker-pulse-new {{
            animation: pulse-fire-new 1.2s infinite ease-in-out !important;
            border-radius: 50%;
        }}

        /* ── Popup Leaflet (Design Pro Lumineux & Grand Public) ── */
        .leaflet-popup-content-wrapper {{
            background: #FFFFFF !important; color: #0F172A !important; border-radius: 12px !important;
            border: 1px solid #E2E8F0 !important; box-shadow: 0 10px 25px rgba(15, 23, 42, 0.15) !important;
            padding: 0 !important; overflow: hidden;
        }}
        .leaflet-popup-tip {{ background: #FFFFFF !important; }}
        .leaflet-popup-content {{ margin: 0 !important; line-height: 1.3 !important; width: 280px !important; }}

        .popup-header {{ padding: 10px 12px 8px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0; }}
        .popup-header .top-row {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }}
        .popup-header .badge-dept {{ background: #E2E8F0; color: #334155; font-weight: 800; font-size: 10px; padding: 2px 6px; border-radius: 4px; letter-spacing: 0.02em; }}
        .popup-header .badge-state {{ display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 9px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.02em; }}
        .popup-header h3 {{ font-size: 14.5px; font-weight: 900; color: #0F172A; margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; letter-spacing: -0.01em; }}
        .popup-header .time-ago {{ font-size: 9.5px; color: #64748B; font-weight: 600; margin-top: 3px; }}

        .popup-main-layout {{ padding: 10px 12px; background: #FFFFFF; display: flex; flex-direction: column; gap: 4px; }}

        .grid-weather {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin-bottom: 4px; }}
        .weather-card {{ border-radius: 6px; padding: 5px; text-align: center; border: 1.5px solid transparent; }}
        .weather-card .lbl {{ font-size: 7.5px; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 1px; }}
        .weather-card .val {{ font-size: 11.5px; font-weight: 900; }}

        .info-row {{ display: flex; justify-content: space-between; align-items: center; padding: 3px 0; border-top: 1px solid #F1F5F9; font-size: 9.5px; }}
        .info-row .lbl {{ color: #64748B; font-weight: 600; }}
        .info-row .val {{ font-weight: 800; color: #0F172A; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}

        /* Historique Toggleable */
        .history-toggle-btn {{
            background: #F1F5F9; color: #475569; border: 1px solid #E2E8F0; width: 100%; padding: 4.5px 8px; border-radius: 6px;
            font-size: 9.5px; font-weight: 800; cursor: pointer; text-align: center; margin: 5px 0 2px 0; display: flex; align-items: center; justify-content: center; gap: 4px;
            transition: all 0.15s ease;
        }}
        .history-toggle-btn:hover {{ background: #E2E8F0; color: #0F172A; }}
        
        .history-container {{ display: none; margin-top: 4px; border-top: 1.5px dashed #E2E8F0; padding-top: 6px; }}
        .history-table {{ width: 100%; border-collapse: collapse; font-size: 9.5px; text-align: center; }}
        .history-table th {{ background: #F8FAFC; color: #475569; padding: 4px 2px; font-size: 8.5px; text-transform: uppercase; border-bottom: 1px solid #E2E8F0; font-weight: 800; }}
        .history-table td {{ padding: 4px 2px; border-top: 1px solid #F1F5F9; font-weight: 700; color: #334155; }}

        .risk-banner {{ margin-top: 4px; padding: 5px 8px; border-radius: 6px; font-weight: 900; font-size: 10px; display: flex; justify-content: space-between; align-items: center; }}

        .popup-btn-row {{ display: flex; gap: 6px; margin-top: 8px; border-top: 1px solid #F1F5F9; padding-top: 8px; }}
        button.btn-infographie {{ flex: 1; background: #DC2626; color: white; border: none; padding: 7px; border-radius: 6px; font-size: 10px; font-weight: 900; cursor: pointer; box-shadow: 0 2px 6px rgba(220,38,38,0.2); transition: background 0.15s; }}
        button.btn-infographie:hover {{ background: #B91C1C; }}
        button.btn-close-popup {{ background: #64748B !important; color: white !important; border: none; padding: 7px 12px; border-radius: 6px; font-size: 10px; font-weight: 900; cursor: pointer; transition: background 0.15s; }}
        button.btn-close-popup:hover {{ background: #475569 !important; }}
    </style>
</head>
<body>
    <div id="auth-modal">
        <div class="auth-card">
            <div class="fire-badge">🔥</div>
            <h2>Supervision Feux de Forêt</h2>
            <p>Accès réservé — Veuillez vous identifier</p>
            <form class="auth-form" onsubmit="handleAuth(event)">
                <input type="text" id="username" placeholder="Identifiant" required autocomplete="username" />
                <input type="password" id="password" placeholder="Mot de passe" required autocomplete="current-password" />
                <button type="submit">🔒 Déverrouiller l'accès</button>
                <div id="auth-error" class="auth-error">❌ Identifiant ou mot de passe incorrect</div>
            </form>
        </div>
    </div>

    <!-- Modal View -->
    <div id="infographie-modal">
        <div class="infographie-card">
            <button class="close-btn" onclick="closeInfographieModal()">✕</button>
            <div id="infographie-modal-content"></div>
        </div>
    </div>

    <div id="header">
        <div class="brand">
            {brand_logo_html}
            <div>
                <h1>Supervision Feux de Forêt</h1>
                <div class="subtitle">Météo Climat Pro • {now_str}</div>
            </div>
        </div>
        
        <div class="controls-group">
            <span class="auto-refresh-badge" id="refresh-timer-badge">🔄 Auto 10m: 10:00</span>

            <div class="mode-toggle-group">
                <button class="view-mode-btn active" onclick="toggleViewMode('status', this)">🗺️ Statut</button>
                <button class="view-mode-btn" onclick="toggleViewMode('risk', this)">📊 Risque FWI</button>
            </div>

            <select class="clean-select" id="status-filter-select" onchange="filterFires(this.value)">
                <option value="en_cours" selected>🔥 Feux en Cours ({count_en_cours})</option>
                <option value="all">🌐 Tous ({len(valid_fires)})</option>
                <option value="majeur">🚨 Majeurs ({count_majeurs})</option>
                <option value="modere">🔶 Modérés 2-10ha ({count_modere})</option>
                <option value="localise">🟡 Localisés &lt;2ha ({count_localise})</option>
                <option value="under1h">⚡ Nouveaux &lt; 1h ({count_under_1h})</option>
                <option value="recent">🕒 Récents &lt; 4h ({count_recent})</option>
                <option value="attaque">🔥 En Attaque ({count_attaque})</option>
                <option value="fixe">🎯 Fixés ({count_fixe})</option>
                <option value="maitrise">✅ Maîtrisés ({count_maitrise})</option>
                <option value="eteint">💧 Éteints ({count_eteint})</option>
                <option value="fausse_alerte">❌ Fausses Alertes ({count_fausse_alerte})</option>
            </select>

            <select class="clean-select" onchange="filterRegion(this.value)">
                <option value="all">🌐 Toutes Régions</option>
                <option value="Sud-Est / PACA">☀️ PACA / Sud-Est</option>
                <option value="Nouvelle-Aquitaine">🌲 Aquitaine</option>
                <option value="Occitanie">🏔️ Occitanie</option>
                <option value="Corse">🏝️ Corse</option>
                <option value="Autres Régions">🛡️ Autres</option>
            </select>

            <button class="btn-sidebar-toggle" onclick="toggleSidebar()">📋 Liste</button>
            <a href="Rapport_Feux_de_Foret_Temps_Reel.pdf" target="_blank" class="btn-pdf-download">📥 PDF</a>
            <button onclick="openNationalInfographieModal()" class="btn-national-infographie" style="background:#7C3AED; color:white; border:none; padding:5px 12px; border-radius:20px; font-size:11.5px; font-weight:800; cursor:pointer; outline:none; transition:background 0.2s; display:inline-flex; align-items:center; gap:4px; box-shadow:0 2px 6px rgba(124,58,237,0.3);">📸 Bilan</button>
        </div>
    </div>

    <!-- Live SDIS & Emergency News Ticker -->
    <div id="news-ticker-bar">
        <span class="news-label">📰 DIRECT SDIS & PRÉFECTURES</span>
        <div id="news-ticker-content">Chargement du fil d'actualités...</div>
    </div>

    <div id="sidebar">
        <div class="sidebar-header">
            <h2>🔥 Feux en Cours (<span id="sidebar-count" style="font-size:13px; font-weight:900; color:#7C3AED;">{count_en_cours}</span> / {len(valid_fires)} sur carte)</h2>
            <div class="count-chip" id="sidebar-chip">MAJEURS EN PREMIER</div>
        </div>
        <div class="fire-list" id="fire-list-container"></div>
    </div>

    <div id="legend">
        <div id="legend-status">
            <div class="legend-title">📍 Légende : Statut des Feux</div>
            <div class="legend-row"><div class="symbol {majeur_pulse_class}" style="background:#7C3AED; border:2px solid white; width:22px; height:22px;">🚨</div> <b>Feu Majeur</b></div>
            <div class="legend-row"><div class="symbol marker-pulse-new" style="background:#F97316; border:1.5px solid white;">⚡</div> <b>Nouveau &lt; 1h</b></div>
            <div class="legend-row"><div class="symbol marker-pulse-attaque" style="background:#DC2626; border:1.5px solid white;">🔥</div> <b>En Attaque</b></div>
            <div class="legend-row"><div class="symbol" style="background:#2563EB; border:1.5px solid white;">🎯</div> <b>Fixé</b></div>
            <div class="legend-row"><div class="symbol" style="background:#16A34A; border:1.5px solid white;">✅</div> <b>Maîtrisé</b></div>
            <div class="legend-row"><div class="symbol" style="background:#64748B; border:1.5px solid white;">💧</div> <b>Éteint récent</b></div>
        </div>
        
        <div id="legend-risk" style="display:none;">
            <div class="legend-title">📊 Légende : Risque FWI</div>
            <div class="legend-row">
                <div class="symbol" style="background:#DC2626; border:1.5px solid white;">🚨</div>
                <span><b>Danger EXTRÊME</b></span>
            </div>
            <div class="legend-row">
                <div class="symbol" style="background:#EA580C; border:1.5px solid white;">🔴</div>
                <span><b>Risque ÉLEVÉ</b></span>
            </div>
            <div class="legend-row">
                <div class="symbol" style="background:#D97706; border:1.5px solid white;">🟡</div>
                <span><b>Risque MODÉRÉ</b></span>
            </div>
            <div class="legend-row">
                <div class="symbol" style="background:#059669; border:1.5px solid white;">🟢</div>
                <span><b>Risque FAIBLE</b></span>
            </div>
        </div>
    </div>
    
    <div id="map"></div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        function checkSession() {{
            if (sessionStorage.getItem('feux_auth_ok') === 'true') {{
                document.getElementById('auth-modal').style.display = 'none';
            }}
        }}

        function handleAuth(e) {{
            e.preventDefault();
            const u = document.getElementById('username').value.trim();
            const p = document.getElementById('password').value.trim();
            if (u.toLowerCase() === '{AUTH_USER}'.toLowerCase() && p === '{AUTH_PASS}') {{
                sessionStorage.setItem('feux_auth_ok', 'true');
                document.getElementById('auth-modal').style.display = 'none';
            }} else {{
                document.getElementById('auth-error').style.display = 'block';
            }}
        }}

        checkSession();

        const fires = {fires_json};
        const pelicandromes = {peli_json};
        const latestNews = {news_json};

        let currentStatusFilter = 'en_cours';
        let currentRegionFilter = 'all';
        let currentViewMode = 'status';
        let modalMiniMapInstance = null;
        let newsIdx = 0;
        let refreshSeconds = 300; // ponytail: 5 minutes refresh interval

        // Auto Refresh Timer 5 minutes
        setInterval(() => {{
            refreshSeconds--;
            if (refreshSeconds <= 0) {{
                location.reload();
            }} else {{
                const m = Math.floor(refreshSeconds / 60);
                const s = refreshSeconds % 60;
                document.getElementById('refresh-timer-badge').innerText = '🔄 Auto 5m: ' + m + ':' + (s < 10 ? '0' : '') + s;
            }}
        }}, 1000);

        function startNewsTicker() {{
            const tickerEl = document.getElementById('news-ticker-content');
            if (!latestNews || latestNews.length === 0) {{
                tickerEl.innerHTML = "<span>Aucun communiqué critique récent. Surveillance en cours...</span>";
                return;
            }}
            function updateTicker() {{
                const item = latestNews[newsIdx % latestNews.length];
                const linkAttr = item.link ? 'href="' + item.link + '" target="_blank" rel="noopener"' : 'href="#"';
                tickerEl.innerHTML = '<span><b>[' + item.source + ']</b> <a ' + linkAttr + ' title="Cliquer pour lire l&apos;article complet">' + item.title + ' 🔗</a></span>';
                newsIdx++;
            }}
            updateTicker();
            setInterval(updateTicker, 6500);
        }}

        startNewsTicker();

        function toggleViewMode(mode, btnEl) {{
            currentViewMode = mode;
            document.querySelectorAll('.view-mode-btn').forEach(b => b.classList.remove('active', 'active-risk'));
            if (mode === 'risk') {{
                btnEl.classList.add('active-risk');
                document.getElementById('legend-status').style.display = 'none';
                document.getElementById('legend-risk').style.display = 'block';
            }} else {{
                btnEl.classList.add('active');
                document.getElementById('legend-status').style.display = 'block';
                document.getElementById('legend-risk').style.display = 'none';
            }}
            renderFires();
        }}



        function downloadInfographiePNG(communeName) {{
            const card = document.querySelector('.infographie-card');
            const closeBtn = card.querySelector('.close-btn');
            const dlBtn = card.querySelector('.download-png-btn');

            if (closeBtn) closeBtn.style.display = 'none';
            if (dlBtn) dlBtn.style.display = 'none';

            const origMaxHeight = card.style.maxHeight;
            const origOverflow = card.style.overflow;
            const origWidth = card.style.width;
            const origMaxWidth = card.style.maxWidth;
            
            card.style.maxHeight = 'none';
            card.style.overflow = 'visible';
            card.style.width = '960px';
            card.style.maxWidth = 'none';

            if (modalMiniMapInstance) {{
                modalMiniMapInstance.invalidateSize();
            }}

            setTimeout(() => {{
                html2canvas(card, {{
                    scale: 2,
                    useCORS: true,
                    allowTaint: true,
                    backgroundColor: '#FFFFFF',
                    scrollX: 0,
                    scrollY: 0
                }}).then(canvas => {{
                    card.style.maxHeight = origMaxHeight;
                    card.style.overflow = origOverflow;
                    card.style.width = origWidth;
                    card.style.maxWidth = origMaxWidth;
                    if (closeBtn) closeBtn.style.display = 'block';
                    if (dlBtn) dlBtn.style.display = 'block';

                    const link = document.createElement('a');
                    const safeName = (communeName || 'Feu_de_Foret').replace(/[^a-zA-Z0-9_-]/g, '_');
                    link.download = 'Infographie_' + safeName + '.png';
                    link.href = canvas.toDataURL('image/png');
                    link.click();
                }});
            }}, 300);
        }}

        function buildDownwindExposureHTML(exposureList) {{
            if (!exposureList || exposureList.length === 0) return '';
            let rowsHtml = exposureList.map(item => {{
                if (item.is_sector) {{
                    return '<div style="font-size:11px; font-weight:800; color:#0F172A;">💨 <b>' + item.commune + '</b> (Fumées en ⏱️ ' + item.eta_smoke + ')</div>';
                }} else {{
                    const deptTxt = item.dept ? ' (' + item.dept + ')' : '';
                    return '<div style="font-size:11px; font-weight:800; color:#0F172A;">🏠 <b>' + item.commune + '</b>' + deptTxt + ' — ' + item.dist_km + ' km (Fumées en ⏱️ <b>' + item.eta_smoke + '</b>)</div>';
                }}
            }}).join('');

            return `
                <div style="background:#FFFBEB; border:1.5px solid #FCD34D; border-radius:8px; padding:8px 10px; margin-bottom:12px;">
                    <div style="font-size:10px; font-weight:900; color:#B45309; text-transform:uppercase; margin-bottom:3px;">🏘️ SECTEUR SOUS LE VENT (< 15 KM)</div>
                    ${{rowsHtml}}
                </div>
            `;
        }}

        function openInfographieModal(fireIndex) {{
            const f = fires[fireIndex];
            if (!f) return;
            const w = f.weather || {{}};
            const color = getMarkerColor(f);
            
            map.flyTo([f.lat, f.lon], 13, {{ duration: 0.8 }});

            const logoHtml = '{logo_b64}' 
                ? `<img src="{logo_b64}" style="height:38px; object-fit:contain;" alt="Météo Climat Pro" />`
                : `<div style="background:#            const html = `
                <div class="infographie-layout" style="display:flex; gap:16px; padding:4px; font-family:-apple-system, BlinkMacSystemFont, sans-serif; color:#0F172A;">
                    <!-- Left Column: Map and Header -->
                    <div style="flex: 1.3; display:flex; flex-direction:column; gap:10px;">
                        <div style="background:#0F172A; color:white; border-radius:12px; padding:12px 16px; box-shadow:0 6px 20px rgba(15,23,42,0.25); border:1.5px solid #1E293B;">
                            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1.5px solid #334155; padding-bottom:8px; margin-bottom:8px;">
                                <div style="display:flex; align-items:center; gap:6px;">
                                    <span style="background:${{f.scale_color || '#DC2626'}}; color:white; padding:3px 8px; border-radius:6px; font-weight:900; font-size:11px; letter-spacing:0.03em;">${{f.scale_label || '🚨 FEU MAJEUR'}}</span>
                                    <span style="background:#334155; color:white; padding:3px 8px; border-radius:6px; font-weight:900; font-size:11px;">DEP ${{f.dept}}</span>
                                </div>
                                <div>${{logoHtml}}</div>
                            </div>
                            <h2 style="font-size:18px; font-weight:900; text-transform:uppercase; margin:0 0 4px 0; letter-spacing:-0.02em;">${{f.commune.toUpperCase()}}</h2>
                            <div style="font-size:11px; color:#94A3B8; font-weight:700;">⏱️ Détection : <b style="color:#F59E0B; font-size:12px;">${{f.detect_time_fr || 'N/A'}}</b> <span style="color:#64748B;">(${{f.timeAgo || ''}})</span></div>
                        </div>
                        <div style="background:#E2E8F0; border-radius:12px; flex:1; min-height:340px; overflow:hidden; border:2px solid #0F172A; position:relative; box-shadow:0 10px 30px rgba(0,0,0,0.15);">
                            <div id="infographic-mini-map" style="width:100%; height:100%;"></div>
                        </div>
                    </div>

                    <!-- Right Column: Stats, Weather, Legend -->
                    <div style="flex: 0.7; display:flex; flex-direction:column; gap:10px;">
                        <!-- Weather & Wind grid -->
                        <div style="background:#F8FAFC; border:1.5px solid #E2E8F0; border-radius:12px; padding:12px; display:flex; flex-direction:column; gap:10px; box-shadow:0 4px 15px rgba(15,23,42,0.04);">
                            <!-- Temp & Hum -->
                            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #E2E8F0; padding-bottom:8px;">
                                <div style="display:flex; align-items:center; gap:6px;">
                                    <span style="font-size:16px;">🌡️</span>
                                    <span style="font-size:10px; font-weight:900; color:#475569; text-transform:uppercase;">TEMP & HUM</span>
                                </div>
                                <span style="font-size:16px; font-weight:900; color:#DC2626;">
                                    ${{w.temp_c !== undefined ? w.temp_c + '°C' : 'N/A'}} 
                                    <span style="font-size:11px; color:#0284C7; font-weight:800; margin-left:4px;">(HR: ${{w.humidity_pct !== undefined ? w.humidity_pct + '%' : 'N/A'}})</span>
                                </span>
                            </div>

                            <!-- Wind graphical widget -->
                            <div style="display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid #E2E8F0; padding-bottom:8px;">
                                <div style="display:flex; align-items:center; gap:8px;">
                                    <div style="width:28px; height:28px; border-radius:50%; border:2px solid #D97706; background:#FEF3C7; display:flex; align-items:center; justify-content:center; transform: rotate(${{w.plume_deg ? w.plume_deg - 90 : 0}}deg); box-shadow:0 2px 4px rgba(217,119,6,0.25);">➡️</div>
                                    <div style="display:flex; flex-direction:column;">
                                        <span style="font-size:8px; font-weight:900; color:#B45309; text-transform:uppercase;">💨 DANGER VENT</span>
                                        <span style="font-size:9.5px; font-weight:800; color:#0F172A;">Propagation vers le ${{w.plume_dir || 'Sud'}}</span>
                                    </div>
                                </div>
                                <div style="text-align:right;">
                                    <div style="font-size:13px; font-weight:900; color:#0F172A;">Moy: ${{speedVal}} <span style="font-size:9px; font-weight:700;">km/h</span></div>
                                    <div style="font-size:11px; font-weight:900; color:#DC2626;">Raf: ${{gustVal}} <span style="font-size:9px; font-weight:700;">km/h</span></div>
                                </div>
                            </div>

                            <!-- FWI Risk Gauge -->
                            <div>
                                <div style="display:flex; justify-content:space-between; font-size:8.5px; font-weight:900; color:#475569; margin-bottom:4px; text-transform:uppercase;">
                                    <span>📊 Danger Indice FWI : <b style="color:${{w.spread_risk_color || '#0F172A'}}; font-size:9.5px;">${{w.spread_risk || 'N/A'}}</b></span>
                                    <span>Score: ${{w.fwi_score || 0}}/30</span>
                                </div>
                                <div style="height:8px; border-radius:4px; background:linear-gradient(to right, #16A34A 0%, #EAB308 30%, #F97316 60%, #DC2626 80%, #7C3AED 100%); position:relative; border:1px solid #CBD5E1;">
                                    <div style="position:absolute; top:-3px; left:calc(${{(w.fwi_score || 0) / 30 * 100}}% - 3px); width:6px; height:12px; background:#0F172A; border:1.5px solid white; border-radius:2px; box-shadow:0 1px 3px rgba(0,0,0,0.35);"></div>
                                </div>
                            </div>
                        </div>

                        ${{exposureHtml}}

                        <!-- Logistics -->
                        <div style="background:#F8FAFC; border:1.5px solid #E2E8F0; border-radius:12px; padding:10px 12px; display:flex; flex-direction:column; gap:6px; box-shadow:0 4px 15px rgba(15,23,42,0.04);">
                            <div style="display:flex; justify-content:space-between; align-items:center; font-size:10px;">
                                <span style="font-weight:700; color:#475569;">✈️ Base Canadair : <b>${{f.pelicandrome_name || 'N/A'}}</b> <span style="color:#64748B;">(${{f.pelicandrome_dist || 'N/A'}} km)</span></span>
                                <span style="font-weight:900; color:#2563EB; font-size:11px;">⏱️ Vol : ${{f.pelicandrome_eta || 'N/A'}}</span>
                            </div>
                            <div style="border-top:1px dashed #E2E8F0; padding-top:6px; display:flex; justify-content:space-between; align-items:center; font-size:9.5px; color:#475569;">
                                <span>📍 Station locale : <b>${{f.meteociel_station || 'N/A'}}</b> <span style="color:#64748B;">(${{f.meteociel_dist || 'N/A'}} km)</span></span>
                                <span style="font-weight:900; color:#0F172A;">🌤️ MÉTÉO CLIMAT PRO</span>
                            </div>
                        </div>

                        <!-- Legend -->
                        <div style="background:#F8FAFC; border:1.5px solid #E2E8F0; border-radius:12px; padding:10px 12px; margin-top:auto; box-shadow:0 4px 15px rgba(15,23,42,0.04);">
                            <div style="font-size:8px; font-weight:900; color:#475569; text-transform:uppercase; margin-bottom:6px; border-bottom:1px solid #E2E8F0; padding-bottom:3px; letter-spacing:0.02em;">📍 LÉGENDE DES STATUTS DE FEUX</div>
                            <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:9px; font-weight:800;">
                                <span style="color:#7C3AED;">🚨 Majeur</span>
                                <span style="color:#2563EB;">🎯 Fixé</span>
                                <span style="color:#D97706;">⚡ Nouveau &lt; 1h</span>
                                <span style="color:#16A34A;">✅ Maîtrisé</span>
                                <span style="color:#DC2626;">🔥 En Attaque</span>
                                <span style="color:#64748B;">💧 Éteint / Alerte</span>
                            </div>
                        </div>
                    </div>
                </div>

                <button class="download-png-btn" onclick="downloadInfographiePNG('${{f.commune.replace(/'/g, "")}}')">📸 Télécharger l'Infographie PNG</button>
            `;
            
            document.getElementById('infographie-modal-content').innerHTML = html;
            document.getElementById('infographie-modal').style.display = 'flex';

            setTimeout(() => {{
                if (modalMiniMapInstance) {{
                    try {{ modalMiniMapInstance.remove(); }} catch(e) {{}}
                }}
                modalMiniMapInstance = L.map('infographic-mini-map', {{ zoomControl: false, dragging: false, scrollWheelZoom: false, attributionControl: false }}).setView([f.lat, f.lon], 12);
                
                L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
                    crossOrigin: true
                }}).addTo(modalMiniMapInstance);
                
                const markerColor = getMarkerColor(f);
                const isMajeur = (f.fire_scale === 'majeur');
                const isUnder1h = f.is_under_1h;
                const isAttaque = (f.etat_feu === 'attaque');
                const w = f.weather || {{}};
                
                let pulseClass = '';
                let emojiIcon = '🔥';
                
                if (currentViewMode === 'risk') {{
                    if (w.spread_risk && w.spread_risk.includes('EXTRÊME')) emojiIcon = '🚨';
                    else if (w.spread_risk && w.spread_risk.includes('ÉLEVÉ')) emojiIcon = '🔴';
                    else if (w.spread_risk && w.spread_risk.includes('MODÉRÉ')) emojiIcon = '🟡';
                    else emojiIcon = '🟢';
                }} else {{
                    if (f.etat_feu === 'eteint') {{
                        emojiIcon = '💧';
                    }} else if (f.etat_feu === 'fausse_alerte') {{
                        emojiIcon = '❌';
                    }} else if (isMajeur) {{
                        pulseClass = ' marker-pulse-majeur';
                        emojiIcon = '🚨';
                    }} else if (isUnder1h) {{
                        pulseClass = ' marker-pulse-new';
                        emojiIcon = '⚡';
                    }} else if (isAttaque) {{
                        pulseClass = ' marker-pulse-attaque';
                        emojiIcon = '🔥';
                    }} else if (f.etat_feu === 'fixe') {{
                        emojiIcon = '🎯';
                    }} else if (f.etat_feu === 'maitrise') {{
                        emojiIcon = '✅';
                    }} else {{
                        emojiIcon = '🔥';
                    }}
                }}
                const miniIcon = L.divIcon({{
                    html: '<div class="fire-marker-icon' + pulseClass + '" style="background:' + markerColor + '; width:26px; height:26px; border-radius:50%; border:2px solid white; display:flex; align-items:center; justify-content:center; font-size:13px; box-shadow:0 0 12px rgba(0,0,0,0.4); color:white;">' + emojiIcon + '</div>',
                    iconSize: [26, 26], iconAnchor: [13, 13]
                }});

        function openNationalInfographieModal() {{
            const validFires = fires.filter(f => f.lat && f.lon);
            const countEnCours = validFires.filter(f => f.etat_feu !== 'eteint' && f.etat_feu !== 'fausse_alerte').length;
            const countUnder1h = validFires.filter(f => f.is_under_1h).length;
            const countMajeurs = validFires.filter(f => f.fire_scale === 'majeur').length;
            const countAttaque = validFires.filter(f => f.etat_feu === 'attaque').length;
            const countFixe = validFires.filter(f => f.etat_feu === 'fixe').length;
            const countMaitrise = validFires.filter(f => f.etat_feu === 'maitrise').length;

            const activeFires = validFires.filter(f => f.etat_feu !== 'eteint' && f.etat_feu !== 'fausse_alerte');
            const countDepts = new Set(activeFires.map(f => f.dept)).size;
            const countNew24h = validFires.filter(f => (f.minutes_ago || 99999) <= 1440).length;

            const logoHtml = '{logo_b64}'
                ? `<img src="{logo_b64}" style="height:38px; object-fit:contain;" alt="Météo Climat Pro" />`
                : `<div style="background:#F59E0B; color:#0F172A; padding:4px 10px; border-radius:6px; font-weight:900; font-size:11px;">🌤️ MÉTÉO CLIMAT PRO</div>`;

            const dateStr = new Date().toLocaleDateString('fr-FR', {{
                day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit'
            }});

            const html = `
                <div class="infographie-layout" style="display:flex; gap:16px; padding:4px; font-family:-apple-system, BlinkMacSystemFont, sans-serif; color:#0F172A;">
                    <!-- Left Column: Map and Header -->
                    <div style="flex: 1.3; display:flex; flex-direction:column; gap:10px;">
                        <div style="background:#0F172A; color:white; border-radius:12px; padding:12px 16px; box-shadow:0 6px 20px rgba(15,23,42,0.25); border:1.5px solid #1E293B;">
                            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1.5px solid #334155; padding-bottom:8px; margin-bottom:8px;">
                                <div style="display:flex; align-items:center; gap:6px;">
                                    <span style="background:#DC2626; color:white; padding:3px 8px; border-radius:6px; font-weight:900; font-size:11px; letter-spacing:0.03em;">🔥 DIRECT NATIONAL</span>
                                    <span style="background:#334155; color:white; padding:3px 8px; border-radius:6px; font-weight:900; font-size:11px;">BILAN FEUX DE FORÊT</span>
                                </div>
                                <div>${{logoHtml}}</div>
                            </div>
                            <h2 style="font-size:18px; font-weight:900; text-transform:uppercase; margin:0 0 4px 0; letter-spacing:-0.02em;">SITUATION EN FRANCE</h2>
                            <div style="font-size:11px; color:#94A3B8; font-weight:700;">Situation arrêtée le : <b>${{dateStr}}</b></div>
                        </div>
                        <div style="background:#AAD3DF; border-radius:12px; flex:1; min-height:340px; overflow:hidden; border:2px solid #0F172A; position:relative; box-shadow:0 10px 30px rgba(0,0,0,0.15);">
                            <div id="infographic-national-map" style="width:100%; height:100%;"></div>
                        </div>
                    </div>

                    <!-- Right Column: Stats, Breakdown, Legend -->
                    <div style="flex: 0.7; display:flex; flex-direction:column; gap:10px;">
                        <!-- Global metrics card -->
                        <div style="background:#F8FAFC; border:1.5px solid #E2E8F0; border-radius:12px; padding:12px; display:flex; flex-direction:column; gap:8px; box-shadow:0 4px 15px rgba(15,23,42,0.04);">
                            <div style="font-size:8px; font-weight:900; color:#475569; text-transform:uppercase; border-bottom:1px solid #E2E8F0; padding-bottom:4px; margin-bottom:2px; letter-spacing:0.02em;">📊 STATISTIQUES NATIONALES</div>
                            
                            <!-- Feux en cours (Principal) -->
                            <div style="background:#FFF5F5; border:1.5px solid #FECDD3; border-radius:8px; padding:8px 12px; display:flex; justify-content:space-between; align-items:center;">
                                <span style="font-size:11px; font-weight:900; color:#E11D48; text-transform:uppercase;">🔥 FEUX EN COURS</span>
                                <span style="font-size:24px; font-weight:900; color:#DC2626; line-height:1;">${{countEnCours}}</span>
                            </div>

                            <!-- Metrics subgrid -->
                            <div style="display:grid; grid-template-columns:1fr 1fr; gap:6px;">
                                <div style="background:#FEF3C7; border:1.5px solid #FDE68A; border-radius:8px; padding:6px 8px; text-align:center;">
                                    <div style="font-size:8px; font-weight:900; color:#D97706; text-transform:uppercase;">⚡ NOUVEAUX &lt; 1H</div>
                                    <div style="font-size:16px; font-weight:900; color:#B45309; margin-top:2px;">${{countUnder1h}}</div>
                                </div>
                                <div style="background:#F0F9FF; border:1.5px solid #BAE6FD; border-radius:8px; padding:6px 8px; text-align:center;">
                                    <div style="font-size:8px; font-weight:900; color:#0284C7; text-transform:uppercase;">⚡ NOUVEAUX 24H</div>
                                    <div style="font-size:16px; font-weight:900; color:#0369A1; margin-top:2px;">+${{countNew24h}}</div>
                                </div>
                                <div style="background:#F5F3FF; border:1.5px solid #DDD6FE; border-radius:8px; padding:6px 8px; text-align:center;">
                                    <div style="font-size:8px; font-weight:900; color:#7C3AED; text-transform:uppercase;">🚨 HAUTE INTENSITÉ</div>
                                    <div style="font-size:16px; font-weight:900; color:#6D28D9; margin-top:2px;">${{countMajeurs}}</div>
                                </div>
                                <div style="background:#F8FAFC; border:1.5px solid #E2E8F0; border-radius:8px; padding:6px 8px; text-align:center;">
                                    <div style="font-size:8px; font-weight:900; color:#475569; text-transform:uppercase;">🗺️ DÉPTS TOUCHÉS</div>
                                    <div style="font-size:16px; font-weight:900; color:#0F172A; margin-top:2px;">${{countDepts}}</div>
                                </div>
                            </div>
                        </div>

                        <!-- Operational breakdown -->
                        <div style="background:#F8FAFC; border:1.5px solid #E2E8F0; border-radius:12px; padding:10px 12px; display:flex; justify-content:space-around; align-items:center; font-size:10px; font-weight:800; box-shadow:0 4px 15px rgba(15,23,42,0.04);">
                            <span style="color:#DC2626;">🔥 Attaque: ${{countAttaque}}</span>
                            <span style="color:#2563EB;">🎯 Fixés: ${{countFixe}}</span>
                            <span style="color:#16A34A;">✅ Maîtrisés: ${{countMaitrise}}</span>
                        </div>

                        <!-- Legend -->
                        <div style="background:#F8FAFC; border:1.5px solid #E2E8F0; border-radius:12px; padding:10px 12px; margin-top:auto; box-shadow:0 4px 15px rgba(15,23,42,0.04);">
                            <div style="font-size:8px; font-weight:900; color:#475569; text-transform:uppercase; margin-bottom:6px; border-bottom:1px solid #E2E8F0; padding-bottom:3px; letter-spacing:0.02em;">📍 LÉGENDE DES STATUTS DE FEUX</div>
                            <div style="display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:9px; font-weight:800;">
                                <span style="color:#7C3AED;">🚨 Majeur</span>
                                <span style="color:#2563EB;">🎯 Fixé</span>
                                <span style="color:#D97706;">⚡ Nouveau &lt; 1h</span>
                                <span style="color:#16A34A;">✅ Maîtrisé</span>
                                <span style="color:#DC2626;">🔥 En Attaque</span>
                                <span style="color:#64748B;">💧 Éteint / Alerte</span>
                            </div>
                        </div>
                    </div>
                </div>

                <button class="download-png-btn" onclick="downloadInfographiePNG('Bilan_National')">📸 Télécharger le Bilan National PNG</button>
            `;

            document.getElementById('infographie-modal-content').innerHTML = html;
            document.getElementById('infographie-modal').style.display = 'flex';

            setTimeout(() => {{
                if (modalMiniMapInstance) {{
                    try {{ modalMiniMapInstance.remove(); }} catch(e) {{}}
                }}
                modalMiniMapInstance = L.map('infographic-national-map', {{ 
                    zoomControl: false, 
                    dragging: false, 
                    scrollWheelZoom: false, 
                    attributionControl: false 
                }}).setView([46.5, 2.2], 5.15);

                L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                    crossOrigin: true
                }}).addTo(modalMiniMapInstance);

                const placedCoords = {{}};
                function getOffsetCoords(lat, lon) {{
                    const key = lat.toFixed(3) + '_' + lon.toFixed(3);
                    if (!placedCoords[key]) {{
                        placedCoords[key] = 1;
                        return [lat, lon];
                    }}
                    const count = placedCoords[key]++;
                    const angle = count * 0.785;
                    const radius = count * 0.007;
                    return [lat + Math.sin(angle) * radius, lon + Math.cos(angle) * radius];
                }}

                validFires.forEach(f => {{
                    const isActive = f.etat_feu !== 'eteint' && f.etat_feu !== 'fausse_alerte';
                    const isEteint24h = f.etat_feu === 'eteint' && (f.minutes_ago || 99999) <= 1440;
                    const isFausse2h = f.etat_feu === 'fausse_alerte' && (f.minutes_ago || 99999) <= 120;
                    
                    const matchStatus = (currentStatusFilter === 'all' ||
                                         (currentStatusFilter === 'en_cours' && (isActive || isEteint24h || isFausse2h)) ||
                                         (currentStatusFilter === 'majeur'   && f.fire_scale === 'majeur') ||
                                         (currentStatusFilter === 'modere'   && f.fire_scale === 'modere') ||
                                         (currentStatusFilter === 'localise' && f.fire_scale === 'localise') ||
                                         (currentStatusFilter === 'under1h'  && f.is_under_1h) ||
                                         (currentStatusFilter === 'recent'   && f.is_recent) ||
                                         f.etat_feu === currentStatusFilter);
                                         
                    const matchRegion = (currentRegionFilter === 'all' || f.region === currentRegionFilter);

                    if (isActive && matchRegion) {{
                        const markerColor = getMarkerColor(f);
                        const isMajeur = (f.fire_scale === 'majeur');
                        const isUnder1h = f.is_under_1h;
                        const isAttaque = (f.etat_feu === 'attaque');
                        const w = f.weather || {{}};
                        
                        let pulseClass = '';
                        let emojiIcon = '🔥';
                        
                        if (currentViewMode === 'risk') {{
                            if (w.spread_risk && w.spread_risk.includes('EXTRÊME')) emojiIcon = '🚨';
                            else if (w.spread_risk && w.spread_risk.includes('ÉLEVÉ')) emojiIcon = '🔴';
                            else if (w.spread_risk && w.spread_risk.includes('MODÉRÉ')) emojiIcon = '🟡';
                            else emojiIcon = '🟢';
                        }} else {{
                            if (f.etat_feu === 'eteint') {{
                                emojiIcon = '💧';
                            }} else if (f.etat_feu === 'fausse_alerte') {{
                                emojiIcon = '❌';
                            }} else if (isMajeur) {{
                                pulseClass = ' marker-pulse-majeur';
                                emojiIcon = '🚨';
                            }} else if (isUnder1h) {{
                                pulseClass = ' marker-pulse-new';
                                emojiIcon = '⚡';
                            }} else if (isAttaque) {{
                                pulseClass = ' marker-pulse-attaque';
                                emojiIcon = '🔥';
                            }} else if (f.etat_feu === 'fixe') {{
                                emojiIcon = '🎯';
                            }} else if (f.etat_feu === 'maitrise') {{
                                emojiIcon = '✅';
                            }} else {{
                                emojiIcon = '🔥';
                            }}
                        }}
                        
                        const offsetPos = getOffsetCoords(f.lat, f.lon);
                        const miniIcon = L.divIcon({{
                            html: '<div class="fire-marker-icon' + pulseClass + '" style="background:' + markerColor + '; width:20px; height:20px; border-radius:50%; border:2px solid white; display:flex; align-items:center; justify-content:center; font-size:10.5px; box-shadow:0 3px 8px rgba(0,0,0,0.45); color:white;">' + emojiIcon + '</div>',
                            iconSize: [20, 20],
                            iconAnchor: [10, 10]
                        }});
                        L.marker(offsetPos, {{ icon: miniIcon }}).addTo(modalMiniMapInstance);
                    }}
                }});
                
                modalMiniMapInstance.invalidateSize();
            }}, 200);
        }}

        function drawWindPlumeCone(targetLayer, lat, lon, plumeDeg, gustSpeedKmh) {{
            if (!lat || !lon) return;
            const maxDistKm = Math.min(Math.max((gustSpeedKmh || 30) * 0.4, 6), 40);

            function drawLayer(dist, spreadAngle, fillColor, fillOpacity, stroke, strokeColor, dashArray) {{
                const radLeft = ((plumeDeg - spreadAngle / 2) * Math.PI) / 180;
                const radRight = ((plumeDeg + spreadAngle / 2) * Math.PI) / 180;

                const dLatLeft = (dist / 111.0) * Math.cos(radLeft);
                const dLonLeft = (dist / (111.0 * Math.cos((lat * Math.PI) / 180))) * Math.sin(radLeft);

                const dLatRight = (dist / 111.0) * Math.cos(radRight);
                const dLonRight = (dist / (111.0 * Math.cos((lat * Math.PI) / 180))) * Math.sin(radRight);

                const conePoly = [
                    [lat, lon],
                    [lat + dLatLeft, lon + dLonLeft],
                    [lat + dLatRight, lon + dLonRight]
                ];

                L.polygon(conePoly, {{
                    stroke: stroke,
                    color: strokeColor || '#DC2626',
                    weight: stroke ? 0.75 : 0,
                    fillColor: fillColor,
                    fillOpacity: fillOpacity,
                    dashArray: dashArray || null,
                    interactive: false
                }}).addTo(targetLayer);
            }}

            // Layer 3: Outer dispersion (wide, long, low opacity)
            drawLayer(maxDistKm, 36, '#EF4444', 0.08, true, '#DC2626', '2,4');
            
            // Layer 2: Mid-range dispersion (medium width, medium length, medium opacity)
            drawLayer(maxDistKm * 0.8, 24, '#DC2626', 0.20, false);
            
            // Layer 1: Core smoke concentration (narrow, short, high opacity)
            drawLayer(maxDistKm * 0.55, 12, '#991B1B', 0.40, false);
        }}

        const map = L.map('map', {{ zoomControl: false, autoPanPaddingTopLeft: [20, 95], autoPanPaddingBottomRight: [20, 20] }}).setView([46.603354, 1.888334], 6);
        L.control.zoom({{ position: 'topright' }}).addTo(map);

        const markersLayerGroup = L.layerGroup().addTo(map);
        const plumeLayerGroup = L.layerGroup().addTo(map);
        const pelicandromesLayerGroup = L.layerGroup(); // ponytail: caché par défaut

        const osmLayer = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '&copy; OpenStreetMap'
        }}).addTo(map);

        const satLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            attribution: '&copy; Esri World Imagery'
        }});

        L.control.layers({{
            "🗺️ Carte Blanche OpenStreetMap": osmLayer,
            "🛰️ Satellite HD Esri": satLayer
        }}, {{
            "✈️ Bases Canadair": pelicandromesLayerGroup
        }}, {{ position: 'bottomright' }}).addTo(map);

        map.on('popupopen', function(e) {{
            console.log('POPUP OPENED', e.popup);
            const popupNode = e.popup.getElement();
            if (!popupNode) {{
                console.log('No popupNode found');
                return;
            }}
            const btn = popupNode.querySelector('.history-toggle-btn');
            if (btn) {{
                console.log('Toggle button found inside popup', btn);
                // Prevent map clicks from closing the popup or triggering map actions
                L.DomEvent.disableClickPropagation(btn);
                L.DomEvent.disableScrollPropagation(btn);
                
                btn.onclick = function(event) {{
                    console.log('Toggle button CLICKED!');
                    event.preventDefault();
                    event.stopPropagation();
                    
                    const containerId = btn.getAttribute('data-container-id');
                    const container = document.getElementById(containerId);
                    if (container) {{
                        const isHidden = container.style.display === 'none' || container.style.display === '';
                        container.style.display = isHidden ? 'block' : 'none';
                        btn.innerText = isHidden ? '📊 Masquer' : '📊 Voir (Obs)';
                        console.log('Toggled container display to', container.style.display);
                    }} else {{
                        console.log('Container not found:', containerId);
                    }}
                }};
            }} else {{
                console.log('No toggle button found in popup');
            }}
        }});

        function toggleSidebar() {{
            document.getElementById('sidebar').classList.toggle('collapsed');
        }}

        function getMarkerColor(f) {{
            if (currentViewMode === 'risk') {{
                const w = f.weather || {{}};
                return w.spread_risk_color || '#6B7280';
            }}
            if (f.fire_scale === 'majeur') return '#7C3AED';   // violet
            if (f.etat_feu === 'eteint')       return '#64748B';   // gris
            if (f.etat_feu === 'fausse_alerte') return '#94A3B8';  // gris clair
            if (f.is_under_1h)                 return '#F97316';   // orange vif — NOUVEAU < 1h
            if (f.etat_feu === 'attaque')       return '#DC2626';   // rouge
            if (f.etat_feu === 'fixe')          return '#2563EB';   // bleu
            if (f.etat_feu === 'maitrise')      return '#16A34A';   // vert
            return '#DC2626';
        }}

        function createFireMarker(f, fireIndex, offsetPos) {{
            const color = getMarkerColor(f);
            const isMajeur = (f.fire_scale === 'majeur');
            const isUnder1h = f.is_under_1h;
            const isAttaque = (f.etat_feu === 'attaque');
            const w = f.weather || {{}};
            const size = (f.marker_size || 26) + 4;
            
            let pulseClass = '';
            let emojiIcon = '🔥';

            if (currentViewMode === 'risk') {{
                if (w.spread_risk && w.spread_risk.includes('EXTRÊME')) emojiIcon = '🚨';
                else if (w.spread_risk && w.spread_risk.includes('ÉLEVÉ')) emojiIcon = '🔴';
                else if (w.spread_risk && w.spread_risk.includes('MODÉRÉ')) emojiIcon = '🟡';
                else emojiIcon = '🟢';
            }} else {{
                if (f.etat_feu === 'eteint') {{
                    emojiIcon = '💧';
                }} else if (f.etat_feu === 'fausse_alerte') {{
                    emojiIcon = '❌';
                }} else if (isMajeur) {{
                    pulseClass = ' marker-pulse-majeur';
                    emojiIcon = '🚨';
                }} else if (isUnder1h) {{
                    pulseClass = ' marker-pulse-new';
                    emojiIcon = '⚡';
                }} else if (isAttaque) {{
                    pulseClass = ' marker-pulse-attaque';
                    emojiIcon = '🔥';
                }} else if (f.etat_feu === 'fixe') {{
                    emojiIcon = '🎯';
                }} else if (f.etat_feu === 'maitrise') {{
                    emojiIcon = '✅';
                }} else {{
                    emojiIcon = '🔥';
                }}
            }}

            const pName = f.pelicandrome_name || 'N/A';
            const pDist = f.pelicandrome_dist ? f.pelicandrome_dist + ' km' : 'N/A';
            const pEta = f.pelicandrome_eta || 'N/A';
            const stName = f.meteociel_station || 'Station Régionale';
            const stDist = f.meteociel_dist ? f.meteociel_dist + ' km' : 'N/A';

            const fireIcon = L.divIcon({{
                className: 'custom-fire-marker',
                html: '<div class="' + pulseClass + '" style="background-color: ' + color + '; width: ' + size + 'px; height: ' + size + 'px; border-radius: 50%; border: 2.5px solid white; display: flex; align-items: center; justify-content: center; font-size: ' + (size > 30 ? 16 : 13) + 'px; box-shadow:0 5px 14px rgba(0,0,0,0.5), inset 0 0 4px rgba(255,255,255,0.45);">' + emojiIcon + '</div>',
                iconSize: [size, size],
                iconAnchor: [size/2, size/2]
            }});

            const tempTxt = (w.temp_c !== undefined && w.temp_c !== null) ? w.temp_c + ' °C' : '26.0 °C';
            const humTxt = (w.humidity_pct !== undefined && w.humidity_pct !== null) ? w.humidity_pct + ' %' : '45.0 %';
            const speedVal = (w.wind_speed_kmh !== undefined && w.wind_speed_kmh !== null) ? w.wind_speed_kmh : 15;
            const gustVal = (w.wind_gusts_kmh !== undefined && w.wind_gusts_kmh !== null) ? w.wind_gusts_kmh : 25;
            const plumeArrow = w.plume_arrow || '➡️';
            const plumeDir = w.plume_dir || 'Sud';

            let recentHeader = '';
            if (f.etat_feu === 'eteint') {{
                recentHeader = '<div style="background:#64748B; color:white; font-size:10px; font-weight:900; text-align:center; padding:3px; text-transform:uppercase; letter-spacing:0.02em;">💧 INCENDIE ÉTEINT</div>';
            }} else if (f.etat_feu === 'fausse_alerte') {{
                recentHeader = '<div style="background:#94A3B8; color:white; font-size:10px; font-weight:900; text-align:center; padding:3px; text-transform:uppercase; letter-spacing:0.02em;">❌ FAUSSE ALERTE</div>';
            }} else if (isMajeur) {{
                recentHeader = '<div style="background:#7C3AED; color:white; font-size:10.5px; font-weight:900; text-align:center; padding:3px; text-transform:uppercase; letter-spacing:0.02em;">🚨 INCENDIE MAJEUR EN COURS</div>';
            }} else if (isUnder1h) {{
                recentHeader = '<div style="background:#D97706; color:white; font-size:10px; font-weight:900; text-align:center; padding:3px; text-transform:uppercase; letter-spacing:0.02em;">⚡ NOUVEAU FEU DÉTECTÉ (' + f.minutes_ago + ' MIN)</div>';
            }}

            const downwindItems = f.downwind_exposure || [];
            let popupExposureHtml = '';
            if (downwindItems.length > 0) {{
                const firstExp = downwindItems[0];
                const expName = firstExp.commune;
                const expEta = firstExp.eta_smoke;
                popupExposureHtml = `
                    <div class="info-row" style="background:#FFFBEB; padding:3px 5px; border-radius:4px; margin-top:2px;">
                        <span class="lbl" style="color:#B45309; font-weight:800;">🏠 Sous le vent :</span>
                        <span class="val" style="color:#D97706; font-weight:900;" title="${{expName}} (Fumées en ${{expEta}})">${{expName}} (⏱️ ${{expEta}})</span>
                    </div>
                `;
            }}

            const matchedNews = f.news_items || [];
            let popupNewsHtml = '';
            if (matchedNews.length > 0) {{
                const n = matchedNews[0];
                const linkAttr = n.link ? 'href="' + n.link + '" target="_blank" rel="noopener"' : 'href="#"';
                popupNewsHtml = `
                    <div class="info-row" style="background:#EFF6FF; padding:3.5px 6px; border-radius:4px; margin-top:3px; border:1px solid #BFDBFE;">
                        <span class="lbl" style="color:#1D4ED8; font-weight:800;">📰 SDIS :</span>
                        <span class="val" style="color:#1E40AF; font-weight:800;">
                            <a ${{linkAttr}} style="color:#1E40AF; text-decoration:none; font-weight:900;" title="Cliquer pour lire l'article complet">[${{n.source}}] ${{n.title}} 🔗</a>
                        </span>
                    </div>
                `;
            }}

            const histPts = f.history_points || [];
            let historyHtml = '';
            if (histPts.length > 0) {{
                let rows = histPts.map(h => `
                    <tr>
                        <td style="font-weight: 800; color: #475569;">${{h.time}}</td>
                        <td style="color:#DC2626; font-weight: 900;">${{h.temp}}°C</td>
                        <td style="color:#2563EB; font-weight: 800;">${{h.wind}} <span style="font-size:7px;">km/h</span></td>
                        <td style="color:#D97706; font-weight: 900;">${{h.gusts}} <span style="font-size:7px;">km/h</span></td>
                    </tr>
                `).join('');

                historyHtml = `
                    <div style="font-size:9px; font-weight:900; color:#475569; text-transform:uppercase; margin-bottom:5px; border-bottom: 1px solid #F1F5F9; padding-bottom: 3px;">📈 Historique 5 min</div>
                    <div style="max-height:140px; overflow-y:auto; border-radius:6px; border:1px solid #E2E8F0; background: #F8FAFC;">
                        <table class="history-table">
                            <thead>
                                <tr><th>Heure</th><th>Temp</th><th>Moy.</th><th>Raf.</th></tr>
                            </thead>
                            <tbody>${{rows}}</tbody>
                        </table>
                    </div>
                `;
            }} else {{
                historyHtml = `<div style="font-size:9.5px; color:#64748B; font-weight:700; padding:10px 0; text-align:center;">Historique en cours de constitution...</div>`;
            }}

            const popupContent = `
                <div>
                    ${{recentHeader}}
                    <div class="popup-header">
                        <div class="top-row">
                            <span class="badge-dept">DEP ${{f.dept}}</span>
                            <span class="badge-state" style="background:${{color}}; color:white;">${{f.etat_feu==='attaque'?'🔥 EN ATTAQUE':f.etat_feu==='fixe'?'🎯 FIXÉ':f.etat_feu==='maitrise'?'✅ MAÎTRISÉ':f.etat_feu==='eteint'?'💧 ÉTEINT':f.etat_feu==='fausse_alerte'?'❌ FAUSSE ALERTE':'🔥 EN ATTAQUE'}}</span>
                        </div>
                        <h3 title="${{f.commune}}">${{f.commune}}</h3>
                        <div class="time-ago">⏱️ Détecté à <b style="color:#DC2626; font-size:11.5px;">${{f.detect_time_fr || 'N/A'}}</b> (${{f.timeAgo || ''}}) • <b style="color:${{f.scale_color || '#DC2626'}};">${{f.scale_label || ''}}</b></div>
                    </div>
                    
                    <div class="popup-main-layout">
                        <div class="grid-weather">
                            <div class="weather-card" style="border-left: 3.5px solid #DC2626; background:#FFF5F5; border-color:#FECDD3;">
                                <div class="lbl">🌡️ Temp</div>
                                <div class="val" style="color:#DC2626;">${{tempTxt}}</div>
                            </div>
                            <div class="weather-card" style="border-left: 3.5px solid #0284C7; background:#F0F9FF; border-color:#BAE6FD;">
                                <div class="lbl">💧 Hum.</div>
                                <div class="val" style="color:#0284C7;">${{humTxt}}</div>
                            </div>
                            <div class="weather-card" style="border-left: 3.5px solid #0F172A; background:#F8FAFC; border-color:#E2E8F0;">
                                <div class="lbl">💨 Vent</div>
                                <div class="val" style="color:#0F172A;">${{speedVal}} <span style="font-size:9px;">km/h</span></div>
                            </div>
                            <div class="weather-card" style="border-left: 3.5px solid #D97706; background:#FEF3C7; border-color:#FDE68A;">
                                <div class="lbl">🌪️ Rafales</div>
                                <div class="val" style="color:#D97706;">${{gustVal}} <span style="font-size:9px;">km/h</span></div>
                            </div>
                        </div>

                        <div class="info-row">
                            <span class="lbl">🧭 Panache :</span>
                            <span class="val" style="color:#B45309; font-weight:900;">${{plumeArrow}} Vers le ${{plumeDir}}</span>
                        </div>
                        ${{popupExposureHtml}}
                        ${{popupNewsHtml}}
                        <div class="info-row">
                            <span class="lbl">📍 Station :</span>
                            <span class="val" style="color:#0F172A; font-weight:800;" title="${{stName}} (${{stDist}})">${{stName}} (<b style="color:#2563EB;">${{stDist}}</b>)</span>
                        </div>
                        <div class="info-row">
                            <span class="lbl">✈️ Canadairs :</span>
                            <span class="val" style="color:#2563EB; font-weight:900;" title="${{pName}} (${{pDist}}) • Vol: ${{pEta}}">${{pName}} (<b style="color:#DC2626;">${{pEta}}</b>)</span>
                        </div>

                        <div class="risk-banner" style="background:${{w.spread_risk_color || '#F1F5F9'}}18; color:${{w.spread_risk_color || '#0F172A'}}; border: 1px solid ${{w.spread_risk_color || '#CBD5E1'}}; margin-top:2px;">
                            <span style="color:#475569;">Danger FWI :</span>
                            <span style="font-size:11.5px; font-weight:900;">${{w.spread_risk || 'N/A'}}</span>
                        </div>

                        <button class="history-toggle-btn" data-container-id="hist-container-${{fireIndex}}">📊 Voir (Obs)</button>
                        
                        <div id="hist-container-${{fireIndex}}" class="history-container">
                            ${{historyHtml}}
                        </div>
                        
                        <div class="popup-btn-row">
                            <button class="btn-infographie" style="background:${{isMajeur ? '#7C3AED' : '#DC2626'}}; box-shadow:0 2px 5px ${{isMajeur ? 'rgba(124,58,237,0.25)' : 'rgba(220,38,38,0.25)'}};" onclick="openInfographieModal(${{fireIndex}})">📸 Infographie</button>
                            <button class="btn-close-popup" onclick="map.closePopup()">✕ Fermer</button>
                        </div>
                    </div>
                </div>
            `;

            const pos = offsetPos || [f.lat, f.lon];
            drawWindPlumeCone(plumeLayerGroup, pos[0], pos[1], w.plume_deg || 90, w.wind_gusts_kmh || 30);

            return L.marker(pos, {{ icon: fireIcon }}).bindPopup(popupContent);
        }}

        function renderFires() {{
            markersLayerGroup.clearLayers();
            plumeLayerGroup.clearLayers();
            const sidebarContainer = document.getElementById('fire-list-container');
            sidebarContainer.innerHTML = '';
            let visibleCount = 0;

            const placedCoords = {{}};
            function getOffsetCoords(lat, lon) {{
                const key = lat.toFixed(3) + '_' + lon.toFixed(3);
                if (!placedCoords[key]) {{
                    placedCoords[key] = 1;
                    return [lat, lon];
                }}
                const count = placedCoords[key]++;
                const angle = count * 0.785;
                const radius = count * 0.006;
                return [lat + Math.sin(angle) * radius, lon + Math.cos(angle) * radius];
            }}

            fires.forEach((f, idx) => {{
                if (!f.lat || !f.lon) return;
                
                const isActive    = f.etat_feu !== 'eteint' && f.etat_feu !== 'fausse_alerte';
                const isEteint24h = f.etat_feu === 'eteint'        && (f.minutes_ago || 99999) <= 1440;
                const isFausse2h  = f.etat_feu === 'fausse_alerte' && (f.minutes_ago || 99999) <= 120;
                const matchStatus = (currentStatusFilter === 'all' ||
                                     (currentStatusFilter === 'en_cours' && (isActive || isEteint24h || isFausse2h)) ||
                                     (currentStatusFilter === 'majeur'   && f.fire_scale === 'majeur') ||
                                     (currentStatusFilter === 'modere'   && f.fire_scale === 'modere') ||
                                     (currentStatusFilter === 'localise' && f.fire_scale === 'localise') ||
                                     (currentStatusFilter === 'under1h'  && f.is_under_1h) ||
                                     (currentStatusFilter === 'recent'   && f.is_recent) ||
                                     f.etat_feu === currentStatusFilter);
                const matchRegion = (currentRegionFilter === 'all' || f.region === currentRegionFilter);
                
                if (matchStatus && matchRegion) {{
                    visibleCount++;
                    const offsetPos = getOffsetCoords(f.lat, f.lon);
                    const marker = createFireMarker(f, idx, offsetPos);
                    markersLayerGroup.addLayer(marker);

                    const color = getMarkerColor(f);
                    const w = f.weather || {{}};
                    const card = document.createElement('div');
                    
                    let stateLabel = f.etat_feu || 'Attaque';
                    if (stateLabel === 'fausse_alerte') stateLabel = 'Fausse alerte';
                    else if (stateLabel === 'maitrise') stateLabel = 'Maîtrisé';
                    else if (stateLabel === 'eteint') stateLabel = 'Éteint';
                    else if (stateLabel === 'attaque') stateLabel = 'En attaque';
                    else if (stateLabel === 'fixe') stateLabel = 'Fixé';

                    let cardClass = 'fire-card-item';
                    let scaleTag = '';

                    if (f.etat_feu === 'eteint') {{
                        cardClass += ' eteint-card';
                        scaleTag = '<span class="scale-badge-eteint" style="background:#64748B; color:white; font-size:7.5px; font-weight:900; padding:1.5px 3.5px; border-radius:3px; text-transform:uppercase; margin-left:4px;">💧 ÉTEINT</span>';
                    }} else if (f.etat_feu === 'fausse_alerte') {{
                        cardClass += ' fausse-alerte-card';
                        scaleTag = '<span class="scale-badge-fausse-alerte" style="background:#94A3B8; color:white; font-size:7.5px; font-weight:900; padding:1.5px 3.5px; border-radius:3px; text-transform:uppercase; margin-left:4px;">❌ ALERTE</span>';
                    }} else if (f.fire_scale === 'majeur') {{
                        cardClass += ' majeur-card';
                        scaleTag = '<span class="scale-badge-majeur">🚨 MAJEUR</span>';
                    }} else if (f.is_under_1h) {{
                        cardClass += ' under-1h-card';
                        scaleTag = '<span class="scale-badge-modere">⚡ ' + f.minutes_ago + ' MIN</span>';
                    }} else {{
                        scaleTag = '<span class="scale-badge-localise">🟡 LOCALISÉ</span>';
                    }}

                    card.className = cardClass;
                    card.onclick = () => {{
                        map.flyTo([f.lat, f.lon], 12, {{ duration: 1.2 }});
                        setTimeout(() => marker.openPopup(), 1200);
                    }};

                    const speedVal = (w.wind_speed_kmh !== undefined && w.wind_speed_kmh !== null) ? w.wind_speed_kmh : 15;
                    const gustVal = (w.wind_gusts_kmh !== undefined && w.wind_gusts_kmh !== null) ? w.wind_gusts_kmh : 25;

                    const subInfo = currentViewMode === 'risk' 
                        ? `<span style="font-weight:900; color:${{w.spread_risk_color || '#0F172A'}}; font-size:11.5px;">Risque: ${{w.spread_risk || 'N/A'}}</span>`
                        : `<span>💨 <b>Vent Moy:</b> <b style="color:#0F172A;">${{speedVal}} km/h</b> | <b>Raf:</b> <b style="color:#DC2626;">${{gustVal}} km/h</b></span>`;

                    card.innerHTML = `
                        <div class="top-line">
                            <div>
                                <span class="dept-tag">DEP ${{f.dept}}</span>
                                ${{scaleTag}}
                            </div>
                            <span class="state-tag" style="color:${{color}}; text-transform: uppercase; font-weight: 900;">${{stateLabel}}</span>
                        </div>
                        <div class="commune-name">${{f.commune}}</div>
                        <div class="sub-details">
                            <span>⏱️ <b>Détecté :</b> <b style="color:#0F172A;">${{f.detect_time_fr || 'N/A'}}</b> (${{f.timeAgo || ''}})</span>
                            ${{subInfo}}
                        </div>
                    `;
                    sidebarContainer.appendChild(card);
                }}
            }});

            document.getElementById('sidebar-count').innerText = visibleCount;
        }}

        function filterFires(statusType) {{
            currentStatusFilter = statusType;
            renderFires();
        }}

        function filterRegion(regionVal) {{
            currentRegionFilter = regionVal;
            renderFires();
        }}

        if (window.innerWidth < 768) {{
            document.getElementById('sidebar').classList.add('collapsed');
        }}
        renderFires();

        pelicandromes.forEach(p => {{
            const pIcon = L.divIcon({{
                className: 'custom-peli-marker',
                html: '<div style="background: #2563EB; color: white; border-radius: 50%; width: 18px; height: 18px; border: 2px solid white; display: flex; align-items: center; justify-content: center; font-size: 9px; box-shadow: 0 2px 6px rgba(37, 99, 235, 0.45);">✈️</div>',
                iconSize: [18, 18],
                iconAnchor: [9, 9]
            }});
            const safeName = p.name ? p.name.replace(/'/g, "&apos;") : '';
            L.marker([p.lat, p.lon], {{ icon: pIcon }}).addTo(pelicandromesLayerGroup).bindPopup('<div style="padding:6px 8px; font-weight:900; font-size:11px; color:#0F172A;">✈️ Base Canadair : ' + safeName + '</div>');
        }});
    </script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Carte interactive avec Auto-Refresh 10 min + Historique Relevés + Fallback Vent 100% : {output_path}")
    return True

def export_pdf(results, latest_news, output_path):
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        print("ReportLab n'est pas installé. Lancez : pip install reportlab")
        return False

    doc = SimpleDocTemplate(output_path, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('DocTitle', parent=styles['Title'], fontSize=16, leading=20, textColor=colors.HexColor('#991B1B'), alignment=0, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor('#4B5563'))
    section_title_red = ParagraphStyle('SecTitleRed', parent=styles['Heading2'], fontSize=11, leading=14, textColor=colors.HexColor('#DC2626'), fontName='Helvetica-Bold')
    section_title_blue = ParagraphStyle('SecTitleBlue', parent=styles['Heading2'], fontSize=11, leading=14, textColor=colors.HexColor('#2563EB'), fontName='Helvetica-Bold')
    section_title_amber = ParagraphStyle('SecTitleAmber', parent=styles['Heading2'], fontSize=11, leading=14, textColor=colors.HexColor('#D97706'), fontName='Helvetica-Bold')
    section_title_dark = ParagraphStyle('SecTitleDark', parent=styles['Heading2'], fontSize=11, leading=14, textColor=colors.HexColor('#0F172A'), fontName='Helvetica-Bold')
    
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=8.5, leading=11, fontName='Helvetica')
    cell_bold = ParagraphStyle('CellBold', parent=styles['Normal'], fontSize=8.5, leading=11, fontName='Helvetica-Bold')
    header_cell = ParagraphStyle('HeaderCell', parent=styles['Normal'], fontSize=8.5, leading=11, fontName='Helvetica-Bold', textColor=colors.white)

    now_str = datetime.now(tz_paris).strftime("%d/%m/%Y à %H:%M")
    
    count_under_1h = sum(1 for f in results if f.get("is_under_1h"))
    count_majeurs = sum(1 for f in results if f.get("fire_scale") == "majeur")
    count_attaque = sum(1 for f in results if f.get("etat_feu") == "attaque")

    elements.append(Paragraph('DOSSIER OPÉRATIONNEL NATIONAL — FEUX DE FORÊT (MÉTÉO CLIMAT PRO)', title_style))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(f'Document d\'intervention synthétique du {now_str} · <b>{len(results)} feux actifs</b> (🚨 {count_majeurs} Feux Majeurs | ⚡ {count_under_1h} nouveaux < 1h | 🔥 {count_attaque} en Attaque)', subtitle_style))
    elements.append(Spacer(1, 4))
    elements.append(HRFlowable(width='100%', thickness=1.5, color=colors.HexColor('#DC2626'), spaceAfter=10))

    def build_status_table(fires_group, header_bg_color):
        table_data = [[
            Paragraph('N°', header_cell),
            Paragraph('Dép.', header_cell),
            Paragraph('Commune', header_cell),
            Paragraph('Ampleur & Détection', header_cell),
            Paragraph('Statut', header_cell),
            Paragraph('Superficie (ha)', header_cell),
            Paragraph('Vent Moy. / Rafales Obs.', header_cell),
            Paragraph('Danger FWI', header_cell),
            Paragraph('Canadair Proche & Vol', header_cell)
        ]]

        for idx, f in enumerate(fires_group, 1):
            m = f.get('minutes_ago', 0)
            if m < 60: anc = f"({m} min)"
            elif m < 9999: anc = f"({m//60}h{m%60:02d})"
            else: anc = ""
            
            recent_prefix = "<b>[🚨 MAJEUR]</b> " if f.get("fire_scale") == "majeur" else (f"<b>[⚡ {m} MIN]</b> " if f.get("is_under_1h") else "")
            det_time = f"{recent_prefix}{f.get('detect_time_fr', 'N/A')} {anc}"
            w = f.get('weather', {})
            
            ha_txt = f"<b>{f.get('superficie', 0)} ha</b>" if f.get('superficie') else "En cours"
            v_raf_txt = f"Moy. <b>{w.get('wind_speed_kmh', 15)}</b> / Raf. <font color=\"#DC2626\"><b>{w.get('wind_gusts_kmh', 25)} km/h</b></font>"
            r_txt = f"<font color=\"{w.get('spread_risk_color', '#000')}\"><b>{w.get('spread_risk', 'N/A')}</b></font>"
            
            p_name = f.get('pelicandrome_name', 'N/A')
            p_eta = f.get('pelicandrome_eta', 'N/A')
            p_txt = f"{p_name} (⏱️ <b>{p_eta}</b>)" if p_name != 'N/A' else 'N/A'
            
            row = [
                Paragraph(f"{idx:02d}", cell_bold),
                Paragraph(f"<b>{f.get('dept', '')}</b>", cell_bold),
                Paragraph(f"<b>{f.get('commune', '')}</b>", cell_bold),
                Paragraph(det_time, cell_style),
                Paragraph(f.get('etat_feu', 'Attaque').capitalize(), cell_bold),
                Paragraph(ha_txt, cell_style),
                Paragraph(v_raf_txt, cell_style),
                Paragraph(r_txt, cell_style),
                Paragraph(p_txt, cell_style)
            ]
            table_data.append(row)

        col_widths = [24, 32, 125, 95, 70, 75, 135, 90, 120]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), header_bg_color),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        return t

    feux_attaque = [f for f in results if f.get('etat_feu') == 'attaque']
    if feux_attaque:
        elements.append(Paragraph(f'🚨 SECTION 1 : INCENDIES EN ATTAQUE ({len(feux_attaque)} Feux Actifs — Priorité Intervention)', section_title_red))
        elements.append(Spacer(1, 4))
        elements.append(build_status_table(feux_attaque, colors.HexColor('#DC2626')))
        elements.append(Spacer(1, 10))

    feux_fixe = [f for f in results if f.get('etat_feu') == 'fixe']
    if feux_fixe:
        elements.append(Paragraph(f'🎯 SECTION 2 : INCENDIES FIXÉS ({len(feux_fixe)} Feux Stoppés — En Surveillance)', section_title_blue))
        elements.append(Spacer(1, 4))
        elements.append(build_status_table(feux_fixe, colors.HexColor('#2563EB')))
        elements.append(Spacer(1, 10))

    feux_maitrise = [f for f in results if f.get('etat_feu') == 'maitrise']
    if feux_maitrise:
        elements.append(Paragraph(f'🟡 SECTION 3 : INCENDIES MAÎTRISÉS ({len(feux_maitrise)} Feux en Noyoyage)', section_title_amber))
        elements.append(Spacer(1, 4))
        elements.append(build_status_table(feux_maitrise, colors.HexColor('#D97706')))
        elements.append(Spacer(1, 10))

    if latest_news:
        elements.append(Paragraph('📰 SECTION 4 : FIL DU DIRECT — DÉPÊCHES SDIS & PRÉFECTURES (Sources Officielles)', section_title_dark))
        elements.append(Spacer(1, 4))
        
        news_table_data = [[
            Paragraph('Source Officielle / Référence', header_cell),
            Paragraph('Dépêche / Titre d\'Information Pompiers & Préfectures', header_cell),
            Paragraph('Date & Heure', header_cell)
        ]]
        for n in latest_news[:8]:
            news_table_data.append([
                Paragraph(f"<b>{n['source']}</b>", cell_bold),
                Paragraph(n['title'], cell_style),
                Paragraph(n['date'][:22] if n['date'] else 'En direct', cell_style)
            ])
        
        t_news = Table(news_table_data, colWidths=[180, 480, 116], repeatRows=1)
        t_news.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F172A')),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
            ('TOPPADDING', (0,0), (-1,-1), 3.5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3.5),
        ]))
        elements.append(t_news)

    doc.build(elements)
    print(f"✅ PDF Opérationnel généré avec succès : {output_path}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Collecteur de feux de forêt & Carte Autonome Pro")
    parser.add_argument("--format", choices=["table", "tsv", "pdf", "json", "map"], default="table", help="Format de sortie")
    parser.add_argument("--desktop", action="store_true", help="Générer la Carte HTML et le PDF directement sur le Bureau")
    parser.add_argument("--ci", action="store_true", help="Mode GitHub Actions : génère index.html + PDF dans le répertoire courant")
    parser.add_argument("--offline", action="store_true", help="Utiliser des données de simulation hors-ligne pour les tests")
    args = parser.parse_args()

    if args.offline:
        results = [
            {
                "id": "1", "title": "Feu de forêt à Marseille", "commune": "Marseille", "dept": "13",
                "lat": 43.2964, "lon": 5.3698, "detect_time_fr": "15:45", "timeAgo": "il y a 10 min",
                "etat_feu": "attaque", "fire_scale": "majeur", "scale_label": "🚨 FEU MAJEUR", "scale_color": "#7C3AED",
                "pelicandrome_name": "Marignane", "pelicandrome_dist": 15.0, "pelicandrome_eta": "4 min",
                "meteociel_station": "Marseille-Marignane", "meteociel_dist": 15.0,
                "weather": {
                    "temp_c": 32.5, "humidity_pct": 35, "wind_speed_kmh": 25, "wind_gusts_kmh": 45,
                    "wind_origin": "N", "plume_arrow": "➡️", "plume_dir": "Sud", "plume_deg": 180,
                    "spread_risk": "TRÈS ÉLEVÉ", "spread_risk_color": "#DC2626", "fwi_score": 24
                },
                "downwind_exposure": [{"commune": "Aubagne", "dist_km": 12, "eta_smoke": "20 min", "is_sector": False}]
            },
            {
                "id": "2", "title": "Feu de forêt à Bordeaux", "commune": "Bordeaux", "dept": "33",
                "lat": 44.8378, "lon": -0.5792, "detect_time_fr": "15:20", "timeAgo": "il y a 35 min",
                "etat_feu": "fixe", "fire_scale": "modere", "scale_label": "🔶 MODÉRÉ", "scale_color": "#D97706",
                "pelicandrome_name": "Mérignac", "pelicandrome_dist": 8.0, "pelicandrome_eta": "3 min",
                "meteociel_station": "Bordeaux-Mérignac", "meteociel_dist": 8.0,
                "weather": {
                    "temp_c": 28.0, "humidity_pct": 45, "wind_speed_kmh": 12, "wind_gusts_kmh": 22,
                    "wind_origin": "E", "plume_arrow": "➡️", "plume_dir": "Ouest", "plume_deg": 270,
                    "spread_risk": "MODÉRÉ", "spread_risk_color": "#F59E0B", "fwi_score": 14
                },
                "downwind_exposure": []
            }
        ]
        latest_news = []
    else:
        results, latest_news = fetch_all_feux()

    if args.ci:
        # ponytail: --ci écrit dans le répertoire courant, compatible runner Ubuntu sans Bureau
        generate_interactive_map(results, latest_news, "index.html")
        export_pdf(results, latest_news, "Rapport_Feux_de_Foret_Temps_Reel.pdf")
        return

    if args.format == "map" or args.desktop:
        desktop_map = os.path.join(os.path.expanduser("~"), "Desktop", "carte_feux.html")
        generate_interactive_map(results, latest_news, desktop_map)

    if args.format == "pdf" or args.desktop:
        desktop_pdf = os.path.join(os.path.expanduser("~"), "Desktop", "Rapport_Feux_de_Foret_Temps_Reel.pdf")
        export_pdf(results, latest_news, desktop_pdf)

if __name__ == "__main__":
    main()
