import os
import re
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import growattServer


STATUS_MAP = {
    "0": "Aguardando",
    "1": "Normal",
    "2": "Falha",
    "3": "Offline",
    "4": "Operando",
    "5": "Alarme",
    "6": "Atualizando",
}


def env(name, default="", required=False):
    value = os.getenv(name, default).strip()
    if required and not value:
        print(f"ERRO: variável {name} não configurada.")
        sys.exit(1)
    return value


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
    except Exception:
        return None


def normalize_power_kw(value):
    number = parse_number(value)
    if number is None:
        return 0.0

    # Se vier acima de 100, provavelmente veio em W.
    if abs(number) > 100:
        return round(number / 1000, 3)

    return round(number, 3)


def clean_kwh(value):
    number = parse_number(value)
    return round(number or 0, 3)


def norm_key(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def deep_find(obj, aliases):
    wanted = {norm_key(a) for a in aliases}

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if norm_key(k) in wanted and v not in ("", None):
                    return v

            for v in x.values():
                found = walk(v)
                if found not in ("", None):
                    return found

        elif isinstance(x, list):
            for item in x:
                found = walk(item)
                if found not in ("", None):
                    return found

        return None

    return walk(obj)


def unwrap(obj):
    current = obj

    for _ in range(4):
        if isinstance(current, dict):
            for key in ("data", "result", "payload"):
                if key in current and current[key] not in ("", None):
                    if isinstance(current[key], (dict, list)):
                        current = current[key]
                        break
            else:
                return current
        else:
            return current

    return current

def should_send_now():
    now_bahia = datetime.now(ZoneInfo("America/Bahia"))

    # Só permite envio agendado entre 18:25 e 18:45
    if now_bahia.hour == 18 and 25 <= now_bahia.minute <= 45:
        return True

    print(f"Fora do horário permitido. Agora na Bahia: {now_bahia.strftime('%d/%m/%Y %H:%M:%S')}")
    return False

def as_list(obj):
    data = unwrap(obj)

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        for key in ("plants", "plant", "plantList", "devices", "device", "list", "records", "rows", "datas"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]

        return [data]

    return []


def call_first(api, candidates):
    last_error = None

    for method_name, args in candidates:
        fn = getattr(api, method_name, None)
        if not callable(fn):
            continue

        try:
            print(f"Chamando Growatt: {method_name}{args}")
            return fn(*args)
        except Exception as exc:
            last_error = exc
            print(f"Falha em {method_name}: {exc}")

    if last_error:
        raise last_error

    raise RuntimeError("Nenhum método compatível encontrado na biblioteca Growatt.")


def get_first_plant_id(api):
    configured = env("GROWATT_PLANT_ID")
    if configured:
        return configured

    response = call_first(api, [
        ("plant_list", tuple()),
        ("plant_list_v1", tuple()),
    ])

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


def get_device_sn(api, plant_id):
    configured = env("GROWATT_DEVICE_SN")
    if configured:
        return configured

    response = call_first(api, [
        ("device_list", (plant_id,)),
        ("device_list_v1", (plant_id,)),
    ])

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


def fetch_growatt_payload():
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

    raw["plant_overview"] = call_first(api, [
        ("plant_energy_overview", (plant_id,)),
        ("plant_energy_overview_v1", (plant_id,)),
        ("plant_data", (plant_id,)),
    ])

    if device_sn:
        try:
            raw["device_energy"] = call_first(api, [
                ("min_energy", (device_sn,)),
                ("min_energy_v1", (device_sn,)),
                ("tlx_energy_overview", (plant_id, device_sn)),
            ])
        except Exception as exc:
            print(f"Aviso: não consegui buscar energia do inversor: {exc}")
            raw["device_energy"] = {}

        try:
            raw["device_detail"] = call_first(api, [
                ("min_detail", (device_sn,)),
                ("min_detail_v1", (device_sn,)),
                ("tlx_system_status", (plant_id, device_sn)),
            ])
        except Exception as exc:
            print(f"Aviso: não consegui buscar detalhes do inversor: {exc}")
            raw["device_detail"] = {}

    power_raw = deep_find(raw, [
        "powerNowKw", "currentPowerKw", "current_power_kw",
        "currentPower", "current_power", "pac", "pacs",
        "outputPower", "output_power", "plantPower", "inverterPower",
        "power"
    ])

    today_raw = deep_find(raw, [
        "energyTodayKwh", "todayEnergy", "today_energy",
        "eToday", "eday", "dailyEnergy", "daily_energy",
        "todayGenerateEnergy"
    ])

    month_raw = deep_find(raw, [
        "energyMonthKwh", "monthEnergy", "month_energy",
        "eMonth", "emonth", "monthlyEnergy", "monthly_energy",
        "monthGenerateEnergy"
    ])

    year_raw = deep_find(raw, [
        "energyYearKwh", "yearEnergy", "year_energy",
        "eYear", "eyear", "yearlyEnergy", "yearly_energy",
        "yearGenerateEnergy"
    ])

    total_raw = deep_find(raw, [
        "energyTotalKwh", "totalEnergy", "total_energy",
        "eTotal", "etotal", "totalGenerateEnergy",
        "total_generate_energy", "total"
    ])

    status_raw = deep_find(raw, [
        "status", "deviceStatus", "device_status",
        "workStatus", "work_status", "inverterStatus", "state"
    ])

    temp_raw = deep_find(raw, [
        "temperature", "temp", "inverterTemp", "deviceTemperature"
    ])

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
        "temperature": clean_kwh(temp_raw),
    }


def br_number(value, decimals=1):
    try:
        return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,0"


def br_money(value):
    try:
        return "R$ " + f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

def build_message(payload):
    today = float(payload.get("energyTodayKwh") or 0)
    month = float(payload.get("energyMonthKwh") or 0)

    date_text = datetime.now().strftime("%d/%m/%Y")

    return f"""☀️Tadeu, aqui está seu relatório solar diário! - {date_text}

Geração hoje: {br_number(today, 1)} kWh
Geração no mês: {br_number(month, 1)} kWh
"""


def send_whatsapp(message):
    phone = env("WHATSAPP_PHONE", required=True)
    apikey = env("WHATSAPP_APIKEY", required=True)

    url = "https://api.callmebot.com/whatsapp.php"

    response = requests.get(
        url,
        params={
            "phone": phone,
            "text": message,
            "apikey": apikey,
        },
        timeout=30,
    )

    print("CallMeBot HTTP:", response.status_code)
    print("Resposta:", response.text[:300])

    if not response.ok:
        raise RuntimeError(f"Falha ao enviar WhatsApp: HTTP {response.status_code}")

    return True


def main():
    payload = fetch_growatt_payload()
    print("Payload Growatt:", payload)

    message = build_message(payload)
    print("Mensagem montada:")
    print(message)

    send_whatsapp(message)
    print("Relatório enviado com sucesso.")


if __name__ == "__main__":
    main()
