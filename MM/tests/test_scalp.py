"""
Test script for Micro-Scalping Manager
"""
import time
import logging
from micro_scalping_manager import MicroScalpingManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Test")

def test_decay():
    print("--- Testing Decay Logic ---")
    
    # 1 cent per minute, 2 second grace for testing
    mgr = MicroScalpingManager(decay_rate_per_min=0.01, grace_period_sec=2.0)
    
    # Buy 10 shares
    mgr.on_fill("buy", 10.0, 0.50, "yes")
    print("Bought 10 YES @ t=0")
    
    # t=1 (Inside Grace)
    time.sleep(1.0)
    decay = mgr.get_decay_adjustment("yes")
    print(f"t=1s Decay: {decay:.4f} (Expected 0.0)")
    assert decay == 0.0
    
    # t=3 (Outside Grace)
    time.sleep(2.1)
    decay = mgr.get_decay_adjustment("yes")
    
    # Expected: (3 - 2) / 60 * 0.01 = 1/6000 = 0.00016
    print(f"t=3.1s Decay: {decay:.4f} (Expected > 0)")
    assert decay > 0.0
    
    print("--- Decay Logic Passed ---\n")

def test_fifo():
    print("--- Testing FIFO Logic ---")
    mgr = MicroScalpingManager(decay_rate_per_min=0.60, grace_period_sec=0.0) # 1 cent/sec
    
    # Batch A: Bought at t=0
    mgr.on_fill("buy", 10.0, 0.50, "yes")
    time.sleep(2) 
    
    # Batch B: Bought at t=2
    mgr.on_fill("buy", 10.0, 0.50, "yes")
    
    # At t=2, Batch A is 2s old. Batch B is 0s old.
    # Decay should be based on A (Oldest) -> ~0.02
    decay_1 = mgr.get_decay_adjustment("yes")
    print(f"Decay with Old Batch: {decay_1:.4f}")
    assert decay_1 > 0.015
    
    # Sell 10 shares (Consumes Batch A)
    mgr.on_fill("sell", 10.0, 0.52, "yes")
    print("Sold 10 YES (Cleared Oldest Batch)")
    
    # Now Oldest is Batch B (0s old). Decay should drop to ~0
    decay_2 = mgr.get_decay_adjustment("yes")
    print(f"Decay with New Batch: {decay_2:.4f}")
    assert decay_2 < 0.005 # Should be tiny
    
    print("--- FIFO Logic Passed ---")

if __name__ == "__main__":
    test_decay()
    test_fifo()
