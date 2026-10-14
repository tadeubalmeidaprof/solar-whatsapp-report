import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from generation_model import Site, analyze_generation, integrate_hourly, _features
from generation_explanation import format_generation_explanation, get_generation_explanation, load_site
from ai.intent_router import detect_local_intent


TZ = ZoneInfo("America/Bahia")
SITE = Site(-14.273, -42.258, 8.4, 8)


def synthetic(days=24):
    data, weather = [], []
    first = date(2026, 9, 1)
    for index in range(days):
        day = first + timedelta(days=index)
        factor = 0.35 + (index * 7 % 13) / 13
        hours = []
        for hour in range(8, 16):
            stamp = datetime(day.year, day.month, day.day, hour, tzinfo=TZ)
            ghi = (800 - abs(hour - 12) * 100) * factor
            power = min(8, 8.4 * ghi / 1000 * 0.82)
            hours.append({"timestamp": stamp, "power_kw": power,
                          "energy_kwh": power, "duration_hours": 1.0})
            weather.append({"timestamp": stamp + timedelta(hours=1),
                            "shortwave_radiation_w_m2": ghi, "temperature_c": 26,
                            "direct_normal_irradiance_w_m2": 400,
                            "diffuse_radiation_w_m2": 100, "wind_speed_m_s": 2})
        data.append({"date": day, "hours": hours})
    return data, weather


class GenerationModelTests(unittest.TestCase):
    def test_confirmed_hardware_defaults(self):
        with patch.dict("os.environ", {"STATION_LATITUDE": "-14.273", "STATION_LONGITUDE": "-42.258"}, clear=True):
            site = load_site()
        self.assertEqual(site.dc_capacity_kwp, 8.4)
        self.assertEqual(site.ac_capacity_kw, 8.0)
        self.assertEqual(site.gamma_pdc_per_c, -0.0024)
        self.assertIsNone(site.tilt)

    def test_invalid_geometry_and_nonfinite_values_rejected(self):
        for kwargs in ({"tilt": 20}, {"tilt": 95, "azimuth": 0}, {"latitude": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Site(**{**SITE.__dict__, **kwargs})

    def test_trapezoid_integrates_unequal_samples(self):
        start = datetime(2026, 10, 1, 8, tzinfo=TZ)
        points = [{"timestamp": start + timedelta(minutes=i * 10), "power_w": i * 1000}
                  for i in range(7)]
        result = integrate_hourly(points, start, start + timedelta(hours=1))
        self.assertAlmostEqual(result[0]["energy_kwh"], 3.0)
        self.assertAlmostEqual(result[0]["duration_hours"], 1.0)

    def test_long_gap_is_not_interpolated_as_generation(self):
        start = datetime(2026, 10, 1, 8, tzinfo=TZ)
        points = [{"timestamp": start, "power_w": 4000},
                  {"timestamp": start + timedelta(hours=1), "power_w": 4000}]
        self.assertEqual(integrate_hourly(points, start, start + timedelta(hours=1)), [])

    def test_radiation_matches_preceding_hour(self):
        stamp = datetime(2026, 10, 1, 9, tzinfo=TZ)
        features = _features([{"timestamp": stamp, "shortwave_radiation_w_m2": 500,
                               "temperature_c": 25}], SITE)
        self.assertIn(stamp - timedelta(hours=1), features)
        self.assertNotIn(stamp, features)

    def test_unavailable_radiation_never_becomes_zero_sun(self):
        data, weather = synthetic()
        for row in weather:
            row["shortwave_radiation_w_m2"] = None
        result = analyze_generation(data, data[-1]["date"], weather, SITE, 8)
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "insufficient_data_coverage")

    def test_short_history_cannot_claim_validated_prediction(self):
        data, weather = synthetic(10)
        result = analyze_generation(data, data[-1]["date"], weather, SITE, 8)
        self.assertEqual(result["status"], "warming_up")
        self.assertNotIn("expected_energy_kwh", result)

    def test_weather_model_beats_daily_median_on_variable_climate(self):
        data, weather = synthetic()
        result = analyze_generation(data, data[-1]["date"], weather, SITE, 8)
        self.assertTrue(result["available"])
        self.assertTrue(result["validation"]["model_beats_baseline"])
        self.assertLess(result["validation"]["normalized_rmse_percent"], 5)
        self.assertEqual(result["model_type"], "empirical_ghi_solar_geometry")

    def test_current_generation_does_not_train_its_own_expectation(self):
        data, weather = synthetic()
        target = data[-1]["date"]
        normal = analyze_generation(data, target, weather, SITE, 8)
        for row in data[-1]["hours"]:
            row["power_kw"] *= 0.4
            row["energy_kwh"] *= 0.4
        low = analyze_generation(data, target, weather, SITE, 8)
        self.assertEqual(normal["expected_energy_kwh"], low["expected_energy_kwh"])
        self.assertEqual(low["status"], "below_weather_adjusted_range")

    def test_validation_does_not_use_later_day_generation(self):
        data, weather = synthetic()
        original = analyze_generation(data, data[-1]["date"], weather, SITE, 8)
        for row in data[-2]["hours"]:
            row["power_kw"] *= 0.7
            row["energy_kwh"] *= 0.7
        changed = analyze_generation(data, data[-1]["date"], weather, SITE, 8)
        self.assertEqual(original["validation"]["predictions"][0],
                         changed["validation"]["predictions"][0])

    def test_model_cannot_displace_a_better_baseline(self):
        data, weather = synthetic()
        for day in data:
            for row in day["hours"]:
                row["power_kw"] = row["energy_kwh"] = 4
        result = analyze_generation(data, data[-1]["date"], weather, SITE, 8)
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "model_not_validated")

    def test_geometry_activates_physical_model_and_ac_limit(self):
        data, weather = synthetic()
        features = _features(weather, Site(**{**SITE.__dict__, "tilt": 15, "azimuth": 0}))
        self.assertTrue(features)
        self.assertTrue(all(0 <= row["physical_power_kw"] <= 8 for row in features.values()))
        self.assertTrue(all(len(row["features"]) == 1 for row in features.values()))

    def test_explanation_routes_exact_user_phrases_without_llm(self):
        for phrase in ("Por que dessa geração mais baixa hoje?", "Por que dessa geração hoje?",
                       "Justifique a produção de ontem", "Porque gerei pouco hoje?"):
            with self.subTest(phrase=phrase):
                result = detect_local_intent(phrase, today=date(2026, 10, 14))
                self.assertEqual(result["tool"], "explicar_geracao_diaria")
                self.assertEqual(result["arguments"]["report_date"],
                                 "2026-10-13" if "ontem" in phrase else "2026-10-14")
        self.assertIsNone(detect_local_intent("Explique a geração de 10 de outubro", today=date(2026, 10, 14)))

    def test_intraday_uses_only_completed_same_hour_window(self):
        day = date(2026, 10, 14)
        rows = [{"time": f"2026-10-14 {hour:02d}:{minute:02d}", "power_w": 1000}
                for hour in range(7, 18) for minute in range(0, 60, 5)]
        with patch("generation_explanation.datetime", wraps=datetime) as clock, \
             patch("generation_explanation.load_site", return_value=SITE), \
             patch("solar_queries._station_id", return_value="test"), \
             patch("solar_queries._stored_curve_profiles", return_value=[]) as history, \
             patch("generation_explanation.fetch_plant_power_curve", return_value=rows), \
             patch("generation_explanation._weather", return_value=[]), \
             patch("generation_explanation.analyze_generation", return_value={"available": False, "status": "warming_up"}) as analyze:
            clock.now.return_value = datetime(2026, 10, 14, 14, 5, tzinfo=TZ)
            result = get_generation_explanation(day.isoformat())
        self.assertEqual(result["window_label"], "07:00–13:00")
        self.assertTrue(result["partial_day"])
        self.assertEqual(analyze.call_args.kwargs["expected_hours"], 6)
        self.assertEqual(history.call_args.args[3], 13)

    def test_formatter_keeps_error_and_uncertainty_visible(self):
        data, weather = synthetic()
        result = analyze_generation(data, data[-1]["date"], weather, SITE, 8)
        result["window_label"] = "08:00–16:00"
        text = format_generation_explanation(result)
        self.assertIn("faixa empírica", text)
        self.assertIn("Erro médio absoluto", text)
        self.assertIn("inclinação e orientação", text)
        self.assertIn("não comprova uma causa exclusiva", text)


if __name__ == "__main__":
    unittest.main()
