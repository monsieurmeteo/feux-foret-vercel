import urllib.request
import urllib.parse
import json

ANONYMOUS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

def debug_fetch():
    url = "https://feuxdeforet.fr/"
    req = urllib.request.Request(url, headers=ANONYMOUS_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8")
        print("Length of HTML fetched:", len(html))
        
        idx = html.find("window.__INITIAL_DATA__=")
        print("Index of window.__INITIAL_DATA__:", idx)
        
        if idx != -1:
            snippet = html[idx:idx+300]
            print("Snippet of INITIAL_DATA:", snippet)
            start = html.find("{", idx)
            try:
                data, _ = json.JSONDecoder().raw_decode(html[start:])
                active_feux = data.get("data", {}).get("feux", [])
                print("Active fires found in JSON:", len(active_feux))
                for f in active_feux[:5]:
                    print(f"  - Fire: {f.get('commune')} (Lat: {f.get('lat')}, Lon: {f.get('lon')})")
            except Exception as ex:
                print("JSON decode error:", ex)
        else:
            print("❌ window.__INITIAL_DATA__ not found in HTML!")
            # Save HTML to a temporary file for analysis
            with open("scratch/page.html", "w", encoding="utf-8") as temp_f:
                temp_f.write(html)
            print("HTML saved to scratch/page.html")
            
    except Exception as e:
        print("Request failed:", e)

if __name__ == "__main__":
    debug_fetch()
