import urllib.request
import json
import traceback

ANONYMOUS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
}

def debug_enrich():
    # 1. Fetch home page to get a fire URL
    url = "https://feuxdeforet.fr/"
    req = urllib.request.Request(url, headers=ANONYMOUS_HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8")
    
    idx = html.find("window.__INITIAL_DATA__=")
    start = html.find("{", idx)
    data, _ = json.JSONDecoder().raw_decode(html[start:])
    active_feux = data.get("data", {}).get("feux", [])
    
    if not active_feux:
        print("No active fires found on homepage")
        return
        
    fire = active_feux[0]
    fire_url = "https://feuxdeforet.fr" + fire["url"]
    print("Testing enrichment on URL:", fire_url)
    
    try:
        req_p = urllib.request.Request(fire_url, headers=ANONYMOUS_HEADERS)
        with urllib.request.urlopen(req_p, timeout=10) as r:
            h = r.read().decode("utf-8")
            
        print("Page HTML fetched successfully, length:", len(h))
        
        # Look for window.__INITIAL_DATA__
        init_idx = h.find("window.__INITIAL_DATA__=")
        print("Index of INITIAL_DATA in detail page:", init_idx)
        
        if init_idx == -1:
            print("❌ window.__INITIAL_DATA__ not found in detail page!")
            return
            
        st = h.find("{", init_idx)
        d, _ = json.JSONDecoder().raw_decode(h[st:])
        det = d.get("data", {})
        print("Detail keys:", list(det.keys()))
        
        # Let's print the specific fire info from detail page
        post = det.get("post", {})
        print("Post keys:", list(post.keys()))
        print("Latitude:", post.get("latitude") or det.get("latitude"))
        print("Longitude:", post.get("longitude") or det.get("longitude"))
        
    except Exception as e:
        print("❌ ENRICHMENT FAILED:")
        traceback.print_exc()

if __name__ == "__main__":
    debug_enrich()
