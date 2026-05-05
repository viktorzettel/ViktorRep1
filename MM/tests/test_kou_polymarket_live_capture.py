import json
import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kou_polymarket_live_capture as capture


def make_market(**overrides):
    payload = {
        "slug": "eth-updown-5m-1776604500",
        "question": "Ethereum Up or Down",
        "asset": "eth",
        "interval_minutes": 5,
        "token_yes": "yes-token",
        "token_no": "no-token",
        "yes_label": "YES",
        "no_label": "NO",
        "start_ts": 1776604500.0,
        "end_ts": 1776604800.0,
        "accepting_orders": True,
        "active": True,
        "closed": False,
        "liquidity": 1000.0,
    }
    payload.update(overrides)
    return capture.MarketCandidate(**payload)


class TestPaperFill(unittest.TestCase):
    def test_full_taker_fill_for_good_buy_yes(self):
        result = capture.classify_paper_fill(
            signal_state="BUY_YES",
            safety_label="GOOD",
            status="LIVE",
            accepting_orders=True,
            yes_ask=0.62,
            yes_ask_size=8.0,
            no_ask=0.42,
            no_ask_size=4.0,
            paper_size=5.0,
        )

        self.assertEqual(result["fill_status"], "full")
        self.assertEqual(result["raw_signal_side"], "yes")
        self.assertTrue(result["policy_would_buy"])
        self.assertEqual(result["fillable_size"], 5.0)
        self.assertEqual(result["entry_price"], 0.62)
        self.assertEqual(result["estimated_cost"], 3.1)

    def test_partial_taker_fill_for_buy_no(self):
        result = capture.classify_paper_fill(
            signal_state="BUY_NO",
            safety_label="OK",
            status="LIVE",
            accepting_orders=True,
            yes_ask=0.67,
            yes_ask_size=10.0,
            no_ask=0.36,
            no_ask_size=2.0,
            paper_size=5.0,
        )

        self.assertEqual(result["fill_status"], "partial")
        self.assertEqual(result["raw_signal_side"], "no")
        self.assertFalse(result["policy_would_buy"])
        self.assertEqual(result["fillable_size"], 2.0)
        self.assertEqual(result["entry_price"], 0.36)

    def test_none_when_no_ask(self):
        result = capture.classify_paper_fill(
            signal_state="BUY_YES",
            safety_label="GOOD",
            status="LIVE",
            accepting_orders=True,
            yes_ask=None,
            yes_ask_size=None,
            no_ask=0.44,
            no_ask_size=9.0,
            paper_size=5.0,
        )

        self.assertEqual(result["fill_status"], "none")
        self.assertEqual(result["reason"], "no_ask")

    def test_none_when_no_signal(self):
        result = capture.classify_paper_fill(
            signal_state="HOLD",
            safety_label="GOOD",
            status="LIVE",
            accepting_orders=True,
            yes_ask=0.62,
            yes_ask_size=8.0,
            no_ask=0.42,
            no_ask_size=4.0,
            paper_size=5.0,
        )

        self.assertEqual(result["fill_status"], "none")
        self.assertEqual(result["reason"], "no_signal")
        self.assertIsNone(result["raw_signal_side"])
        self.assertFalse(result["policy_would_buy"])

    def test_none_when_market_not_live(self):
        result = capture.classify_paper_fill(
            signal_state="BUY_YES",
            safety_label="GOOD",
            status="ENDED",
            accepting_orders=True,
            yes_ask=0.62,
            yes_ask_size=8.0,
            no_ask=0.42,
            no_ask_size=4.0,
            paper_size=5.0,
        )

        self.assertEqual(result["fill_status"], "none")
        self.assertEqual(result["reason"], "no_live_market")

    def test_entry_price_prefers_observed_buy_price(self):
        result = capture.classify_paper_fill(
            signal_state="BUY_YES",
            safety_label="GOOD",
            status="LIVE",
            accepting_orders=True,
            yes_ask=0.63,
            yes_ask_size=10.0,
            no_ask=0.39,
            no_ask_size=10.0,
            paper_size=5.0,
            yes_buy_price=0.61,
        )

        self.assertEqual(result["fill_status"], "full")
        self.assertEqual(result["entry_price"], 0.61)
        self.assertEqual(result["entry_price_source"], "clob_price_buy")
        self.assertEqual(result["book_ask_price"], 0.63)
        self.assertEqual(result["book_endpoint_delta"], 0.02)


class TestBookAndPriceCapture(unittest.TestCase):
    def test_fetch_book_top_sorts_best_bid_and_ask(self):
        class Order:
            def __init__(self, price, size):
                self.price = price
                self.size = size

        class Book:
            bids = [Order("0.01", "100"), Order("0.31", "7")]
            asks = [Order("0.99", "200"), Order("0.33", "8")]

        class Client:
            def get_order_book(self, token_id):
                return Book()

        top = capture.fetch_book_top(Client(), "token")

        self.assertEqual(top.bid, 0.31)
        self.assertEqual(top.bid_size, 7.0)
        self.assertEqual(top.ask, 0.33)
        self.assertEqual(top.ask_size, 8.0)

    def test_fetch_book_top_keeps_buy_ask_when_bid_side_empty(self):
        class Order:
            def __init__(self, price, size):
                self.price = price
                self.size = size

        class Book:
            bids = []
            asks = [Order("0.91", "4"), Order("0.89", "11")]

        class Client:
            def get_order_book(self, token_id):
                return Book()

        top = capture.fetch_book_top(Client(), "token")

        self.assertEqual(top.bid, 0.0)
        self.assertEqual(top.bid_size, 0.0)
        self.assertEqual(top.ask, 0.89)
        self.assertEqual(top.ask_size, 11.0)

    def test_token_price_payload_sums_observed_prices(self):
        payload = capture.build_token_prices_payload(
            {"buy_price": 0.61},
            {"buy_price": 0.42},
        )

        self.assertEqual(payload["yes"]["buy_price"], 0.61)
        self.assertEqual(payload["no"]["buy_price"], 0.42)
        self.assertEqual(payload["buy_price_sum"], 1.03)
        self.assertNotIn("midpoint_sum", payload)

    def test_book_payload_keeps_only_execution_needed_fields(self):
        payload = capture.build_book_payload(
            capture.BookTop(bid=0.6, ask=0.62, bid_size=20.0, ask_size=9.0),
            capture.BookTop(bid=0.39, ask=0.41, bid_size=15.0, ask_size=10.0),
        )

        self.assertEqual(payload["yes"], {"ask": 0.62, "ask_size": 9.0})
        self.assertEqual(payload["no"], {"ask": 0.41, "ask_size": 10.0})
        self.assertNotIn("bid", payload["yes"])
        self.assertNotIn("mid_sum", payload)


class TestSessionResolution(unittest.TestCase):
    def write_meta(self, root: Path, session_id: str, *, stopped: bool, started_at: float = 100.0) -> Path:
        session_dir = root / session_id
        session_dir.mkdir(parents=True)
        meta = {
            "session_id": session_id,
            "started_at_ts": started_at,
        }
        if stopped:
            meta["stopped_at_ts"] = started_at + 10.0
        (session_dir / "session_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return session_dir

    def test_explicit_session_id_creates_or_uses_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = capture.resolve_output_session(root, "manual-session")

            self.assertEqual(result.session_id, "manual-session")
            self.assertEqual(result.mode, "explicit")
            self.assertTrue(result.output_dir.exists())

    def test_attaches_to_one_active_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_meta(root, "old-stopped", stopped=True, started_at=50.0)
            active_dir = self.write_meta(root, "active", stopped=False, started_at=100.0)

            result = capture.resolve_output_session(root, None)

            self.assertEqual(result.session_id, "active")
            self.assertEqual(result.mode, "attached_active")
            self.assertEqual(result.output_dir, active_dir)

    def test_creates_standalone_when_no_active_session_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_meta(root, "old-stopped", stopped=True, started_at=50.0)

            result = capture.resolve_output_session(root, None, now_ts=1776604500.0)

            self.assertEqual(result.mode, "standalone_new")
            self.assertEqual(result.session_id, "20260419T131500Z")
            self.assertTrue(result.output_dir.exists())

    def test_refuses_multiple_active_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_meta(root, "active-a", stopped=False, started_at=100.0)
            self.write_meta(root, "active-b", stopped=False, started_at=200.0)

            with self.assertRaises(RuntimeError):
                capture.resolve_output_session(root, None)


class TestSnapshotMapping(unittest.TestCase):
    def test_asset_from_symbol_and_selection(self):
        payload = {
            "assets": [
                {"symbol": "ethusdt", "signal": "BUY_YES"},
                {"symbol": "xrpusdt", "signal": "BUY_NO"},
                {"symbol": "btcusdt", "signal": "BUY_YES"},
            ]
        }

        self.assertEqual(capture.asset_from_symbol("ETH/USDT"), "eth")
        self.assertEqual(capture.asset_from_symbol("xrp-usd"), "xrp")
        self.assertIsNone(capture.asset_from_symbol("btcusdt"))

        auto = capture.selected_snapshot_assets(payload, None)
        self.assertEqual([asset for asset, _snapshot in auto], ["eth", "xrp"])

        xrp_only = capture.selected_snapshot_assets(payload, {"xrp"})
        self.assertEqual([asset for asset, _snapshot in xrp_only], ["xrp"])

    def test_extract_kou_ref_uses_asset_bucket_then_payload_bucket(self):
        payload = {"bucket_end": 2000.0, "time_left_s": 44.0}
        asset_snapshot = {
            "symbol": "ethusdt",
            "price": 3100.0,
            "strike": 3090.0,
            "signal": "BUY_YES",
            "kou_yes": 0.61,
            "bs_yes": 0.58,
            "trade_score_label": "GOOD",
            "late_policy_margin_z": 1.4,
        }

        kou_ref = capture.extract_kou_ref(payload, asset_snapshot)

        self.assertEqual(kou_ref["symbol"], "ethusdt")
        self.assertEqual(kou_ref["bucket_end"], 2000.0)
        self.assertEqual(kou_ref["time_left_s"], 44.0)
        self.assertEqual(kou_ref["signal"], "BUY_YES")
        self.assertEqual(kou_ref["kou_yes"], 0.61)
        self.assertNotIn("safety_components", kou_ref)
        self.assertNotIn("raw_kou_yes", kou_ref)


class TestMarketAlignment(unittest.TestCase):
    def test_aligned_when_close_offset_is_small(self):
        market = make_market(end_ts=1776604800.0)
        delta = capture.market_end_delta_s(1776604799.0, market)

        self.assertEqual(delta, -1.0)
        self.assertEqual(capture.market_alignment_status(delta), "aligned")

    def test_warning_when_materially_different(self):
        market = make_market(end_ts=1776604800.0)
        delta = capture.market_end_delta_s(1776604770.0, market)

        self.assertEqual(delta, -30.0)
        self.assertEqual(capture.market_alignment_status(delta), "warning")


class TestDiscoveryFallback(unittest.TestCase):
    def test_slug_probe_used_when_list_has_no_live_market(self):
        live_market = make_market(start_ts=90.0, end_ts=390.0)

        with (
            mock.patch.object(capture, "discover_5m_markets", return_value=[]),
            mock.patch.object(capture, "probe_5m_markets_by_slug", return_value=[live_market]),
        ):
            result = capture.discover_current_and_next_5m_markets(
                "eth",
                120.0,
                market_limit=500,
                allow_slug_probe=True,
            )

        self.assertEqual(result.current, live_market)
        self.assertEqual(result.list_count, 0)
        self.assertEqual(result.probe_count, 1)
        self.assertTrue(result.used_slug_probe)


class TestMarketHolding(unittest.TestCase):
    def test_live_market_is_held_until_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                url="http://127.0.0.1:8071/api/snapshot",
                output_root=tmp,
                session_id="hold-test",
                env_file=".env",
                assets="auto",
                paper_size=5.0,
                grid_thresholds="0.90",
                grid_hold_seconds="2",
                grid_window_seconds=90.0,
                fine_window_seconds=120.0,
                fine_seconds=1.0,
                coarse_seconds=1.0,
                discover_seconds=1.0,
                slug_probe_seconds=20.0,
                market_limit=500,
                max_runtime_seconds=None,
                verbose=False,
                mock_polymarket=False,
            )
            bot = capture.PolymarketQuoteCapture(args)
            held = make_market(slug="eth-updown-5m-100", start_ts=100.0, end_ts=400.0)
            bot.market_states["eth"] = capture.AssetMarketState(current_market=held, last_discover_ts=100.0)

            with (
                open(Path(tmp) / "events.jsonl", "w", encoding="utf-8") as events,
                open(Path(tmp) / "markets.jsonl", "w", encoding="utf-8") as markets,
                mock.patch.object(capture, "discover_current_and_next_5m_markets") as discover,
            ):
                bot.discover_asset("eth", 250.0, events, markets)

            discover.assert_not_called()
            self.assertEqual(bot.market_states["eth"].current_market, held)

    def test_market_is_rediscovered_after_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                url="http://127.0.0.1:8071/api/snapshot",
                output_root=tmp,
                session_id="roll-test",
                env_file=".env",
                assets="auto",
                paper_size=5.0,
                grid_thresholds="0.90",
                grid_hold_seconds="2",
                grid_window_seconds=90.0,
                fine_window_seconds=120.0,
                fine_seconds=1.0,
                coarse_seconds=1.0,
                discover_seconds=1.0,
                slug_probe_seconds=20.0,
                market_limit=500,
                max_runtime_seconds=None,
                verbose=False,
                mock_polymarket=False,
            )
            bot = capture.PolymarketQuoteCapture(args)
            old = make_market(slug="eth-updown-5m-100", start_ts=100.0, end_ts=400.0)
            new = make_market(slug="eth-updown-5m-400", start_ts=400.0, end_ts=700.0)
            bot.market_states["eth"] = capture.AssetMarketState(current_market=old, last_discover_ts=100.0)

            with (
                open(Path(tmp) / "events.jsonl", "w", encoding="utf-8") as events,
                open(Path(tmp) / "markets.jsonl", "w", encoding="utf-8") as markets,
                mock.patch.object(
                    capture,
                    "discover_slug_first_current_and_next_5m_markets",
                    return_value=capture.DiscoveryResult(new, None, 1, 0, False),
                ) as discover,
            ):
                bot.discover_asset("eth", 401.0, events, markets)

            discover.assert_called_once()
            self.assertEqual(bot.market_states["eth"].current_market, new)


class TestDecisionGrid(unittest.TestCase):
    def make_capture(self, tmp_root: Path) -> capture.PolymarketQuoteCapture:
        args = argparse.Namespace(
            url="http://127.0.0.1:8071/api/snapshot",
            output_root=str(tmp_root),
            session_id="grid-test",
            env_file=".env",
            assets="auto",
            paper_size=5.0,
            grid_thresholds="0.90",
            grid_hold_seconds="2",
            grid_window_seconds=90.0,
            fine_window_seconds=120.0,
            fine_seconds=1.0,
            coarse_seconds=1.0,
            discover_seconds=5.0,
            slug_probe_seconds=20.0,
            market_limit=500,
            max_runtime_seconds=None,
            verbose=False,
        )
        return capture.PolymarketQuoteCapture(args)

    def make_record(self, captured_at_ts: float) -> dict:
        return {
            "session": {
                "id": "grid-test",
                "captured_at_ts": captured_at_ts,
                "captured_at_iso": capture.utc_iso(captured_at_ts),
                "capture_interval_s": 1.0,
            },
            "kou_ref": {
                "symbol": "ethusdt",
                "bucket_end": 200.0,
                "time_left_s": 80.0,
                "kou_yes": 0.92,
                "signal": None,
                "trade_score_label": "GOOD",
            },
            "grid_context": {
                "safety": {"final_label": "GOOD", "weakest_component": "trend"},
                "policy": {"level": "CLEAR", "margin_z": 1.2, "override": False},
            },
        }

    def test_grid_emits_trigger_after_hold_with_observed_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = self.make_capture(Path(tmp))
            market = make_market(slug="eth-updown-5m-200", start_ts=0.0, end_ts=200.0)
            book = capture.build_book_payload(
                capture.BookTop(bid=0.6, ask=0.62, bid_size=20.0, ask_size=9.0),
                capture.BookTop(bid=0.39, ask=0.41, bid_size=15.0, ask_size=10.0),
            )
            token_prices = capture.build_token_prices_payload(
                {"buy_price": 0.61},
                {"buy_price": 0.42},
            )

            first = bot.evaluate_grid(
                record=self.make_record(100.0),
                asset="eth",
                market=market,
                book=book,
                token_prices=token_prices,
            )
            bot.remember_snapshot(
                symbol="ethusdt",
                captured_at_ts=101.0,
                asset_snapshot={"delta_bps": -1.0, "late_policy_margin_z": 0.9, "kou_yes": 0.2},
            )
            bot.remember_snapshot(
                symbol="ethusdt",
                captured_at_ts=102.0,
                asset_snapshot={"delta_bps": 2.0, "late_policy_margin_z": 1.2, "kou_yes": 0.92},
            )
            second = bot.evaluate_grid(
                record=self.make_record(102.0),
                asset="eth",
                market=market,
                book=book,
                token_prices=token_prices,
            )

        self.assertEqual(first, [])
        self.assertEqual(len(second), 1)
        event = second[0]
        self.assertEqual(event["rule"]["threshold"], 0.9)
        self.assertEqual(event["rule"]["hold_seconds"], 2)
        self.assertEqual(event["trigger"]["side"], "yes")
        self.assertEqual(event["decision_context"]["safety"]["final_label"], "GOOD")
        self.assertEqual(event["decision_context"]["policy"]["level"], "CLEAR")
        self.assertEqual(event["pre_trigger_path"]["last_15s"]["cross_count"], 1)
        self.assertEqual(event["pre_trigger_path"]["last_15s"]["margin_z_change"], 0.3)
        self.assertEqual(event["observed_token"]["entry_price"], 0.61)
        self.assertEqual(event["observed_token"]["fill_status"], "full")
        self.assertEqual(event["observed_token"]["pnl_per_share_if_win"], 0.39)


class TestShadowExecution(unittest.TestCase):
    def make_grid_event(self) -> dict:
        return {
            "session": {
                "id": "shadow-test",
                "captured_at_ts": 1776604790.0,
                "captured_at_iso": "2026-04-19T13:19:50Z",
            },
            "asset": "xrp",
            "symbol": "xrpusdt",
            "bucket_end": 1776604800.0,
            "bucket_end_iso": "2026-04-19T13:20:00Z",
            "time_left_s": 10.0,
            "market_slug": "xrp-updown-5m-1776604500",
            "rule": {"threshold": 0.9, "hold_seconds": 2, "window_seconds": 90.0},
            "trigger": {"side": "no", "side_probability": 0.93, "kou_yes": 0.07},
            "decision_context": {
                "safety": {"final_label": "GOOD", "final_score": 101, "weakest_component": "trend"},
                "policy": {"level": "CLEAR", "margin_z": 1.8, "override": False},
            },
            "pre_trigger_path": {
                "last_60s": {"cross_count": 0, "adverse_sample_share": 0.0, "margin_z_change": 1.2},
            },
            "observed_token": {
                "entry_price": 0.9,
                "entry_price_source": "clob_price_buy",
                "book_ask_price": 0.91,
                "book_ask_size": 8.0,
                "endpoint_buy_price": 0.9,
                "fill_status": "full",
                "fillable_size": 5.0,
                "estimated_cost": 4.5,
                "pnl_per_share_if_win": 0.1,
                "pnl_per_share_if_loss": -0.9,
            },
        }

    def test_grid_event_candidate_row_flattens_shadow_inputs(self):
        row = capture.grid_event_candidate_row(self.make_grid_event())

        self.assertEqual(row["asset"], "xrp")
        self.assertEqual(row["symbol"], "xrpusdt")
        self.assertEqual(row["side"], "no")
        self.assertEqual(row["threshold"], 0.9)
        self.assertEqual(row["entry_price"], 0.9)
        self.assertEqual(row["path_60s_adverse_share"], 0.0)
        self.assertEqual(row["path_60s_margin_z_change"], 1.2)
        self.assertEqual(row["policy_margin_z"], 1.8)

    def test_shadow_order_and_settlement_are_read_only_paper_records(self):
        event = self.make_grid_event()
        candidate = {"name": "unit_candidate", "path": "/tmp/unit_candidate.py"}
        decision = {"allow_trade": True, "reason": "unit_allow"}
        order = capture.build_shadow_order(
            event=event,
            candidate=candidate,
            candidate_row=capture.grid_event_candidate_row(event),
            decision=decision,
            paper_size=5.0,
        )

        self.assertIsNotNone(order)
        assert order is not None
        self.assertFalse(order["real_order_submitted"])
        self.assertEqual(order["mode"], "read_only_shadow_no_order")
        self.assertEqual(order["source_grid_event"]["captured_at_iso"], "2026-04-19T13:19:50Z")
        self.assertEqual(order["order"]["side"], "no")
        self.assertEqual(order["order"]["requested_size_cost"], 4.5)

        sniper_signal = capture.build_sniper_signal_from_shadow_order(order)
        self.assertIsNotNone(sniper_signal)
        assert sniper_signal is not None
        self.assertEqual(sniper_signal["symbol"], "xrpusdt")
        self.assertEqual(sniper_signal["side"], "no")
        self.assertEqual(sniper_signal["max_entry_price"], 0.9)
        self.assertEqual(sniper_signal["market_slug"], "xrp-updown-5m-1776604500")
        self.assertEqual(sniper_signal["bucket_end"], 1776604800.0)
        self.assertLessEqual(sniper_signal["expires_at"], 1776604795.0)

        settlement = capture.build_shadow_settlement(
            order,
            {
                "complete": True,
                "settled_side": "no",
                "settled_yes": False,
                "bucket_end": 1776604800.0,
                "bucket_end_iso": "2026-04-19T13:20:00Z",
                "sample_count": 120,
            },
        )

        self.assertEqual(settlement["event_type"], "shadow_settlement")
        self.assertTrue(settlement["result"]["win"])
        self.assertEqual(settlement["result"]["pnl_per_share"], 0.1)
        self.assertEqual(settlement["result"]["paper_pnl_requested_size"], 0.5)


if __name__ == "__main__":
    unittest.main()
