"""
Verification Suite for Crypto Parser
"""
import unittest
from crypto_parser import CryptoParser

class TestCryptoParser(unittest.TestCase):
    
    def test_btc_basic(self):
        title = "Will BTC hit $105,000 by Friday?"
        data = CryptoParser.parse_title(title)
        self.assertIsNotNone(data)
        self.assertEqual(data.asset, "BTC")
        self.assertEqual(data.strike, 105000.0)
        self.assertEqual(data.raw_date, "Friday")

    def test_btc_k_notation(self):
        title = "Will Bitcoin hit $95k?"
        data = CryptoParser.parse_title(title)
        self.assertEqual(data.asset, "BTC")
        self.assertEqual(data.strike, 95000.0)
        
    def test_eth_basic(self):
        title = "Will ETH be above $3,200 on Jan 26?"
        data = CryptoParser.parse_title(title)
        self.assertEqual(data.asset, "ETH")
        self.assertEqual(data.strike, 3200.0)
        self.assertEqual(data.raw_date, "Jan 26")

    def test_decimal_strike(self):
        title = "Will ETH hit $2,550.50?"
        data = CryptoParser.parse_title(title)
        self.assertEqual(data.strike, 2550.50)
        self.assertEqual(data.market_type, "FIXED_STRIKE")

    def test_hourly_up_down(self):
        title = "Will BTC be up at 5pm?"
        data = CryptoParser.parse_title(title)
        self.assertEqual(data.asset, "BTC")
        self.assertEqual(data.market_type, "UP_DOWN")
        self.assertEqual(data.direction, "up")
        
    def test_sol_close_higher(self):
        title = "Will SOL close higher than open?"
        data = CryptoParser.parse_title(title)
        self.assertEqual(data.asset, "SOL")
        self.assertEqual(data.market_type, "UP_DOWN")


    def test_ignore_junk(self):
        # Should return None for non-binary-compatible titles
        self.assertIsNone(CryptoParser.parse_title("Will the Chiefs win?"))
        self.assertIsNone(CryptoParser.parse_title("Will Trump tweet about BTC?")) # Contains BTC but no Price

if __name__ == '__main__':
    unittest.main()
