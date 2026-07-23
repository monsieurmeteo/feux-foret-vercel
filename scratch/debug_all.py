import urllib.request
import json
from concurrent.futures import ThreadPoolExecutor

ANONYMOUS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
}

def debug_all():
    url = "https://feuxdeforet.fr/"
    req = urllib.request.Request(url, headers=ANONYMOUS_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8")
    
    idx = html.find("window.__INITIAL_DATA__=")
    start = html.find("{", idx)
    data, _ = json.JSONDecoder().raw_decode(html[start:])
    active_feux = data.get("data", {}).get("feux", [])
    
    print("Total active fires to enrich:", len(active_feux))
    
    def enrich_debug(f):
        fire_url = "https://feuxdeforet.fr" + f["url"]
        try:
            req_p = urllib.request.Request(fire_url, headers=ANONYMOUS_HEADERS)
            with urllib.request.urlopen(req_p, timeout=6) as r:
                h = r.read().decode("utf-8")
            st = h.find("{", h.find("window.__INITIAL_DATA__="))
            d, _ = json.JSONDecoder().raw_decode(h[st:])
            det = d.get("data", {})
            
            # Print if we get here
            lat = det.get("latitude")
            lon = det.get("longitude")
            print(f"✅ Success: {f['commune']} (Lat: {lat}, Lon: {lon})")
            
        except Exception as e:
            print(f"❌ Failed: {f['commune']} on {fire_url} with error: {type(e).__name__}: {str(e)}")

    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(enrich_debug, active_feux[:10]))

if __name__ == "__main__":
    debug_all()
