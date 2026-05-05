import json
import tempfile
import time
import unittest
from pathlib import Path

import polymarket_token_sniper as sniper


def make_signal(**overrides):
    payload = {
        "symbol": "xrpusdt",
        "side": "yes",
        "max_entry_price": 0.98,
        "market_slug": "xrp-updown-5m-1777938600",
        "bucket_end": 1777938899.0,
        "expires_at": time.time() + 60.0,
        "source_age_s": 0.5,
        "model_age_s": 0.5,
        "time_left_s": 20.0,
        "price": 1.3926,
        "strike": 1.3918,
    }
    payload.update(overrides)
    return sniper.KouBuySignal.from_mapping(payload)


class PolymarketTokenSniperTests(unittest.TestCase):
    def test_validate_signal_requires_model_age(self):
        signal = make_signal(model_age_s=None)
        ok, reason, _checks = sniper.validate_signal(signal, sniper.SniperLimits(), time.time())
        self.assertFalse(ok)
        self.assertEqual(reason, "signal_model_age_missing")

    def test_session_risk_detects_used_bucket(self):
        signal = make_signal()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            ledger.write_text(
                json.dumps(
                    {
                        "event_type": "live_order_submitted",
                        "bucket_end": signal.bucket_end,
                        "market_slug": signal.market_slug,
                        "estimated_cost": 0.9,
                        "real_order_submitted": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = sniper.session_risk_state(str(ledger), signal)
        self.assertTrue(state.bucket_already_used)
        self.assertEqual(state.submitted_order_count, 1)
        self.assertAlmostEqual(state.submitted_cost, 0.9)

    def test_build_plan_stops_before_network_when_bucket_used(self):
        signal = make_signal()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            ledger.write_text(
                json.dumps(
                    {
                        "event_type": "live_order_submitted",
                        "bucket_end": signal.bucket_end,
                        "market_slug": signal.market_slug,
                        "estimated_cost": 0.9,
                        "real_order_submitted": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            plan = sniper.build_dry_run_plan(
                signal,
                sniper.SniperLimits(),
                env_file=".env",
                ledger_path=str(ledger),
            )
        self.assertFalse(plan.allow_submit)
        self.assertEqual(plan.reason, "bucket_already_used_in_session")

    def test_build_plan_stops_before_network_when_session_cost_reached(self):
        signal = make_signal(bucket_end=1777939199.0, market_slug="xrp-updown-5m-1777938900")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            ledger.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "event_type": "live_order_submitted",
                            "bucket_end": 1777938000.0 + idx,
                            "market_slug": f"xrp-updown-5m-{1777937700 + idx}",
                            "estimated_cost": 1.0,
                            "real_order_submitted": True,
                        }
                    )
                    for idx in range(4)
                )
                + "\n",
                encoding="utf-8",
            )
            plan = sniper.build_dry_run_plan(
                signal,
                sniper.SniperLimits(max_session_cost=4.0, max_session_orders=4),
                env_file=".env",
                ledger_path=str(ledger),
            )
        self.assertFalse(plan.allow_submit)
        self.assertEqual(plan.reason, "max_session_orders_reached")


if __name__ == "__main__":
    unittest.main()
