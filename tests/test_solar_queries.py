import unittest
from unittest.mock import patch

from solar_queries import get_generation_summary


class SolarQueriesTests(unittest.TestCase):
    @patch("solar_queries.fetch_growatt_payload")
    def test_formats_daily_and_monthly_generation(self, fetch_payload):
        fetch_payload.return_value = {
            "energyTodayKwh": 18.42,
            "energyMonthKwh": 312.67,
        }

        summary = get_generation_summary()

        self.assertEqual(summary["today_kwh"], "18,4")
        self.assertEqual(summary["month_kwh"], "312,7")


if __name__ == "__main__":
    unittest.main()
