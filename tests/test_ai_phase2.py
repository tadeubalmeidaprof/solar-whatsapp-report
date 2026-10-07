import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from solar_queries import (
    compare_months,
    get_fault_code_info,
    get_generation_period,
    get_savings_summary,
    get_weather_summary,
)


class SolarQueriesPhase2Tests(unittest.TestCase):
    @patch("solar_queries.fetch_growatt_payload")
    @patch("solar_queries.fetch_daily_generation_range")
    def test_generation_period_merges_live_today(self, fetch_range, fetch_live):
        fetch_range.return_value = []
        fetch_live.return_value = {
            "plantId": "plant-1",
            "energyTodayKwh": 12.5,
            "status": "Normal",
        }

        with patch("solar_queries._station_id", return_value="plant-1"), patch(
            "solar_queries.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value.date.return_value = date(2026, 10, 7)
            result = get_generation_period("2026-10-07", "2026-10-07")

        self.assertTrue(result["complete_history"])
        self.assertEqual(result["total_generation_kwh"], 12.5)

    @patch("solar_queries.get_monthly_generation")
    def test_compare_months_calculates_percentage(self, monthly):
        monthly.side_effect = [
            {
                "available": True,
                "year_month": "2026-08",
                "generation_kwh": 100.0,
            },
            {
                "available": True,
                "year_month": "2026-09",
                "generation_kwh": 125.0,
            },
        ]

        result = compare_months("2026-08", "2026-09")

        self.assertEqual(result["difference_kwh"], 25.0)
        self.assertEqual(result["difference_percent_relative_to_first"], 25.0)

    @patch("solar_queries.calculate_savings_without_fio_b")
    @patch("solar_queries.get_monthly_generation")
    def test_savings_uses_safe_simplified_mode_when_fio_b_missing(
        self,
        monthly,
        calculate,
    ):
        monthly.return_value = {
            "available": True,
            "year_month": "2026-09",
            "generation_kwh": 100.0,
        }
        calculate.return_value = {
            "estimated_savings": Decimal("80.00"),
            "compensated_energy_kwh": Decimal("100"),
            "generated_credits_kwh": Decimal("0"),
        }

        with patch.dict(
            "os.environ",
            {"ENERGY_TARIFF": "0.80"},
            clear=True,
        ):
            result = get_savings_summary("2026-09")

        self.assertTrue(result["available"])
        self.assertEqual(result["calculation_mode"], "estimativa_sem_fio_b")
        self.assertEqual(result["estimated_savings_brl"], 80.0)

    def test_known_fault_code_has_safe_guidance(self):
        result = get_fault_code_info("302(00)")

        self.assertTrue(result["known"])
        self.assertIn("rede AC", result["error_meaning"])
        self.assertTrue(result["guidance"])

    @patch("solar_queries.fetch_daily_weather_for_date")
    @patch("solar_queries._station_id", return_value="plant-1")
    def test_weather_uses_stored_history(self, station_id, fetch_weather):
        fetch_weather.return_value = {
            "cloud_cover_percent": 20,
            "rainfall_mm": 0,
            "solar_radiation_wh_m2": 4200,
            "sunshine_hours": 8.5,
            "temperature_min_c": 20,
            "temperature_max_c": 31,
            "weather_class": "favorable",
        }

        result = get_weather_summary("2026-10-01")

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "monitoring_history")
        self.assertEqual(result["weather_class"], "favorable")


if __name__ == "__main__":
    unittest.main()
