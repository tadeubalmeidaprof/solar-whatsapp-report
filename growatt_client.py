import hashlib
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import growattServer
import requests

from config import env, required_env


TIMEZONE = ZoneInfo("America/Bahia")
DEFAULT_WEB_SERVER = "https://openapi.growatt.com"

STATUS_MAP = {
    "0": "Aguardando",
    "1": "Normal",
    "2": "Falha",
    "3": "Offline",
    "4": "Operando",
    "5": "Alarme",
    "6": "Atualizando",
}


def parse_number(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")

    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def normalize_power_kw(value) -> float:
    number = parse_number(value)

    if number is None:
        return 0.0

    if abs(number) > 100:
        return round(number / 1000, 3)

    return round(number, 3)


def clean_kwh(value) -> float:
    number = parse_number(value)
    return round(number or 0, 3)


def norm_key(key) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def deep_find(obj, aliases):
    wanted = {norm_key(alias) for alias in aliases}

    def walk(item):
        if isinstance(item, dict):
            for key, value in item.items():
                if norm_key(key) in wanted and value not in ("", None):
                    return value

            for value in item.values():
                found = walk(value)
                if found not in ("", None):
                    return found

        elif isinstance(item, list):
            for value in item:
                found = walk(value)
                if found not in ("", None):
                    return found

        return None

    return walk(obj)


def unwrap(obj):
    current = obj

    for _ in range(4):
        if not isinstance(current, dict):
            return current

        for key in ("data", "result", "payload"):
            if key in current and current[key] not in ("", None):
                if isinstance(current[key], (dict, list)):
                    current = current[key]
                    break
        else:
            return current

    return current


def as_list(obj) -> list[dict]:
    data = unwrap(obj)

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in (
            "plants",
            "plant",
            "plantList",
            "devices",
            "device",
            "list",
            "records",
            "rows",
            "datas",
        ):
            value = data.get(key)

            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        return [data]

    return []


def call_first(api, candidates):
    last_error = None

    for method_name, args in candidates:
        function = getattr(api, method_name, None)

        if not callable(function):
            continue

        try:
            return function(*args)
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError("Nenhum método compatível encontrado na biblioteca Growatt.")


def get_first_plant_id(api) -> str:
    configured = env("GROWATT_PLANT_ID")

    if configured:
        return configured

    response = call_first(
        api,
        [
            ("plant_list", tuple()),
            ("plant_list_v1", tuple()),
        ],
    )

    plants = as_list(response)

    if not plants:
        raise RuntimeError("Nenhuma usina encontrada pelo token.")

    plant = plants[0]
    plant_id = (
        plant.get("plant_id")
        or plant.get("plantId")
        or plant.get("id")
        or deep_find(plant, ["plant_id", "plantId", "id"])
    )

    if not plant_id:
        raise RuntimeError("Usina encontrada, mas sem Plant ID.")

    return str(plant_id)


def get_device_sn(api, plant_id: str) -> str:
    configured = env("GROWATT_DEVICE_SN")

    if configured:
        return configured

    response = call_first(
        api,
        [
            ("device_list", (plant_id,)),
            ("device_list_v1", (plant_id,)),
        ],
    )

    devices = as_list(response)

    if not devices:
        return ""

    device = devices[0]
    sn = (
        device.get("sn")
        or device.get("deviceSn")
        or device.get("device_sn")
        or device.get("serialNum")
        or device.get("serialNumber")
        or deep_find(device, ["sn", "deviceSn", "serialNum", "serialNumber"])
    )

    return str(sn or "")


def fetch_growatt_payload() -> dict:
    token = env("GROWATT_API_TOKEN", required=True)
    server_url = env("GROWATT_SERVER_URL", "https://openapi.growatt.com/v1/")

    api = growattServer.OpenApiV1(token=token)

    if hasattr(api, "server_url"):
        api.server_url = server_url

    plant_id = get_first_plant_id(api)
    device_sn = get_device_sn(api, plant_id)

    raw = {
        "plant_id": plant_id,
        "device_sn": device_sn,
    }

    raw["plant_overview"] = call_first(
        api,
        [
            ("plant_energy_overview", (plant_id,)),
            ("plant_energy_overview_v1", (plant_id,)),
            ("plant_data", (plant_id,)),
        ],
    )

    if device_sn:
        try:
            raw["device_energy"] = call_first(
                api,
                [
                    ("min_energy", (device_sn,)),
                    ("min_energy_v1", (device_sn,)),
                    ("tlx_energy_overview", (plant_id, device_sn)),
                ],
            )
        except Exception as exc:
            print(f"Aviso: não consegui buscar energia do inversor: {exc}")
            raw["device_energy"] = {}

        try:
            raw["device_detail"] = call_first(
                api,
                [
                    ("min_detail", (device_sn,)),
                    ("min_detail_v1", (device_sn,)),
                    ("tlx_system_status", (plant_id, device_sn)),
                ],
            )
        except Exception as exc:
            print(f"Aviso: não consegui buscar detalhes do inversor: {exc}")
            raw["device_detail"] = {}

    power_raw = deep_find(
        raw,
        [
            "powerNowKw",
            "currentPowerKw",
            "current_power_kw",
            "currentPower",
            "current_power",
            "pac",
            "pacs",
            "outputPower",
            "output_power",
            "plantPower",
            "inverterPower",
            "power",
        ],
    )
    today_raw = deep_find(
        raw,
        [
            "energyTodayKwh",
            "todayEnergy",
            "today_energy",
            "eToday",
            "eday",
            "dailyEnergy",
            "daily_energy",
            "todayGenerateEnergy",
        ],
    )
    month_raw = deep_find(
        raw,
        [
            "energyMonthKwh",
            "monthEnergy",
            "month_energy",
            "eMonth",
            "emonth",
            "monthlyEnergy",
            "monthly_energy",
            "monthGenerateEnergy",
        ],
    )
    year_raw = deep_find(
        raw,
        [
            "energyYearKwh",
            "yearEnergy",
            "year_energy",
            "eYear",
            "eyear",
            "yearlyEnergy",
            "yearly_energy",
            "yearGenerateEnergy",
        ],
    )
    total_raw = deep_find(
        raw,
        [
            "energyTotalKwh",
            "totalEnergy",
            "total_energy",
            "eTotal",
            "etotal",
            "totalGenerateEnergy",
            "total_generate_energy",
            "total",
        ],
    )
    status_raw = deep_find(
        raw,
        [
            "status",
            "deviceStatus",
            "device_status",
            "workStatus",
            "work_status",
            "inverterStatus",
            "state",
        ],
    )
    temperature_raw = deep_find(
        raw,
        [
            "temperature",
            "temp",
            "inverterTemp",
            "deviceTemperature",
        ],
    )

    status = str(status_raw or "").strip()
    status = STATUS_MAP.get(status, status or "Sem informação")

    return {
        "plantId": plant_id,
        "deviceSn": device_sn,
        "powerNowKw": normalize_power_kw(power_raw),
        "energyTodayKwh": clean_kwh(today_raw),
        "energyMonthKwh": clean_kwh(month_raw),
        "energyYearKwh": clean_kwh(year_raw),
        "energyTotalKwh": clean_kwh(total_raw),
        "status": status,
        "temperature": clean_kwh(temperature_raw),
    }


def _web_server() -> str:
    return env("GROWATT_WEB_SERVER").rstrip("/") or DEFAULT_WEB_SERVER


def _growatt_login() -> requests.Session:
    username = required_env("GROWATT_USERNAME")
    password = required_env("GROWATT_PASSWORD")
    server = _web_server()

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/153 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
    )

    response = session.post(
        f"{server}/login",
        data={
            "account": username,
            "password": "",
            "validateCode": "",
            "isReadPact": "0",
            "passwordCrc": hashlib.md5(password.encode("utf-8")).hexdigest(),
        },
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    if payload.get("result") != 1:
        raise RuntimeError(f"Login Growatt recusado: {payload}")

    session.headers.update({"Referer": f"{server}/index"})
    return session


def _query_fault_page(
    session: requests.Session,
    station_id: str,
    device_sn: str,
    day_text: str,
    page: int,
) -> dict:
    response = session.post(
        f"{_web_server()}/log/getNewPlantFaultLog",
        data={
            "plantId": station_id,
            "date": day_text,
            "deviceSn": device_sn,
            "toPageNum": page,
            "type": 1,
        },
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    if payload.get("result") != 1:
        raise RuntimeError(f"Growatt recusou Fault Log: {payload}")

    return payload


def _fetch_faults_for_day(
    session: requests.Session,
    station_id: str,
    device_sn: str,
    day_text: str,
) -> list[dict]:
    faults = []
    page = 1

    while True:
        payload = _query_fault_page(
            session=session,
            station_id=station_id,
            device_sn=device_sn,
            day_text=day_text,
            page=page,
        )

        obj = payload.get("obj") or {}
        faults.extend(obj.get("datas") or [])

        total_pages = int(obj.get("pages") or 1)

        if page >= total_pages:
            break

        page += 1

    return faults


def fetch_recent_faults(station_id: str, device_sn: str) -> list[dict]:
    session = _growatt_login()

    try:
        now = datetime.now(TIMEZONE)
        days = [now.date(), (now - timedelta(days=1)).date()]
        merged = {}

        for day in days:
            day_text = day.isoformat()
            print(f"Consultando Fault Log de {day_text}")

            for fault in _fetch_faults_for_day(
                session=session,
                station_id=station_id,
                device_sn=device_sn,
                day_text=day_text,
            ):
                raw_code = str(
                    fault.get("eventId")
                    or fault.get("eventCode")
                    or fault.get("alarmCode")
                    or ""
                )
                time_text = str(
                    fault.get("time")
                    or fault.get("startTime")
                    or ""
                )
                merged[(raw_code, time_text)] = fault

        return list(merged.values())
    finally:
        try:
            session.get(f"{_web_server()}/logout", timeout=10)
        except requests.RequestException:
            pass


def fetch_live_data() -> dict:
    response = requests.post(
        "https://openapi.growatt.com/v4/new-api/queryLastData",
        headers={"token": required_env("GROWATT_API_TOKEN")},
        data={
            "deviceType": "min",
            "deviceSn": required_env("GROWATT_DEVICE_SN"),
        },
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    if isinstance(payload, str):
        payload = json.loads(payload)

    if payload.get("code") != 0:
        raise RuntimeError(f"queryLastData falhou: {payload}")

    devices = ((payload.get("data") or {}).get("min") or [])
    return devices[0] if devices else {}
