import unittest
from unittest.mock import Mock, patch

import pandas as pd
from realtime_data_service import RealTimeDataService


def candles(prices, freq="D"):
    return pd.DataFrame({
        "Close": prices, "High": [6.2] * len(prices),
        "Low": [5.0] * len(prices), "Volume": [12000] * len(prices),
    }, index=pd.date_range("2026-09-21", periods=len(prices), freq=freq, tz="America/New_York"))


class LivePricesTest(unittest.TestCase):
    def setUp(self):
        self.service = RealTimeDataService()
        self.ticker = Mock()
        self.ticker.history.side_effect = lambda **kw: (
            candles([5.5, 6.0]) if kw["interval"] == "1d"
            else candles([5.99, 6.0], "min")
        )
        self.patch = patch("realtime_data_service.yf.Ticker", return_value=self.ticker)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_daily_change_and_quote_timestamp(self):
        quote = self.service.get_copper_price_live()
        self.assertEqual(quote.change, 0.5)
        self.assertEqual(quote.change_percent, 9.09)
        self.assertEqual(quote.timestamp, candles([5.99, 6.0], "min").index[-1].isoformat())
        self.assertEqual(quote.volume, "12,000")

    def test_cache_and_failure_preserve_price_and_timestamp(self):
        quote = self.service.get_copper_price_live()
        self.assertEqual(self.service.get_copper_price_live(), quote)
        self.assertEqual(self.ticker.history.call_count, 2)
        self.service._copper_checked = float("-inf")
        self.ticker.history.side_effect = RuntimeError("Yahoo unavailable")
        stale = self.service.get_copper_price_live()
        self.assertEqual(stale.status, "stale")
        self.assertEqual(stale.price, quote.price)
        self.assertEqual(stale.timestamp, quote.timestamp)

    def test_failure_without_quote_is_explicit(self):
        self.ticker.history.side_effect = RuntimeError("Yahoo unavailable")
        quote = self.service.get_copper_price_live()
        self.assertEqual(quote.status, "unavailable")
        self.assertEqual(quote.source, "Demo")
        self.assertEqual(quote.timestamp, "Unavailable")

    def test_intraday_exception_tries_next_interval(self):
        original = self.ticker.history.side_effect
        def history(**kw):
            if kw["interval"] == "1m":
                raise RuntimeError("minute data unavailable")
            return original(**kw)
        self.ticker.history.side_effect = history
        self.assertEqual(self.service.get_copper_price_live().status, "available")

    def test_invalid_rows_are_ignored(self):
        self.ticker.history.side_effect = lambda **kw: (
            candles([5.5, 6.0]) if kw["interval"] == "1d"
            else candles([6.0, float("nan")], "min")
        )
        self.assertEqual(self.service.get_copper_price_live().price, 6.0)


class DashboardTest(unittest.TestCase):
    def test_socket_events_and_static_client(self):
        import integrated_dashboard as dashboard
        quote = dict(symbol="HG=F", price=6.0, change=0.5, change_percent=9.09,
                     high=6.2, low=5.0, volume="12,000", timestamp="test",
                     source="Yahoo Finance", status="available")
        with patch.object(dashboard, "get_latest_copper_price", return_value=quote), \
             patch.object(dashboard, "get_prediction_data", return_value={}), \
             patch.object(dashboard.socketio, "start_background_task") as start:
            dashboard._background_task_started = False
            first = dashboard.socketio.test_client(dashboard.app)
            second = dashboard.socketio.test_client(dashboard.app)
            self.assertTrue(first.is_connected())
            self.assertEqual(start.call_count, 1)
            first.get_received()
            first.emit("request_update")
            events = first.get_received()
            update = next(e for e in events if e["name"] == "market_update")
            self.assertEqual(update["args"][0]["market_data"]["price"], 6.0)
            first.disconnect()
            second.disconnect()
        client = dashboard.app.test_client()
        self.assertEqual(client.get("/static/js/websocket_client_silent.js").status_code, 200)
        for status in ("available", "stale", "unavailable"):
            with patch.object(dashboard, "get_latest_copper_price",
                              return_value=dict(quote, status=status)):
                page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"data-feed-status", page.data)
            if status == "unavailable":
                self.assertIn(b"Waiting for a Yahoo Finance quote", page.data)


if __name__ == "__main__":
    unittest.main()
