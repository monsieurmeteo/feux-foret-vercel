import urllib.request
import sys

ANONYMOUS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

def test_fetch():
    url = "https://feuxdeforet.fr/"
    req = urllib.request.Request(url, headers=ANONYMOUS_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8")
        print("STATUS: 200 OK")
        print("HTML LENGTH:", len(html))
        print("HTML SNIPPET:", html[:1000])
    except Exception as e:
        print("FETCH FAILED:", str(e))

if __name__ == "__main__":
    test_fetch()
