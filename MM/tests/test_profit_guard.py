
import logging
from dataclasses import dataclass
from typing import Optional

# Setup minimal mocks to replicate main.py logic
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ProfitGuardTest")

@dataclass
class Quote:
    bid: float
    ask: float

@dataclass
class DualQuote:
    yes: Quote
    no: Quote
    metadata: Optional[dict] = None

def test_profit_guard():
    print("--- Testing Profit Guard Logic ---")
    
    # SCENARIO 1: TRAPPED (Avg Entry > Market Ask)
    # We bought at 0.50, Market is now 0.40.
    # Strategy says Sell at 0.41.
    # Profit Guard should LIFT Ask to 0.51 (Entry + Margin).
    
    inv_yes = 10.0
    avg_entry_yes = 0.50
    
    quote = DualQuote(
        yes=Quote(0.39, 0.41), # Market Strategy Quote
        no=Quote(0.59, 0.61)
    )
    
    min_profit_margin = 0.01
    
    print(f"Scenario 1: Bag Holder (Entry {avg_entry_yes}, Market {quote.yes.ask})")
    
    if inv_yes > 5.0 and avg_entry_yes > 0:
        min_ask = avg_entry_yes + min_profit_margin
        if quote.yes.ask < min_ask:
             print(f"   🛡️ GUARD ACTIVE: Lifting Ask {quote.yes.ask:.3f} -> {min_ask:.3f}")
             quote.yes.ask = min_ask
        else:
             print(f"   ❌ GUARD FAILED: Ask not lifted.")
             
    if abs(quote.yes.ask - 0.51) < 0.001:
        print("   ✅ SUCCESS: Ask lifted to Break-Even + Profit.")
    else:
        print(f"   ❌ FAILURE: Final Ask {quote.yes.ask}")

    # SCENARIO 2: WINNING (Avg Entry < Market Ask)
    # We bought at 0.30, Market is now 0.40.
    # Strategy says Sell at 0.41.
    # Profit Guard should DO NOTHING (Let us take profit).
    
    avg_entry_yes = 0.30
    quote = DualQuote(
        yes=Quote(0.39, 0.41), 
        no=Quote(0.59, 0.61)
    )
    
    print(f"\nScenario 2: Winner (Entry {avg_entry_yes}, Market {quote.yes.ask})")
    
    if inv_yes > 5.0 and avg_entry_yes > 0:
        min_ask = avg_entry_yes + min_profit_margin
        if quote.yes.ask < min_ask:
             print(f"   ❌ ERROR: Guard shouldn't trigger!")
             quote.yes.ask = min_ask
        else:
             print(f"   ✅ PASS: Guard inactive (taking profit).")

    if abs(quote.yes.ask - 0.41) < 0.001:
        print("   ✅ SUCCESS: Ask remains at Market.")
    else:
        print(f"   ❌ FAILURE: Final Ask {quote.yes.ask}")

if __name__ == "__main__":
    test_profit_guard()
