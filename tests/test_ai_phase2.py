import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from solar_queries import (
    compare_months,
    get_fault_code_info,
    get_generation_period,
    get_monthly_generation,
    get_recent_generation,
    get_savings_summary,
    get_solar_generation_hours,
    get_weather_summary,
    get_weather_window_summary,
)


class SolarQueriesPhase2Tests(unittest.TestCase):
    @patch("solar_queries.fetch_generation_history")
    @patch("solar_queries.fetch_growatt_payload")
    @patch("solar_queries.fetch_daily_generation_range")
    def test_generation_period_merges_live_today(
        self,
        fetch_range,
        fetch_live,
        fetch_history,
    ):
        fetch_range.return_value = []
        fetch_history.return_value = []
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
        self.assertEqual(result["daily"][0]["source"], "growatt_live")

    @patch("solar_queries.fetch_growatt_payload")
    @patch("solar_queries.fetch_generation_history")
    @patch("solar_queries.fetch_daily_generation_range")
    def test_generation_period_uses_growatt_history_when_database_is_missing(
        self,
        fetch_range,
        fetch_history,
        fetch_live,
    ):
        fetch_range.return_value = []
        fetch_history.return_value = [
            {"date": "2026-10-05", "energy_kwh": 10.0},
            {"date": "2026-10-06", "energy_kwh": 11.0},
        ]
        fetch_live.side_effect = RuntimeError("live unavailable")

        with patch("solar_queries._station_id", return_value="plant-1"), patch(
            "solar_queries.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value.date.return_value = date(2026, 10, 7)
            result = get_generation_period("2026-10-05", "2026-10-06")

        self.assertTrue(result["complete_history"])
        self.assertEqual(result["total_generation_kwh"], 21.0)
        self.assertTrue(all(x["source"] == "growatt_history" for x in result["daily"]))

    @patch("solar_queries.get_generation_period")
    def test_recent_generation_calculates_range_in_backend(self, period):
        period.return_value = {"complete_history": True}

        with patch("solar_queries.datetime") as mocked_datetime:
            mocked_datetime.now.return_value.date.return_value = date(2026, 10, 7)
            result = get_recent_generation(7)

        self.assertEqual(result, {"complete_history": True})
        period.assert_called_once_with(
            start_date="2026-10-01",
            end_date="2026-10-07",
        )

    @patch("solar_queries.fetch_growatt_payload")
    @patch("solar_queries.fetch_generation_for_month")
    @patch("solar_queries._station_id", return_value="plant-1")
    def test_current_month_falls_back_to_snapshot_when_live_growatt_fails(
        self,
        station_id,
        fetch_month,
        fetch_live,
    ):
        fetch_month.return_value = ("plant-1", Decimal("248.4"))
        fetch_live.side_effect = RuntimeError("Growatt temporarily unavailable")

        with patch("solar_queries.datetime", wraps=datetime) as mocked_datetime:
            mocked_datetime.now.return_value = datetime(2026, 10, 7, 18, 0)
            result = get_monthly_generation("2026-10")

        self.assertTrue(result["available"])
        self.assertEqual(result["generation_kwh"], 248.4)
        self.assertEqual(result["source"], "monthly_snapshot_fallback")

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

    @patch("solar_queries.get_generation_period")
    @patch("solar_queries.get_monthly_generation")
    def test_compare_current_month_uses_equivalent_days(
        self,
        monthly,
        period,
    ):
        monthly.side_effect = [
            {
                "available": True,
                "year_month": "2026-09",
                "generation_kwh": 1215.7,
            },
            {
                "available": True,
                "year_month": "2026-10",
                "generation_kwh": 294.1,
            },
        ]
        period.side_effect = [
            {
                "complete_history": True,
                "total_generation_kwh": 275.0,
            },
            {
                "complete_history": True,
                "total_generation_kwh": 294.1,
            },
        ]

        with patch("solar_queries.datetime", wraps=datetime) as mocked_datetime:
            mocked_datetime.now.return_value = datetime(2026, 10, 7, 19, 0)
            result = compare_months("2026-09", "2026-10")

        self.assertFalse(result["months_are_directly_comparable"])
        self.assertEqual(
            result["comparison_mode"],
            "equivalent_partial_period",
        )
        self.assertTrue(result["fair_comparison_available"])
        self.assertEqual(result["fair_comparison"]["through_day"], 7)
        self.assertEqual(
            result["fair_comparison"]["difference_kwh"],
            19.1,
        )
        period.assert_any_call(
            start_date="2026-09-01",
            end_date="2026-09-07",
        )
        period.assert_any_call(
            start_date="2026-10-01",
            end_date="2026-10-07",
        )

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

    @patch("solar_queries.get_daily_weather")
    @patch("solar_queries.fetch_daily_weather_for_date", return_value=None)
    @patch("solar_queries._station_id", return_value="plant-1")
    def test_weather_fetches_open_meteo_when_history_is_missing(
        self,
        station_id,
        fetch_weather,
        get_weather,
    ):
        get_weather.return_value = {
            "PERCENTUALNUVENS": 30,
            "CHUVAMM": 0,
            "RADIACAOSOLARWHM2": 4000,
            "HORASSOL": 8,
            "TEMPERATURAMINIMAC": 20,
            "TEMPERATURAMAXIMAC": 31,
            "CLASSIFICACAOCLIMA": "favorable",
        }

        with patch.dict(
            "os.environ",
            {
                "STATION_LATITUDE": "-14.2",
                "STATION_LONGITUDE": "-42.2",
            },
            clear=True,
        ):
            result = get_weather_summary("2026-10-06")

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "open_meteo")

    @patch("solar_queries.get_weather_window")
    def test_weather_window_summary_uses_default_solar_window(self, get_window):
        get_window.return_value = {
            "available": True,
            "window_label": "07:00-17:00",
        }

        with patch.dict(
            "os.environ",
            {
                "STATION_LATITUDE": "-14.2",
                "STATION_LONGITUDE": "-42.2",
            },
            clear=True,
        ):
            result = get_weather_window_summary("2026-10-07")

        self.assertTrue(result["available"])
        get_window.assert_called_once_with(
            latitude=-14.2,
            longitude=-42.2,
            report_date=date(2026, 10, 7),
            start_hour=7,
            end_hour=17,
        )

    @patch("solar_queries.fetch_plant_peak_power_kwp", return_value=8.0)
    @patch("solar_queries.fetch_plant_power_curve")
    @patch("solar_queries._station_id", return_value="plant-1")
    def test_solar_generation_hours_uses_real_growatt_curve(
        self,
        station_id,
        power_curve,
        peak_power,
    ):
        power_curve.return_value = [
            {"time": "2026-10-07T10:00:00-03:00", "power_w": 1000},
            {"time": "2026-10-07T10:05:00-03:00", "power_w": 4000},
            {"time": "2026-10-07T10:10:00-03:00", "power_w": 8000},
        ]

        result = get_solar_generation_hours(
            report_date="2026-10-07",
            start_hour=7,
            end_hour=17,
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "growatt_power_curve")
        self.assertEqual(result["plant_peak_power_kwp"], 8.0)
        self.assertEqual(result["samples"], 3)
        self.assertGreater(result["active_generation_hours"], 0)
        self.assertGreater(result["equivalent_full_power_hours"], 0)
        self.assertEqual(result["generation_start"], "10:00")


if __name__ == "__main__":
    unittest.main()
