
import time
from main import CycleManager

def test_cycle_logic():
    print("--- Testing Smart Cycle Strategy (3-Zones) ---")
    
    cm = CycleManager(use_cycle=True)
    
    # =========================================================================
    # SCENARIO 1: EARLY GAME (Accumulator)
    # Time Left: 50 mins
    # =========================================================================
    time_left = 3000
    regime = cm.get_regime(time_left)
    print(f"\n[Zone 1] Early Game (50m Left): {regime['name']}")
    
    assert regime["name"] == "ACCUMULATOR", "Wrong Regime"
    assert regime["start_margin"] == 0.08, "Wrong Start Margin"
    assert regime["buy_allowed"] == True
    
    # Test Margin Decay
    cm.last_buy_time_yes = time.time() # Just bought
    margin_t0 = cm.get_dynamic_margin("yes", time_left)
    print(f"   T+0 Margin: {margin_t0:.3f} (Expected 0.080)")
    assert abs(margin_t0 - 0.08) < 0.001
    
    # Advance 10 mins (Half of 20m decay)
    cm.last_buy_time_yes = time.time() - 600
    margin_t10 = cm.get_dynamic_margin("yes", time_left)
    print(f"   T+10m Margin: {margin_t10:.3f} (Expected ~0.045)")
    # 0.08 -> 0.01 over 20m. 10m is 50%. So 0.045.
    assert 0.04 < margin_t10 < 0.05
    
    # =========================================================================
    # SCENARIO 2: MID GAME (Accelerator)
    # Time Left: 20 mins
    # =========================================================================
    time_left = 1200
    regime = cm.get_regime(time_left)
    print(f"\n[Zone 2] Mid Game (20m Left): {regime['name']}")
    
    assert regime["name"] == "ACCELERATOR", "Wrong Regime"
    assert regime["start_margin"] == 0.06
    assert regime["size_mult"] == 0.50
    
    # Test Faster Decay (5 mins total)
    cm.last_buy_time_yes = time.time() - 150 # 2.5 mins (Half decay)
    margin_mid = cm.get_dynamic_margin("yes", time_left)
    print(f"   T+2.5m Margin: {margin_mid:.3f} (Expected ~0.035)")
    assert 0.03 < margin_mid < 0.04
    
    # =========================================================================
    # SCENARIO 3: END GAME (Terminator)
    # Time Left: 8 mins
    # =========================================================================
    time_left = 480
    regime = cm.get_regime(time_left)
    print(f"\n[Zone 3] End Game (8m Left): {regime['name']}")
    
    assert regime["name"] == "LIQUIDATE_ONLY", "Wrong Regime"
    assert regime["buy_allowed"] == False
    assert regime["size_mult"] == 0.0
    
    # =========================================================================
    # SCENARIO 4: HARD STOP (T-5m)
    # Time Left: 4 mins
    # =========================================================================
    time_left = 240
    margin_panic = cm.get_dynamic_margin("yes", time_left)
    print(f"\n[Zone 4] Hard Stop (4m Left)")
    print(f"   Panic Margin: {margin_panic:.3f} (Expected 0.010)")
    assert margin_panic == 0.01

    print("\n✅ All Cycle Tests Passed.")

if __name__ == "__main__":
    test_cycle_logic()
