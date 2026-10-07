import unittest
from datetime import date
from unittest.mock import patch

from weather import (
    OPEN_METEO_ARCHIVE_URL,
    OPEN_METEO_FORECAST_URL,
    _open_meteo_url,
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


if __name__ == "__main__":
    unittest.main()
