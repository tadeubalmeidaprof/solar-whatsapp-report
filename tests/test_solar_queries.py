import unittest
from datetime import date, datetime
from unittest.mock import patch

from solar_queries import (
    get_active_faults_summary,
    get_generation_summary,
    get_maintenance_status,
    get_plant_status,
)


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

    @patch("solar_queries.fetch_growatt_payload")
    def test_plant_status_exposes_only_safe_operational_fields(self, fetch_payload):
        fetch_payload.return_value = {
            "plantId": "secret-plant-id",
            "deviceSn": "secret-device-sn",
            "status": "Normal",
            "powerNowKw": 3.82,
            "energyTodayKwh": 18.4,
            "energyMonthKwh": 312.7,
            "energyYearKwh": 2450.1,
            "energyTotalKwh": 9120.8,
        }

        result = get_plant_status()

        self.assertEqual(result["status"], "Normal")
        self.assertEqual(result["power_now_kw"], 3.82)
        self.assertNotIn("plantId", result)
        self.assertNotIn("deviceSn", result)
        self.assertNotIn("secret-plant-id", str(result))
        self.assertNotIn("secret-device-sn", str(result))

    @patch("solar_queries.fetch_active_growatt_faults")
    def test_active_faults_are_sanitized_and_limited(self, fetch_faults):
        fetch_faults.return_value = [
            {
                "fault_code_raw": str(index),
                "fault_message": f"Falha {index}",
                "fault_time": datetime(2026, 10, index, 10, 0),
                "status": "active",
                "raw_payload": {"sensitive": "ignore"},
                "device_sn": "hidden",
            }
            for index in range(1, 7)
        ]

        result = get_active_faults_summary()

        self.assertEqual(result["active_fault_count"], 6)
        self.assertEqual(len(result["faults"]), 5)
        self.assertEqual(result["faults"][0]["code"], "2")
        self.assertNotIn("raw_payload", str(result))
        self.assertNotIn("hidden", str(result))

    @patch("solar_queries.fetch_open_maintenance_alert")
    def test_maintenance_status_returns_sanitized_open_alerts(self, fetch_alert):
        fetch_alert.side_effect = [
            {
                "alert_type": "possible_soiling",
                "severity": "warning",
                "status": "integrator_notified",
                "drop_percentage": 31.2,
                "expected_generation_kwh": 28.0,
                "observed_generation_kwh": 19.3,
                "probable_cause": "Possível sujeira.",
                "reference_start_date": date(2026, 10, 1),
                "reference_end_date": date(2026, 10, 5),
                "details": {"internal": "not-exposed"},
            },
            None,
        ]

        with patch.dict("os.environ", {"GROWATT_PLANT_ID": "plant-1"}, clear=True):
            result = get_maintenance_status()

        self.assertTrue(result["has_open_alert"])
        self.assertEqual(len(result["alerts"]), 1)
        self.assertEqual(result["alerts"][0]["drop_percentage"], 31.2)
        self.assertNotIn("not-exposed", str(result))
        self.assertEqual(fetch_alert.call_count, 2)


if __name__ == "__main__":
    unittest.main()
