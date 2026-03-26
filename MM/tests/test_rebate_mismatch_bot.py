import unittest

from rebate_mismatch_bot import (
    _detect_interval_minutes,
    _extract_yes_no_tokens,
    est_taker_fee_usdc,
)


class TestRebateMismatchBot(unittest.TestCase):
    def test_detect_interval_minutes(self):
        self.assertEqual(_detect_interval_minutes("Will SOL be above X in 5 minute market?"), 5)
        self.assertEqual(_detect_interval_minutes("eth-15min-breakout"), 15)
        self.assertIsNone(_detect_interval_minutes("Will ETH be above by tomorrow?"))

    def test_extract_yes_no_tokens_with_outcome_mapping(self):
        market = {
            "clobTokenIds": '["token_no","token_yes"]',
            "outcomes": '["No","Yes"]',
        }
        yes, no = _extract_yes_no_tokens(market)
        self.assertEqual(yes, "token_yes")
        self.assertEqual(no, "token_no")

    def test_fee_estimate_monotonic_around_mid(self):
        fee_bps = 1000
        low = est_taker_fee_usdc(shares=100.0, price=0.10, fee_rate_bps=fee_bps)
        mid = est_taker_fee_usdc(shares=100.0, price=0.50, fee_rate_bps=fee_bps)
        self.assertGreater(mid, low)
        self.assertGreater(mid, 0.0)


if __name__ == "__main__":
    unittest.main()
