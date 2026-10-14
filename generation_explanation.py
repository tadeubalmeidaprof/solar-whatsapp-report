import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from database import fetch_solar_curve_points
from generation_model import Site, analyze_generation, integrate_hourly
from growatt_client import fetch_plant_power_curve
from weather import get_generation_model_weather


logger = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("America/Bahia")
_weather_cache = {}
_cache_lock = Lock()


def load_site() -> Site:
    settings = json.loads(Path(__file__).with_name("pv_site.json").read_text(encoding="utf-8"))

    def parameter(env_name, default):
        value = os.getenv(env_name, "").strip()
        return float(value.replace(",", ".")) if value else default

    latitude = os.getenv("STATION_LATITUDE", "").strip()
    longitude = os.getenv("STATION_LONGITUDE", "").strip()
    if not latitude or not longitude:
        raise ValueError("Localização da usina não configurada.")
    return Site(
        latitude=float(latitude.replace(",", ".")), longitude=float(longitude.replace(",", ".")),
        dc_capacity_kwp=parameter("PV_DC_CAPACITY_KWP", settings["dc_capacity_kwp"]),
        ac_capacity_kw=parameter("PV_AC_CAPACITY_KW", settings["ac_capacity_kw"]),
        gamma_pdc_per_c=settings["gamma_pdc_per_c"],
        tilt=parameter("PV_SURFACE_TILT_DEG", settings["surface_tilt_deg"]),
        azimuth=parameter("PV_SURFACE_AZIMUTH_DEG", settings["surface_azimuth_deg"]),
    )


def _weather(site, first, last):
    key = (site.latitude, site.longitude, first, last)
    with _cache_lock:
        cached = _weather_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < 900:
            return cached[1]
    rows = get_generation_model_weather(site.latitude, site.longitude, first, last)
    with _cache_lock:
        if len(_weather_cache) >= 8:
            _weather_cache.clear()
        _weather_cache[key] = (time.monotonic(), rows)
    return rows


def get_generation_explanation(report_date: str, start_hour: int = 7, end_hour: int = 17) -> dict:
    # Keep the established provider parsing in one place; import lazily to avoid cycles.
    from solar_queries import _parse_date, _parse_power_timestamp, _station_id, _stored_curve_profiles

    target = _parse_date(report_date)
    now = datetime.now(TIMEZONE)
    if target > now.date():
        raise ValueError("Não é possível explicar a geração de um dia futuro.")
    start_hour, end_hour = int(start_hour), int(end_hour)
    if not 0 <= start_hour < end_hour <= 24:
        raise ValueError("Intervalo de análise inválido.")
    requested_end = end_hour
    if target == now.date():
        # Avoid forecast hours and unsettled telemetry. Compare the same prefix on every day.
        settled = now - timedelta(minutes=20)
        end_hour = min(end_hour, settled.hour if settled.date() == target else 0)
    if end_hour <= start_hour:
        return {"available": False, "date": report_date, "status": "window_in_progress",
                "reason": "Ainda não há uma hora solar completa com telemetria consolidada."}
    site = load_site()
    station_id = _station_id()
    history = _stored_curve_profiles(station_id, target, start_hour, end_hour, lookback_days=60)
    source = "growatt-live"
    try:
        rows = fetch_plant_power_curve(target, station_id)
        points = []
        for row in rows:
            try:
                timestamp = _parse_power_timestamp(row.get("time"))
            except (TypeError, ValueError):
                continue
            points.append({"timestamp": timestamp, "power_w": row.get("power_w")})
        if not points:
            raise ValueError("Curva ao vivo vazia.")
    except Exception:
        source = "stored_curve"
        rows = fetch_solar_curve_points("growatt", station_id, target)
        points = [{"timestamp": row["measured_at"].astimezone(TIMEZONE), "power_w": row["power_w"]}
                  for row in rows]
    history.append({"date": target, "points": points})
    days = []
    for item in history:
        start = datetime.combine(item["date"], datetime.min.time(), tzinfo=TIMEZONE) + timedelta(hours=start_hour)
        end = start + timedelta(hours=end_hour - start_hour)
        hours = integrate_hourly(item["points"], start, end)
        if hours:
            days.append({"date": item["date"], "hours": hours})
    if not days:
        return {"available": False, "date": report_date, "status": "insufficient_data_coverage",
                "reason": "Curva de geração indisponível para o intervalo."}
    try:
        weather = _weather(site, min(item["date"] for item in days), target)
    except Exception as exc:
        logger.warning("Clima do modelo de geração indisponível: %s", type(exc).__name__)
        return {"available": False, "date": report_date, "status": "weather_unavailable",
                "reason": "Radiação horária indisponível; não é possível calcular uma justificativa climática quantitativa."}
    result = analyze_generation(days, target, weather, site, expected_hours=end_hour - start_hour)
    result.update(date=report_date, window_label=f"{start_hour:02d}:00–{end_hour:02d}:00",
                  partial_day=end_hour < requested_end, observed_source=source,
                  dc_capacity_kwp=site.dc_capacity_kwp, ac_capacity_kw=site.ac_capacity_kw,
                  weather_sources=sorted({row["source"] for row in weather}))
    return result


def format_generation_explanation(payload: dict) -> str:
    def number(value, digits=1):
        return f"{float(value):.{digits}f}".replace(".", ",")

    window = payload.get("window_label") or "o intervalo solicitado"
    if not payload.get("available"):
        if payload.get("status") == "warming_up":
            return (f"Ainda não tenho histórico suficiente para justificar quantitativamente a geração em {window}. "
                    f"Tenho {payload.get('historical_days', 0)} dias comparáveis; preciso de pelo menos "
                    f"{payload.get('minimum_training_days', 7)} para ajuste e "
                    f"{payload.get('minimum_validation_days', 5)} posteriores para medir o erro.")
        return str(payload.get("reason") or "Faltam dados confiáveis para justificar a geração agora.")
    openings = {
        "weather_compatible_reduction": "A menor radiação é compatível com a redução observada.",
        "below_weather_adjusted_range": "A geração ficou abaixo da faixa estimada para esse clima; o desvio merece investigação.",
        "above_weather_adjusted_range": "A geração ficou acima da faixa estimada para esse clima.",
        "within_weather_adjusted_range": "A geração ficou dentro da faixa estimada para esse clima.",
    }
    lower, upper = payload["empirical_range_kwh"]
    date_label = str(payload.get("date") or "")
    text = (f"{openings[payload['status']]}\n\n"
            f"{date_label} — "
            f"Entre {window}: *{number(payload['observed_energy_kwh'])} kWh*. "
            f"Estimativa: *{number(payload['expected_energy_kwh'])} kWh* "
            f"(faixa empírica {number(lower)}–{number(upper)} kWh).\n")
    change = payload.get("radiation_change_vs_reference_percent")
    if change is not None:
        text += f"Radiação frente à referência: *{number(change):s}%*. "
    validation = payload["validation"]
    text += (f"Erro médio absoluto histórico: *{number(validation['mae_kwh'])} kWh*, "
             f"medido em {validation['days']} dias posteriores ao ajuste.\n")
    if payload["model_type"] == "empirical_ghi_solar_geometry":
        text += "Estimativa empírica; ainda faltam inclinação e orientação dos painéis. "
    else:
        text += "Temperatura de célula e eficiência do inversor são modeladas. "
    text += "A faixa não garante o resultado e não comprova uma causa exclusiva."
    return text
