import json
import os
import unittest

from app import create_app
from core.historical_backtest import INITIAL_CAPITAL, RESULT_PATH


class HistoricalBacktestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(RESULT_PATH):
            raise unittest.SkipTest("history_backtest.json 尚未生成")
        with open(RESULT_PATH, "r", encoding="utf-8") as handle:
            cls.payload = json.load(handle)

    def test_annual_returns_compound_to_summary(self):
        for scheme in ["A", "B", "C"]:
            result = self.payload["schemes"][scheme]
            compounded = 1.0
            previous_end = INITIAL_CAPITAL
            for row in result["annual"]:
                self.assertAlmostEqual(row["start_asset"], previous_end, places=2)
                compounded *= 1 + row["return_pct"] / 100
                previous_end = row["end_asset"]
            self.assertAlmostEqual((compounded - 1) * 100,
                                   result["summary"]["cumulative_return_pct"], delta=0.08)

    def test_history_api(self):
        client = create_app().test_client()
        response = client.get("/api/historical?scheme=B")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["scheme"], "B")
        self.assertEqual(data["initial_capital"], INITIAL_CAPITAL)
        self.assertEqual(len(data["annual"]), 6)
        self.assertEqual(data["quality"]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
