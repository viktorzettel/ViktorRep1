import time
import requests
import asyncio
import websockets
import json

def test_https(name, url):
    print(f"Testing HTTPS to {name} ({url})...")
    latencies = []
    for _ in range(5):
        try:
            start = time.perf_counter()
            # Simple GET request
            requests.get(url, timeout=5)
            end = time.perf_counter()
            latencies.append((end - start) * 1000)
        except Exception:
            pass
    
    if latencies:
        avg = sum(latencies) / len(latencies)
        print(f"✅ {name} (HTTPS) Average RTT: {avg:.2f}ms")
    else:
        print(f"❌ {name} (HTTPS) Failed")

async def test_ws(name, url):
    print(f"Testing WebSocket Handshake to {name}...")
    import ssl
    try:
        ssl_context = ssl._create_unverified_context()
        start = time.perf_counter()
        async with websockets.connect(url, ssl=ssl_context) as ws:
            end = time.perf_counter()
            print(f"✅ {name} (WS Handshake): {(end - start) * 1000:.2f}ms")
    except Exception as e:
        print(f"❌ {name} (WS) Failed: {e}")

async def main():
    test_https("Polymarket API", "https://clob.polymarket.com/health")
    await test_ws("Binance WS", "wss://stream.binance.com:9443/ws/btcusdt@aggTrade")

if __name__ == "__main__":
    asyncio.run(main())
