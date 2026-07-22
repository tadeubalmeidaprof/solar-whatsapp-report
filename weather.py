from datetime import date
from decimal import Decimal

import requests


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 30


def classify_weather(
    cloud_cover_percent: float,
    rainfall_mm: float,
    solar_radiation_wh_m2: float,
) -> str:
    if rainfall_mm > 5 or solar_radiation_wh_m2 < 2000:
        return "unfavorable"

    if rainfall_mm <= 1 and cloud_cover_percent <= 45 and solar_radiation_wh_m2 >= 3500:
        return "favorable"

    return "partially_favorable"


def get_daily_weather(
    latitude: float,
    longitude: float,
    report_date: date,
) -> dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "cloud_cover_mean",
                "shortwave_radiation_sum",
                "sunshine_duration",
            ]
        ),
        "timezone": "America/Bahia",
        "start_date": report_date.isoformat(),
        "end_date": report_date.isoformat(),
    }

    response = requests.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    daily = payload.get("daily") or {}

    if not daily.get("time"):
        raise RuntimeError("Open-Meteo não retornou dados para a data solicitada.")

    cloud_cover = float((daily.get("cloud_cover_mean") or [0])[0] or 0)
    rainfall = float((daily.get("precipitation_sum") or [0])[0] or 0)

    # A API diária retorna shortwave_radiation_sum em MJ/m².
    # Conversão: 1 MJ/m² = 277,7778 Wh/m².
    radiation_mj_m2 = Decimal(str((daily.get("shortwave_radiation_sum") or [0])[0] or 0))
    radiation_wh_m2 = float(radiation_mj_m2 * Decimal("277.7777778"))

    sunshine_seconds = float((daily.get("sunshine_duration") or [0])[0] or 0)
    sunshine_hours = sunshine_seconds / 3600

    weather_class = classify_weather(
        cloud_cover_percent=cloud_cover,
        rainfall_mm=rainfall,
        solar_radiation_wh_m2=radiation_wh_m2,
    )

    return {
        "PERCENTUALNUVENS": round(cloud_cover, 2),
        "CHUVAMM": round(rainfall, 2),
        "RADIACAOSOLARWHM2": round(radiation_wh_m2, 2),
        "HORASSOL": round(sunshine_hours, 2),
        "TEMPERATURAMINIMAC": float((daily.get("temperature_2m_min") or [0])[0] or 0),
        "TEMPERATURAMAXIMAC": float((daily.get("temperature_2m_max") or [0])[0] or 0),
        "CLASSIFICACAOCLIMA": weather_class,
        "PROVEDORCLIMA": "open-meteo",
        "DADOSBRUTOS": payload,
    }
