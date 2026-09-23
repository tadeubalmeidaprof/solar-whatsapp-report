import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from database import (
    fetch_active_growatt_faults,
    fetch_pending_growatt_recovery_notifications,
    ensure_growatt_fault_events_table,
    increment_growatt_fault_normal_check,
    mark_growatt_fault_notified,
    mark_growatt_fault_recovery_notified,
    mark_growatt_fault_resolved,
    reset_growatt_fault_normal_checks,
    upsert_growatt_fault_event,
)
from send_daily_report import env, send_whatsapp_to


TIMEZONE = ZoneInfo("America/Bahia")
DEFAULT_WEB_SERVER = "https://openapi.growatt.com"

ERROR_MESSAGES_PT = {
    "200": "Falha AFCI: possível arco elétrico no circuito fotovoltaico",
    "201": "Corrente residual elevada",
    "202": "Tensão fotovoltaica acima do limite permitido",
    "203": "Baixo isolamento do sistema fotovoltaico",
    "300": "Tensão da rede AC fora da faixa permitida",
    "302": "Sem conexão com a rede AC",
    "303": "Anomalia entre neutro e aterramento (PE)",
    "304": "Frequência da rede AC fora da faixa permitida",
    "402": "Componente CC elevado na saída do inversor",
    "404": "Falha na medição do barramento interno",
    "405": "Falha do relé interno",
    "407": "Falha no autoteste do inversor",
    "408": "Temperatura excessiva do inversor",
    "409": "Sobretensão no barramento interno",
    "411": "Falha de comunicação interna DSP/M3",
    "414": "Falha na memória EEPROM",
    "416": "Sobrecorrente detectada pela proteção de software",
    "420": "Falha na proteção contra fuga à terra (GFCI)",
    "422": "Divergência entre as medições DSP e M3",
    "425": "Falha no autoteste AFCI",
}

WARNING_MESSAGES_PT = {
    "202": "Anomalia no DPS do lado CC",
    "203": "Curto-circuito em uma entrada fotovoltaica",
    "204": "Anomalia na função de contato seco",
    "205": "Falha no circuito Boost fotovoltaico",
    "207": "Sobrecorrente na porta USB",
    "401": "Falha de comunicação entre o inversor e o medidor",
    "404": "Anomalia na memória EEPROM",
    "405": "Versão de firmware incompatível ou inconsistente",
}

EVENT_NAME_MESSAGES_PT = {
    "afci fault": "Falha AFCI: possível arco elétrico no circuito fotovoltaico",
    "residual i high": "Corrente residual elevada",
    "pv voltage high": "Tensão fotovoltaica acima do limite permitido",
    "pv isolation low": "Baixo isolamento do sistema fotovoltaico",
    "ac v outrange": "Tensão da rede AC fora da faixa permitida",
    "no ac connection": "Sem conexão com a rede AC",
    "ac f outrange": "Frequência da rede AC fora da faixa permitida",
    "auto test failed": "Falha no autoteste do inversor",
    "dci high": "Componente CC elevado na saída do inversor",
    "bus sample fault": "Falha na medição do barramento interno",
    "relay fault": "Falha do relé interno",
    "over temperature": "Temperatura excessiva do inversor",
    "bus over voltage": "Sobretensão no barramento interno",
    "dsp communicate with m3 abnormal": "Falha de comunicação interna DSP/M3",
    "eeprom fault": "Falha na memória EEPROM",
    "over current": "Sobrecorrente detectada pela proteção de software",
    "gfci device fault": "Falha na proteção contra fuga à terra (GFCI)",
    "consistent fault": "Divergência entre as medições DSP e M3",
    "afci self test fault": "Falha no autoteste AFCI",
    "dc spd abnormal": "Anomalia no DPS do lado CC",
    "pv short circuit": "Curto-circuito em uma entrada fotovoltaica",
    "dryconnect function abnormal": "Anomalia na função de contato seco",
    "pv1 boost driver broken": "Falha no circuito Boost fotovoltaico",
    "usb over current": "Sobrecorrente na porta USB",
    "meter communication abnormal": "Falha de comunicação entre o inversor e o medidor",
    "eeprom abnormal": "Anomalia na memória EEPROM",
    "firmware version inconsistent": "Versão de firmware incompatível ou inconsistente",
}

ERROR_GUIDANCE_PT = {
    "302": "Verifique falta de energia, disjuntor AC e conexões do lado AC.",
    "304": (
        "A frequência fornecida pela rede saiu da faixa aceita pelo inversor. "
        "Se ocorrer repetidamente, verifique a qualidade da rede elétrica."
    ),
}


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável {name} não configurada.")
    return value


def int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def web_server() -> str:
    return os.getenv("GROWATT_WEB_SERVER", "").strip().rstrip("/") or DEFAULT_WEB_SERVER


def normalize_fault_code(value) -> str:
    match = re.match(r"(\d+)", str(value or "").strip())
    return match.group(1) if match else str(value or "").strip()


def parse_growatt_datetime(value) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=TIMEZONE)
        except ValueError:
            continue

    return None


def normalized_message_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def event_kind(fault: dict) -> str:
    text = " ".join(
        str(fault.get(key) or "")
        for key in ("eventName", "faultDescription", "alarmMessage", "eventType")
    ).lower()

    if "warn" in text or "warning" in text:
        return "warning"

    return "error"


def translated_message(fault: dict) -> str:
    source_message = (
        fault.get("eventName")
        or fault.get("faultDescription")
        or fault.get("alarmMessage")
        or ""
    )
    by_name = EVENT_NAME_MESSAGES_PT.get(normalized_message_key(source_message))
    if by_name:
        return by_name

    raw_code = (
        fault.get("eventId")
        or fault.get("eventCode")
        or fault.get("alarmCode")
        or ""
    )
    code = normalize_fault_code(raw_code)
    kind = event_kind(fault)

    if kind == "warning":
        translated = WARNING_MESSAGES_PT.get(code)
    else:
        translated = ERROR_MESSAGES_PT.get(code)

    if translated:
        return translated

    return str(
        fault.get("eventName")
        or fault.get("faultDescription")
        or fault.get("alarmMessage")
        or "Falha Growatt"
    ).strip()


def growatt_login() -> requests.Session:
    username = required_env("GROWATT_USERNAME")
    password = required_env("GROWATT_PASSWORD")
    server = web_server()

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


def query_fault_page(
    session: requests.Session,
    station_id: str,
    device_sn: str,
    day_text: str,
    page: int,
) -> dict:
    server = web_server()

    response = session.post(
        f"{server}/log/getNewPlantFaultLog",
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


def fetch_faults_for_day(
    session: requests.Session,
    station_id: str,
    device_sn: str,
    day_text: str,
) -> list[dict]:
    faults = []
    page = 1

    while True:
        payload = query_fault_page(
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


def fetch_recent_faults(
    session: requests.Session,
    station_id: str,
    device_sn: str,
) -> list[dict]:
    now = datetime.now(TIMEZONE)
    days = [now.date(), (now - timedelta(days=1)).date()]
    merged = {}

    for day in days:
        day_text = day.isoformat()
        print(f"Consultando Fault Log de {day_text}")

        for fault in fetch_faults_for_day(
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
            time_text = str(fault.get("time") or fault.get("startTime") or "")
            merged[(raw_code, time_text)] = fault

    return list(merged.values())


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


def live_data_is_fresh_and_normal(live: dict) -> bool:
    if not live:
        return False

    live_time = parse_growatt_datetime(live.get("time"))
    if live_time is None:
        return False

    max_age = timedelta(
        minutes=int_env("FAULT_LIVE_MAX_AGE_MINUTES", 30)
    )
    if datetime.now(TIMEZONE) - live_time > max_age:
        return False

    if live.get("lost") is True:
        return False

    values = (
        live.get("faultType"),
        live.get("sysFaultWord"),
        live.get("warnCode"),
        live.get("newWarnCode"),
    )
    has_fault = any(int(value or 0) != 0 for value in values)

    return int(live.get("status") or 0) == 1 and not has_fault


def event_is_recent(fault: dict) -> bool:
    event_time = parse_growatt_datetime(
        fault.get("time") or fault.get("startTime")
    )
    if event_time is None:
        return False

    age = datetime.now(TIMEZONE) - event_time
    max_age = timedelta(
        minutes=int_env("FAULT_NOTIFY_MAX_AGE_MINUTES", 120)
    )

    return timedelta(0) <= age <= max_age


def format_number(value, decimals: int = 1) -> str:
    try:
        return (
            f"{float(value):,.{decimals}f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except (TypeError, ValueError):
        return "-"


def format_event_time(value) -> str:
    parsed = parse_growatt_datetime(value)
    if parsed:
        return parsed.strftime("%d/%m/%Y às %H:%M")
    return str(value or "-")


def build_fault_message(fault: dict, live: dict) -> str:
    raw_code = str(
        fault.get("eventId")
        or fault.get("eventCode")
        or fault.get("alarmCode")
        or ""
    ).strip()
    code = normalize_fault_code(raw_code)
    message = translated_message(fault)
    recovery_time = fault.get("recoveryTime") or fault.get("endTime")

    lines = [
        "SolCare - Nova falha na usina solar",
        "",
        f"Erro {raw_code or code} - {message}",
        f"Detectado em: {format_event_time(fault.get('time') or fault.get('startTime'))}",
    ]

    guidance = ERROR_GUIDANCE_PT.get(code)
    if guidance:
        lines.extend(["", guidance])

    if recovery_time:
        lines.extend(
            [
                "",
                f"A Growatt já registra normalização em: {format_event_time(recovery_time)}",
            ]
        )

    if live:
        lines.extend(
            [
                "",
                "Última telemetria disponível:",
                f"Status: {live.get('statusText') or live.get('status') or '-'}",
                f"Tensão AC: {format_number(live.get('vac1'), 1)} V",
                f"Frequência: {format_number(live.get('fac'), 1)} Hz",
                f"Potência AC: {format_number(live.get('pac'), 0)} W",
                f"Horário: {live.get('time') or '-'}",
            ]
        )

    return "\n".join(lines)


def duration_text(started_at: datetime, resolved_at: datetime) -> str:
    start = started_at.replace(tzinfo=TIMEZONE) if started_at.tzinfo is None else started_at
    end = resolved_at.replace(tzinfo=TIMEZONE) if resolved_at.tzinfo is None else resolved_at
    minutes = max(int((end - start).total_seconds() // 60), 0)

    if minutes < 60:
        return f"{minutes} min"

    hours, remaining = divmod(minutes, 60)
    return f"{hours}h {remaining}min"


def build_recovery_message(fault: dict, resolved_at: datetime, live: dict) -> str:
    code = str(fault["fault_code_raw"] or fault["fault_code"])
    message = str(fault.get("fault_message") or "Falha Growatt")

    lines = [
        "SolCare - Sistema normalizado",
        "",
        f"Falha {code} - {message}",
        f"Início: {format_event_time(fault['fault_time'])}",
        f"Normalização: {format_event_time(resolved_at)}",
        f"Duração: {duration_text(fault['fault_time'], resolved_at)}",
    ]

    if live:
        lines.extend(
            [
                "",
                f"Status atual: {live.get('statusText') or live.get('status') or '-'}",
                f"Tensão AC: {format_number(live.get('vac1'), 1)} V",
                f"Frequência: {format_number(live.get('fac'), 1)} Hz",
            ]
        )

    return "\n".join(lines)


def send_message(message: str) -> None:
    send_whatsapp_to(
        env("WHATSAPP_PHONE", required=True),
        env("WHATSAPP_APIKEY", required=True),
        message,
    )


def import_faults(
    faults: list[dict],
    station_id: str,
    device_sn: str,
    live: dict,
) -> None:
    for fault in sorted(
        faults,
        key=lambda item: str(item.get("time") or item.get("startTime") or ""),
    ):
        event_time = parse_growatt_datetime(
            fault.get("time") or fault.get("startTime")
        )
        if event_time is None:
            print("Ignorando evento sem horário válido")
            continue

        recovery_time = parse_growatt_datetime(
            fault.get("recoveryTime") or fault.get("endTime")
        )
        recent = event_is_recent(fault)

        if recovery_time:
            initial_status = "resolved"
        elif recent:
            initial_status = "active"
        else:
            initial_status = "historical"

        record, _created = upsert_growatt_fault_event(
            station_id=station_id,
            device_sn=device_sn,
            fault_code=normalize_fault_code(
                fault.get("eventId")
                or fault.get("eventCode")
                or fault.get("alarmCode")
            ),
            fault_code_raw=str(
                fault.get("eventId")
                or fault.get("eventCode")
                or fault.get("alarmCode")
                or ""
            ).strip(),
            fault_message=translated_message(fault),
            fault_time=event_time.replace(tzinfo=None),
            recovery_time=(
                recovery_time.replace(tzinfo=None)
                if recovery_time
                else None
            ),
            device_type=str(fault.get("deviceType") or "").strip(),
            solution=str(
                fault.get("solution")
                or fault.get("eventSolution")
                or ""
            ).strip(),
            raw_payload=fault,
            initial_status=initial_status,
        )

        if not recent or record.get("notified_at"):
            continue

        send_message(build_fault_message(fault, live))
        mark_growatt_fault_notified(record["id"])

        if recovery_time:
            mark_growatt_fault_recovery_notified(record["id"])


def resolve_faults_from_log() -> None:
    active_faults = fetch_active_growatt_faults()

    for fault in active_faults:
        recovery_time = fault.get("recovery_time")
        if not recovery_time:
            continue

        mark_growatt_fault_resolved(
            event_id=fault["id"],
            resolved_at=recovery_time,
        )


def confirm_recovery_with_live_data(live: dict) -> None:
    active_faults = fetch_active_growatt_faults()
    if not active_faults:
        return

    if not live_data_is_fresh_and_normal(live):
        reset_growatt_fault_normal_checks()
        return

    required_checks = int_env("FAULT_RECOVERY_CONFIRMATIONS", 2)
    live_time = parse_growatt_datetime(live.get("time"))
    if live_time is None:
        return

    for fault in active_faults:
        if fault.get("recovery_time"):
            continue

        fault_time = fault["fault_time"]
        if fault_time.tzinfo is None:
            fault_time = fault_time.replace(tzinfo=TIMEZONE)

        if live_time <= fault_time:
            continue

        normal_checks = increment_growatt_fault_normal_check(
            fault["id"],
            live_time.replace(tzinfo=None),
        )
        if normal_checks < required_checks:
            continue

        resolved_at = datetime.now(TIMEZONE)
        mark_growatt_fault_resolved(
            event_id=fault["id"],
            resolved_at=resolved_at.replace(tzinfo=None),
        )

def send_pending_recovery_notifications(live: dict) -> None:
    for fault in fetch_pending_growatt_recovery_notifications():
        resolved_at = fault.get("recovery_time")
        if not resolved_at:
            continue

        send_message(
            build_recovery_message(
                fault=fault,
                resolved_at=resolved_at,
                live=live,
            )
        )
        mark_growatt_fault_recovery_notified(fault["id"])


def main() -> None:
    station_id = required_env("GROWATT_PLANT_ID")
    device_sn = required_env("GROWATT_DEVICE_SN")

    ensure_growatt_fault_events_table()

    session = growatt_login()
    try:
        faults = fetch_recent_faults(
            session=session,
            station_id=station_id,
            device_sn=device_sn,
        )
    finally:
        server = web_server()
        try:
            session.get(f"{server}/logout", timeout=10)
        except requests.RequestException:
            pass

    try:
        live = fetch_live_data()
    except Exception as exc:
        print(f"Telemetria V4 indisponível: {exc}")
        live = {}

    import_faults(
        faults=faults,
        station_id=station_id,
        device_sn=device_sn,
        live=live,
    )

    resolve_faults_from_log()
    confirm_recovery_with_live_data(live)
    send_pending_recovery_notifications(live)

    print(
        "Monitoramento concluído",
        {
            "faults_found": len(faults),
            "live_status": live.get("statusText") if live else None,
        },
    )


if __name__ == "__main__":
    main()
