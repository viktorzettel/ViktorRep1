import urllib.request
import ssl
import sys

def verify_slug(slug):
    ctx = ssl._create_unverified_context()
    url = f"https://clob.polymarket.com/markets/{slug}"
    print(f"Checking {url}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx) as response:
            if response.status == 200:
                print(f"✅ Slug '{slug}' EXISTS!")
                return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"❌ Slug '{slug}' NOT FOUND.")
        else:
            print(f"Error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    return False

if __name__ == "__main__":
    slugs = [
        "bitcoin-up-or-down-january-28-2pm-et",
        "bitcoin-up-or-down-january-28-1pm-et",
        "bitcoin-up-or-down-january-28-3pm-et"
    ]
    for s in slugs:
        verify_slug(s)
