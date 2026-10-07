import unittest
from datetime import date
from unittest.mock import Mock, patch

from weather import (
    OPEN_METEO_ARCHIVE_URL,
    OPEN_METEO_FORECAST_URL,
    _open_meteo_url,
    get_daily_weather,
)


class WeatherSourceTests(unittest.TestCase):
    def test_recent_date_uses_forecast_endpoint(self):
        with patch("weather.date") as mocked_date:
            mocked_date.today.return_value = date(2026, 10, 7)
            self.assertEqual(
                _open_meteo_url(date(2026, 10, 6)),
                OPEN_METEO_FORECAST_URL,
            )

    def test_older_date_uses_archive_endpoint(self):
        with patch("weather.date") as mocked_date:
            mocked_date.today.return_value = date(2026, 10, 7)
            self.assertEqual(
                _open_meteo_url(date(2026, 9, 1)),
                OPEN_METEO_ARCHIVE_URL,
            )

    @patch("weather.requests.get")
    def test_daily_weather_uses_hourly_cloud_cover_average(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "daily": {
                "time": ["2026-10-07"],
                "temperature_2m_max": [31],
                "temperature_2m_min": [20],
                "precipitation_sum": [0],
                "shortwave_radiation_sum": [18],
                "sunshine_duration": [28800],
            },
            "hourly": {
                "cloud_cover": [10, 20, 30, 40],
            },
        }
        get.return_value = response

        result = get_daily_weather(
            latitude=-14.2,
            longitude=-42.2,
            report_date=date(2026, 10, 7),
        )

        self.assertEqual(result["PERCENTUALNUVENS"], 25.0)
        self.assertEqual(result["CHUVAMM"], 0.0)
        _, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["hourly"], "cloud_cover")
        self.assertNotIn(
            "cloud_cover_mean",
            kwargs["params"]["daily"],
        )


if __name__ == "__main__":
    unittest.main()
