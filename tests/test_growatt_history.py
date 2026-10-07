import unittest
from datetime import date
from unittest.mock import Mock, patch

from growatt_client import (
    _extract_generation_history_rows,
    fetch_generation_history,
)


class GrowattGenerationHistoryTests(unittest.TestCase):
    def test_extracts_official_plant_energy_history_shape(self):
        payload = {
            "data": {
                "count": 2,
                "time_unit": "day",
                "energys": [
                    {"date": "2026-10-05", "energy": 42.1},
                    {"date": "2026-10-06", "energy": "43.7"},
                ],
            },
            "error_code": 0,
            "error_msg": "",
        }

        self.assertEqual(
            _extract_generation_history_rows(payload),
            [
                {"date": "2026-10-05", "energy_kwh": 42.1},
                {"date": "2026-10-06", "energy_kwh": 43.7},
            ],
        )

    @patch("growatt_client.growattServer.OpenApiV1")
    def test_history_is_split_into_safe_seven_day_chunks(self, api_class):
        api = Mock()
        api.server_url = ""
        api.plant_energy_history.side_effect = [
            {
                "data": {
                    "energys": [
                        {"date": "2026-10-01", "energy": 10},
                    ]
                }
            },
            {
                "data": {
                    "energys": [
                        {"date": "2026-10-08", "energy": 20},
                    ]
                }
            },
        ]
        api_class.return_value = api

        def fake_env(name, default="", required=False):
            values = {
                "GROWATT_API_TOKEN": "token",
                "GROWATT_SERVER_URL": "https://openapi.growatt.com/v1/",
            }
            return values.get(name, default)

        with patch("growatt_client.env", side_effect=fake_env):
            rows = fetch_generation_history(
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 8),
                plant_id="plant-1",
            )

        self.assertEqual(
            rows,
            [
                {"date": "2026-10-01", "energy_kwh": 10.0},
                {"date": "2026-10-08", "energy_kwh": 20.0},
            ],
        )
        self.assertEqual(api.plant_energy_history.call_count, 2)
        first_args = api.plant_energy_history.call_args_list[0].args
        second_args = api.plant_energy_history.call_args_list[1].args
        self.assertEqual(first_args[1], date(2026, 10, 1))
        self.assertEqual(first_args[2], date(2026, 10, 7))
        self.assertEqual(second_args[1], date(2026, 10, 8))
        self.assertEqual(second_args[2], date(2026, 10, 8))


if __name__ == "__main__":
    unittest.main()
