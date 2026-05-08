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


def make_plan(**overrides):
    signal = overrides.pop("signal", make_signal())
    quote = overrides.pop(
        "token_quote",
        sniper.TokenQuote(
            token_id="123",
            side="yes",
            buy_price=0.89,
            book_ask_price=0.9,
            book_ask_size=10.0,
            book_endpoint_delta=0.01,
            entry_price=0.9,
            entry_price_source="book_ask",
        ),
    )
    payload = {
        "allow_submit": True,
        "mode": "dry_run_no_order",
        "reason": "dry_run_ready_live_submit_disabled",
        "signal": signal,
        "market_slug": signal.market_slug,
        "token_quote": quote,
        "requested_size": 1.0,
        "estimated_cost": 1.0,
        "checks": {},
    }
    payload.update(overrides)
    return sniper.ExecutionPlan(**payload)


class FakeOrderType:
    FOK = "FOK"
    FAK = "FAK"


class FakeSide:
    BUY = "BUY"


class FakeMarketOrderArgs:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create_and_post_market_order(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


FAKE_SDK = {
    "MarketOrderArgs": FakeMarketOrderArgs,
    "OrderType": FakeOrderType,
    "PartialCreateOrderOptions": FakeOptions,
    "Side": FakeSide,
}


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

    def test_session_risk_counts_dry_run_plans_as_cap_usage(self):
        signal = make_signal(bucket_end=1777939199.0, market_slug="xrp-updown-5m-1777938900")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            ledger.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "event_type": "dry_run_order_plan",
                            "bucket_end": 1777938000.0 + idx,
                            "market_slug": f"xrp-updown-5m-{1777937700 + idx}",
                            "side": "yes",
                            "estimated_cost": 1.0,
                            "real_order_submitted": False,
                        }
                    )
                    for idx in range(4)
                )
                + "\n",
                encoding="utf-8",
            )
            state = sniper.session_risk_state(str(ledger), signal)
            plan = sniper.build_dry_run_plan(
                signal,
                sniper.SniperLimits(max_session_cost=4.0, max_session_orders=4),
                env_file=".env",
                ledger_path=str(ledger),
            )
        self.assertEqual(state.planned_order_count, 4)
        self.assertAlmostEqual(state.planned_cost, 4.0)
        self.assertFalse(plan.allow_submit)
        self.assertEqual(plan.reason, "max_session_orders_reached")

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

    def test_record_dry_run_plan_writes_cap_ledger_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            plan = make_plan()
            sniper.record_dry_run_order_plan(
                plan,
                ledger_path=str(ledger),
                session_id="session-1",
                shadow_order_id="shadow-1",
            )
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            state = sniper.session_risk_state(str(ledger), make_signal(bucket_end=999.0, market_slug="other"))
        self.assertEqual(rows[0]["event_type"], "dry_run_order_plan")
        self.assertFalse(rows[0]["real_order_submitted"])
        self.assertFalse(rows[0]["counts_as_successful_buy"])
        self.assertEqual(state.planned_order_count, 1)
        self.assertAlmostEqual(state.planned_cost, 1.0)

    def test_live_submit_requires_explicit_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            with self.assertRaisesRegex(RuntimeError, "live_ack_required"):
                sniper.submit_live_order(
                    make_plan(),
                    env_file=".env",
                    ledger_path=str(ledger),
                    client=FakeClient({"success": True}),
                    sdk=FAKE_SDK,
                )

    def test_live_submit_writes_ledger_and_calls_market_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            client = FakeClient({"success": True, "orderID": "abc"})
            result = sniper.submit_live_order(
                make_plan(),
                env_file=".env",
                ledger_path=str(ledger),
                live_ack=True,
                client=client,
                sdk=FAKE_SDK,
            )
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(result.submitted)
        self.assertEqual(result.status, "submitted_final_or_accepted")
        self.assertTrue(result.counts_as_successful_buy)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event_type"], "live_order_plan")
        self.assertEqual(rows[1]["event_type"], "live_order_submitted")
        self.assertTrue(rows[1]["real_order_submitted"])

    def test_live_submit_ambiguous_response_stops_after_ledger_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "live_orders.jsonl"
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                sniper.submit_live_order(
                    make_plan(),
                    env_file=".env",
                    ledger_path=str(ledger),
                    live_ack=True,
                    client=FakeClient({"status": "open"}),
                    sdk=FAKE_SDK,
                )
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["event_type"], "live_order_submitted")
        self.assertTrue(rows[-1]["stop_required"])
        self.assertFalse(rows[-1]["counts_as_successful_buy"])

    def test_non_executable_plan_never_counts_as_buy(self):
        result = sniper.non_executable_result(
            make_plan(
                allow_submit=False,
                reason="missing_book_ask",
                token_quote=sniper.TokenQuote(
                    token_id="123",
                    side="yes",
                    buy_price=None,
                    book_ask_price=None,
                    book_ask_size=None,
                    book_endpoint_delta=None,
                    entry_price=None,
                    entry_price_source=None,
                ),
                estimated_cost=None,
            ),
            ledger_path="session/live.jsonl",
        )
        self.assertFalse(result.submitted)
        self.assertFalse(result.counts_as_successful_buy)
        self.assertEqual(result.status, "not_submitted_non_executable")
        self.assertEqual(result.reason, "missing_book_ask")


if __name__ == "__main__":
    unittest.main()
