"""Weather-conditioned generation, with chronological validation and explicit limits."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pvlib


VERSION = "generation-explanation-v1"
MIN_TRAIN_DAYS = 7
MIN_VALIDATION_DAYS = 5
MAX_GAP_MINUTES = 15


@dataclass(frozen=True)
class Site:
    latitude: float
    longitude: float
    dc_capacity_kwp: float
    ac_capacity_kw: float
    gamma_pdc_per_c: float = -0.0024
    tilt: float | None = None
    azimuth: float | None = None

    def __post_init__(self):
        values = (self.latitude, self.longitude, self.dc_capacity_kwp,
                  self.ac_capacity_kw, self.gamma_pdc_per_c)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Parâmetros fotovoltaicos devem ser finitos.")
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ValueError("Localização inválida.")
        if self.dc_capacity_kwp <= 0 or self.ac_capacity_kw <= 0:
            raise ValueError("Potências DC e AC devem ser positivas.")
        if not -0.02 < self.gamma_pdc_per_c <= 0:
            raise ValueError("Coeficiente térmico inválido.")
        if (self.tilt is None) != (self.azimuth is None):
            raise ValueError("Inclinação e orientação devem ser informadas juntas.")
        if self.tilt is not None and not (
            math.isfinite(self.tilt) and 0 <= self.tilt <= 90
            and math.isfinite(self.azimuth) and 0 <= self.azimuth < 360
        ):
            raise ValueError("Geometria dos painéis inválida.")


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def integrate_hourly(points: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Integrate the observed curve; never fill long gaps or fabricate zeroes."""
    clean = {}
    for point in points:
        timestamp = point.get("timestamp")
        power = _number(point.get("power_w"))
        if isinstance(timestamp, datetime) and power is not None and power >= 0:
            clean[timestamp] = power / 1000
    ordered = sorted(clean.items())
    buckets = {}
    for (left, p0), (right, p1) in zip(ordered, ordered[1:]):
        span = (right - left).total_seconds()
        if not 0 < span <= MAX_GAP_MINUTES * 60:
            continue
        cursor, stop = max(left, start), min(right, end)
        while cursor < stop:
            hour = cursor.replace(minute=0, second=0, microsecond=0)
            boundary = min(stop, hour + timedelta(hours=1))
            a = p0 + (p1 - p0) * (cursor - left).total_seconds() / span
            b = p0 + (p1 - p0) * (boundary - left).total_seconds() / span
            duration = (boundary - cursor).total_seconds() / 3600
            bucket = buckets.setdefault(hour, {"energy_kwh": 0.0, "duration_hours": 0.0})
            bucket["energy_kwh"] += (a + b) * 0.5 * duration
            bucket["duration_hours"] += duration
            cursor = boundary
    return [
        {"timestamp": hour, **values,
         "power_kw": values["energy_kwh"] / values["duration_hours"]}
        for hour, values in sorted(buckets.items())
        if values["duration_hours"] >= 0.85
    ]


def _features(weather: list[dict], site: Site) -> dict:
    """Open-Meteo irradiance timestamps represent the preceding hour."""
    valid = []
    for row in weather:
        ghi, temp = _number(row.get("shortwave_radiation_w_m2")), _number(row.get("temperature_c"))
        stamp = row.get("timestamp")
        if (not isinstance(stamp, datetime) or ghi is None or ghi < 0
                or ghi > 1800 or temp is None or not -60 <= temp <= 70):
            continue
        valid.append({**row, "ghi": ghi, "temp": temp,
                      "midpoint": stamp - timedelta(minutes=30),
                      "hour": stamp - timedelta(hours=1)})
    if not valid:
        return {}
    times = pd.DatetimeIndex([row["midpoint"] for row in valid])
    position = pvlib.solarposition.get_solarposition(times, site.latitude, site.longitude)
    result = {}
    for index, row in enumerate(valid):
        zenith = float(position.iloc[index]["apparent_zenith"])
        azimuth = float(position.iloc[index]["azimuth"])
        g = row["ghi"] / 1000
        cos_z = max(0.0, math.cos(math.radians(zenith)))
        if cos_z <= 0 and g > 0.05:
            continue
        features = [g, g * g, g * (row["temp"] - 25) / 25,
                    g * cos_z, g * math.sin(math.radians(azimuth)),
                    g * math.cos(math.radians(azimuth))]
        physical = None
        if site.tilt is not None:
            dni = _number(row.get("direct_normal_irradiance_w_m2"))
            dhi = _number(row.get("diffuse_radiation_w_m2"))
            wind = _number(row.get("wind_speed_m_s"))
            if dni is None or dhi is None or wind is None or min(dni, dhi, wind) < 0:
                continue
            extra = float(pvlib.irradiance.get_extra_radiation(row["midpoint"]))
            poa = pvlib.irradiance.get_total_irradiance(
                site.tilt, site.azimuth, zenith, azimuth, dni, row["ghi"], dhi,
                dni_extra=extra, model="perez",
            )["poa_global"]
            if not math.isfinite(float(poa)):
                continue
            cell_temp = pvlib.temperature.faiman(max(0.0, float(poa)), row["temp"], wind)
            dc_w = pvlib.pvsystem.pvwatts_dc(
                max(0.0, float(poa)), cell_temp, site.dc_capacity_kwp * 1000,
                site.gamma_pdc_per_c,
            )
            # Generic inverter efficiency and Faiman coefficients are assumptions;
            # a robust site gain is fitted later, not advertised as measured losses.
            ac_kw = float(pvlib.inverter.pvwatts(
                max(0.0, float(dc_w)), site.ac_capacity_kw * 1000 / 0.96,
            )) / 1000
            physical = max(0.0, ac_kw)
            features = [physical / site.dc_capacity_kwp]
        result[row["hour"]] = {"features": features, "ghi": row["ghi"],
                               "temperature_c": row["temp"], "physical_power_kw": physical}
    return result


def _fit(rows: list[dict], site: Site):
    x = np.asarray([row["features"] for row in rows], dtype=float)
    y = np.asarray([row["power_kw"] / site.dc_capacity_kwp for row in rows])
    scale = np.maximum(np.sqrt(np.mean(x * x, axis=0)), 1e-6)
    x = x / scale
    weights = np.asarray([row["duration_hours"] for row in rows])
    regularizer = np.eye(x.shape[1]) * 0.01
    beta = np.zeros(x.shape[1])
    for _ in range(20):
        previous = beta.copy()
        beta = np.linalg.solve(x.T @ (weights[:, None] * x) + regularizer,
                               x.T @ (weights * y))
        residual = y - x @ beta
        center = np.median(residual)
        sigma = max(float(1.4826 * np.median(np.abs(residual - center))), 0.01)
        weights = np.asarray([row["duration_hours"] for row in rows]) * np.minimum(
            1.0, 1.345 * sigma / np.maximum(np.abs(residual - center), 1e-9),
        )
        if np.max(np.abs(beta - previous)) < 1e-7:
            break
    return beta, scale


def _predict(rows: list[dict], fitted, site: Site) -> np.ndarray:
    beta, scale = fitted
    x = np.asarray([row["features"] for row in rows], dtype=float) / scale
    return np.clip(x @ beta * site.dc_capacity_kwp, 0.0, site.ac_capacity_kw)


def analyze_generation(days: list[dict], target_date, weather: list[dict], site: Site,
                       expected_hours: int) -> dict:
    if not 1 <= expected_hours <= 24:
        raise ValueError("Intervalo de análise inválido.")
    features = _features(weather, site)
    matched = {}
    for day in days:
        report_date = day["date"]
        rows = []
        for observed in day["hours"]:
            climate = features.get(observed["timestamp"])
            power = _number(observed.get("power_kw"))
            if climate is None or power is None or not 0 <= power <= site.ac_capacity_kw * 1.1:
                continue
            rows.append({**observed, **climate})
        if sum(row["duration_hours"] for row in rows) >= expected_hours * 0.90:
            matched[report_date] = rows
    current = matched.get(target_date)
    target_hours = {row["timestamp"].hour for row in current or []}
    historical = sorted(
        day for day in matched if day < target_date
        and target_hours.issubset({row["timestamp"].hour for row in matched[day]})
    )
    result = {
        "available": False, "status": "warming_up", "algorithm_version": VERSION,
        "model_type": "physical_poa_calibrated" if site.tilt is not None else "empirical_ghi_solar_geometry",
        "historical_days": len(historical), "minimum_training_days": MIN_TRAIN_DAYS,
        "minimum_validation_days": MIN_VALIDATION_DAYS,
        "limitations": [
            "Clima estimado por provedor; não há medição local de irradiância e temperatura de célula.",
            "A faixa é empírica, baseada nos erros de dias posteriores ao ajuste; não é garantia de cobertura futura.",
            "Compatibilidade com clima não prova uma causa exclusiva; o desvio restante não identifica um defeito.",
        ],
    }
    if site.tilt is None:
        result["limitations"].append(
            "Inclinação e orientação não informadas: modelo empírico com GHI, sem alegar irradiância no plano dos módulos ou perda térmica física."
        )
    else:
        result["limitations"].append(
            "Modelo Faiman e curva genérica do inversor; bifacialidade e perdas são absorvidas na calibração, sem porcentagens causais separadas."
        )
    if current is None:
        result.update(status="insufficient_data_coverage", reason="Faltam curva ou radiação horária com cobertura suficiente.")
        return result
    observed = sum(row["energy_kwh"] for row in current)
    result["observed_energy_kwh"] = round(observed, 3)
    result["equivalent_full_power_hours"] = round(observed / site.dc_capacity_kwp, 3)
    result["telemetry_coverage_percent"] = round(sum(row["duration_hours"] for row in current) / expected_hours * 100, 1)
    if len(historical) < MIN_TRAIN_DAYS + MIN_VALIDATION_DAYS:
        return result

    validation = []
    for index in range(MIN_TRAIN_DAYS, len(historical)):
        prior = [row for day in historical[:index] for row in matched[day]]
        test = matched[historical[index]]
        fitted = _fit(prior, site)
        predicted = float(sum(p * row["duration_hours"] for p, row in zip(_predict(test, fitted, site), test)))
        actual = sum(row["energy_kwh"] for row in test)
        baseline = sum(
            float(np.median([r["power_kw"] for r in prior if r["timestamp"].hour == row["timestamp"].hour]))
            * row["duration_hours"] for row in test
        )
        validation.append({"date": historical[index].isoformat(), "observed_kwh": actual,
                           "expected_kwh": predicted, "residual_kwh": actual - predicted,
                           "baseline_error_kwh": actual - baseline})
    errors = np.asarray([row["residual_kwh"] for row in validation])
    base_errors = np.asarray([row["baseline_error_kwh"] for row in validation])
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    baseline_rmse = float(np.sqrt(np.mean(base_errors ** 2)))
    mean_actual = float(np.mean([row["observed_kwh"] for row in validation]))
    result["validation"] = {
        "method": "expanding_window_previous_days_only",
        "days": len(validation), "mae_kwh": round(float(np.mean(np.abs(errors))), 3),
        "rmse_kwh": round(rmse, 3), "bias_observed_minus_expected_kwh": round(float(np.mean(errors)), 3),
        "normalized_rmse_percent": round(rmse / mean_actual * 100, 1) if mean_actual > 0 else None,
        "baseline_rmse_kwh": round(baseline_rmse, 3),
        "model_beats_baseline": rmse < baseline_rmse,
        "predictions": [{key: round(value, 3) if isinstance(value, float) else value
                         for key, value in row.items()} for row in validation],
    }
    if mean_actual <= 0 or rmse / mean_actual > 0.30 or rmse >= baseline_rmse:
        result.update(status="model_not_validated", reason="O modelo ainda não superou a referência histórica com erro aceitável.")
        return result
    prior = [row for day in historical for row in matched[day]]
    if (max(row["ghi"] for row in current) > max(row["ghi"] for row in prior) * 1.2 + 50
            or min(row["temperature_c"] for row in current) < min(row["temperature_c"] for row in prior) - 10
            or max(row["temperature_c"] for row in current) > max(row["temperature_c"] for row in prior) + 10):
        result.update(status="out_of_calibration_range", reason="O clima está fora das condições usadas na validação; a estimativa seria uma extrapolação não validada.")
        return result
    predicted_powers = _predict(current, _fit(prior, site), site)
    expected = float(sum(p * row["duration_hours"] for p, row in zip(predicted_powers, current)))
    lower = max(0.0, expected + float(np.quantile(errors, 0.10)))
    upper = max(lower, expected + float(np.quantile(errors, 0.90)))
    # Compare the same clock hours and observed durations. A telemetry gap is not weather loss.
    reference = sum(
        float(np.median([r["power_kw"] for r in prior if r["timestamp"].hour == row["timestamp"].hour]))
        * row["duration_hours"] for row in current
    )
    current_radiation = sum(row["ghi"] * row["duration_hours"] for row in current)
    history_radiation = sum(
        float(np.median([r["ghi"] for r in prior if r["timestamp"].hour == row["timestamp"].hour]))
        * row["duration_hours"] for row in current
    )
    radiation_change = (current_radiation / history_radiation - 1) * 100 if history_radiation > 0 else None
    if observed < lower:
        status = "below_weather_adjusted_range"
    elif observed > upper:
        status = "above_weather_adjusted_range"
    elif reference > 0 and observed < reference * 0.90 and radiation_change is not None and radiation_change < -10:
        status = "weather_compatible_reduction"
    else:
        status = "within_weather_adjusted_range"
    result.update(
        available=True, status=status, confidence="moderate" if len(validation) >= 10 else "low",
        expected_energy_kwh=round(expected, 3), empirical_range_kwh=[round(lower, 3), round(upper, 3)],
        reference_energy_kwh=round(reference, 3),
        observed_change_vs_reference_percent=round((observed / reference - 1) * 100, 1) if reference > 0 else None,
        residual_kwh=round(observed - expected, 3),
        residual_percent=round((observed / expected - 1) * 100, 1) if expected > 0 else None,
        radiation_change_vs_reference_percent=round(radiation_change, 1) if radiation_change is not None else None,
    )
    return result
