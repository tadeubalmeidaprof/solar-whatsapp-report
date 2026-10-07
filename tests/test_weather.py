import unittest
from datetime import date
from unittest.mock import Mock, patch

from weather import (
    OPEN_METEO_ARCHIVE_URL,
    OPEN_METEO_FORECAST_URL,
    WeatherRateLimitError,
    _open_meteo_url,
    get_weather_window,
    get_hourly_weather_rows,
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

    @patch("weather.get_hourly_weather_rows")
    def test_window_defaults_to_seven_until_seventeen(self, hourly):
        rows = []
        for hour in range(24):
            rows.append(
                {
                    "time": f"2026-10-07T{hour:02d}:00",
                    "temperature_c": 20 + hour / 10,
                    "precipitation_mm": 0.1 if hour == 10 else 0,
                    "cloud_cover_percent": 30,
                    "shortwave_radiation_w_m2": 500 if 7 <= hour < 17 else 0,
                    "sunshine_seconds": 3000 if 7 <= hour < 17 else 0,
                    "is_day": 1 if 6 <= hour < 18 else 0,
                    "condition_text": None,
                }
            )
        hourly.return_value = (rows, "open-meteo")

        result = get_weather_window(
            latitude=-14.2,
            longitude=-42.2,
            report_date=date(2026, 10, 7),
        )

        self.assertEqual(result["window_label"], "07:00-17:00")
        self.assertEqual(result["hours_with_data"], 10)
        self.assertEqual(result["solar_radiation_wh_m2"], 5000.0)
        self.assertAlmostEqual(result["sunshine_hours"], 8.33, places=2)
        self.assertEqual(result["total_precipitation_mm"], 0.1)

    @patch("weather._weatherapi_hourly_rows")
    @patch("weather._open_meteo_hourly_rows")
    def test_weatherapi_is_fallback_after_open_meteo_rate_limit(
        self,
        open_meteo,
        weatherapi,
    ):
        open_meteo.side_effect = WeatherRateLimitError("429")
        weatherapi.return_value = [
            {
                "time": "2026-10-07 07:00",
                "temperature_c": 25,
                "precipitation_mm": 0,
                "cloud_cover_percent": 20,
                "shortwave_radiation_w_m2": None,
                "sunshine_seconds": None,
                "is_day": 1,
                "condition_text": "Parcialmente nublado",
            }
        ]

        with patch.dict("os.environ", {"WEATHERAPI_KEY": "test-key"}, clear=True):
            rows, source = get_hourly_weather_rows(
                latitude=-14.2,
                longitude=-42.2,
                report_date=date(2026, 10, 7),
            )

        self.assertEqual(source, "weatherapi")
        self.assertEqual(len(rows), 1)

    def test_rejects_inverted_time_window(self):
        with self.assertRaises(ValueError):
            get_weather_window(
                latitude=-14.2,
                longitude=-42.2,
                report_date=date(2026, 10, 7),
                start_hour=17,
                end_hour=7,
            )


if __name__ == "__main__":
    unittest.main()
