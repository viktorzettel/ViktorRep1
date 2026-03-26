import time
import threading
import logging
import asyncio
import scanner  # Modified import

logger = logging.getLogger(__name__)

class AutoPilot:
    def __init__(self, clob_client, market_maker_class, args, risk_config=None):
        self.client = clob_client
        self.mm_class = market_maker_class
        self.args = args  # CLI args (refresh_rate, etc.)
        self.risk_config = risk_config # Store conservative settings
        # self.scanner = MarketScanner() Removed
        
        self.current_market = None # This is a MarketScore object now
        self.current_score = -1.0
        self.current_mm = None
        self.mm_thread = None
        
        self.running = True
        self.scan_interval = 300  # 5 minutes
        self.hysteresis = 1.3     # New score must be 1.3x better to switch

    async def run(self):
        """Main Auto-Pilot Loop (Async)"""
        logger.info("🚀 Auto-Pilot Engaged: Scanning for initial market...")
        
        while self.running:
            try:
                # 1. Scan for best market
                best_market, best_score = self._scan_and_select()
                
                # 2. Decision Logic
                should_switch = False
                
                if best_market:
                    if self.current_market is None:
                        should_switch = True
                    elif best_market.slug != self.current_market.slug:
                         if self._should_switch(best_score):
                             should_switch = True
                         else:
                             logger.info(f"Existing market still good enough. Current: {self.current_score:.2f}, New Best: {best_score:.2f}")
                
                if should_switch:
                    await self._switch_market(best_market, best_score)
                
                # 3. Maintain current bot
                if self.mm_thread and not self.mm_thread.is_alive():
                    logger.warning("MarketMaker thread died unexpectedly! Restarting scan.")
                    self.current_market = None # Force re-selection
                    
                # 4. Wait for next scan
                await asyncio.sleep(self.scan_interval)
                
            except asyncio.CancelledError:
                logger.info("Auto-Pilot task cancelled.")
                await self.stop()
                break
            except KeyboardInterrupt:
                logger.info("Auto-Pilot Stopping (KeyboardInterrupt)...")
                await self.stop()
                break
            except Exception as e:
                logger.error(f"Auto-Pilot Loop Error: {e}", exc_info=True)
                await asyncio.sleep(60) # Backoff on error

    async def stop(self):
        self.running = False
        if self.current_mm:
            logger.info("Stopping active MarketMaker...")
            self.current_mm.initiate_soft_stop()
            # We don't block the loop joining threads here, 
            # we just signal the stop. 
            # In main.py we will wait for cleanup.
            if self.mm_thread:
                # Joining a thread in an async method is tricky if we want to be non-blocking.
                # But here we are shutting down, so we can wait a bit or let it daemon-exit.
                pass

    def _scan_and_select(self):
        """Scans markets and returns the single best candidate."""
        # Using a dummy config for now, scanner uses defaults
        markets = scanner.scan_markets(limit=20) 
        if not markets:
            logger.warning("Scanner found NO viable markets (Filter strictness?).")
            return None, 0.0
            
        # Markets are already sorted by score desc in scanner usually, 
        # but let's double check
        best_market = markets[0]
        return best_market, best_market.score

    def _should_switch(self, new_score):
        """Hysteresis Logic"""
        if self.current_market is None:
            return True # Always start if nothing running
            
        # Threshold: New score must be significantly better
        if new_score > self.current_score * self.hysteresis:
            logger.info(f"✅ Switch Triggered! New Score {new_score:.2f} > Current {self.current_score:.2f} * {self.hysteresis}")
            return True
            
        return False

    async def _switch_market(self, new_market, new_score):
        """Stops current MM (gracefully) and starts new one."""
        current_question = self.current_market.question if self.current_market else 'None'
        logger.info(f"🔄 Switching Markets: {current_question} -> {new_market.question}")
        
        # 1. Soft Stop Current
        if self.current_mm:
            logger.info("⏳ Initiating Soft Stop on current bot...")
            self.current_mm.initiate_soft_stop()
            # For thread-joining in async, we'll use a wrapper or just wait
            # Since this is a specialized thread, we wait for it to stop
            start_wait = time.time()
            while self.mm_thread.is_alive() and time.time() - start_wait < 360:
                await asyncio.sleep(1)
                
            if self.mm_thread.is_alive():
                 logger.error("⚠️ Current bot failed to unwind in time! Forcing shutdown.")
                 await self.current_mm.shutdown() 
                 # Final wait
                 start_wait = time.time()
                 while self.mm_thread.is_alive() and time.time() - start_wait < 10:
                     await asyncio.sleep(1)
        
        # 2. Start New
        # Pass both tokens to avoid the 404 lookup in MarketMaker
        token_id_yes = new_market.token_id_yes
        token_id_no = new_market.token_id_no
        market_id = new_market.slug
        
        logger.info(f"🚀 Launching new MarketMaker for {market_id} (Y:{token_id_yes[:8]}, N:{token_id_no[:8]})...")
        
        refresh_rate = getattr(self.args, 'refresh', 5.0) 
        long_only = getattr(self.args, 'long_only', False)

        self.current_mm = self.mm_class(
            market_id=market_id,
            token_id=token_id_yes,
            token_id_no=token_id_no, # Passed explicitly
            market_type=None, 
            risk_config=self.risk_config, 
            starting_inventory=None, 
            refresh_rate_seconds=refresh_rate,
            long_only=long_only
        )
            
        # Start in Thread
        self.mm_thread = threading.Thread(target=lambda: asyncio.run(self.current_mm.run()), daemon=True)
        self.mm_thread.start()
        
        self.current_market = new_market
        self.current_score = new_score
        logger.info(f"✅ New Market Active: {new_market.question}")

