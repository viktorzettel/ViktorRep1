
import asyncio
import logging
import sys
from client_wrapper import PolymarketClient
from data_feed import UserWebSocket

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_feeds(token_id=None):
    print("="*60)
    print("POLYMARKET CONNECTION TESTER")
    print("="*60)
    
    # 1. Test Client & Credentials
    print("\n1. Testing Client & Credentials...")
    try:
        pm = PolymarketClient()
        creds = pm.get_credentials()
        address = pm.address
        print(f"✅ Client initialized for address: {address}")
        if creds:
             print("✅ L2 Credentials present")
    except Exception as e:
        print(f"❌ Client init failed: {e}")
        return

    # 2. Test On-Chain Position
    if token_id:
        print(f"\n2. Testing Position Fetch for {token_id}...")
        try:
            pos = pm.get_position(token_id)
            print(f"✅ Fetched Position: {pos} shares")
        except Exception as e:
            print(f"❌ Position fetch failed: {e}")
    else:
        print("\n2. Skipping Position Test (no token_id provided)")

    # 3. Test User WebSocket
    print("\n3. Testing User WebSocket Connection...")
    user_ws = UserWebSocket(api_creds=creds)
    
    # Run WS for 5 seconds then shutdown
    try:
        # Create a task to stop after 5 seconds
        async def stop_later():
            await asyncio.sleep(5)
            await user_ws.disconnect()
            print("✅ User WebSocket connected and ran for 5s (assumed success if no errors)")

        # Start WS in background task
        ws_task = asyncio.create_task(user_ws.connect())
        stop_task = asyncio.create_task(stop_later())
        
        # Wait for either to finish (stop_task should finish first)
        done, pending = await asyncio.wait(
            [ws_task, stop_task], 
            return_when=asyncio.FIRST_COMPLETED
        )
        
        # Cleanup
        for task in pending:
            task.cancel()
            
    except Exception as e:
        print(f"❌ WebSocket test failed: {e}")

if __name__ == "__main__":
    TOKEN_ID = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(test_feeds(TOKEN_ID))
