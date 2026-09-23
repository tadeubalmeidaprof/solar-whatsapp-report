import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from database import connect
from send_daily_report import env, send_whatsapp_to


TIMEZONE = ZoneInfo("America/Bahia")
DEFAULT_WEB_SERVER = "https://server.growatt.com"
DEFAULT_NOTIFY_MAX_AGE_MINUTES = 180


FAULT_INFO = {
    "302": {
        "title": "Sem conexão AC",
        "explanation": (
            "O inversor deixou de detectar a rede elétrica no lado AC. "
            "Verifique alimentação, disjuntor e conexões AC."
        ),
    },
    "304": {
        "title": "Frequência AC fora da faixa",
        "explanation": (
            "A frequência da rede elétrica ficou fora da faixa aceita pelo inversor. "
            "Pode ser uma oscilação momentânea da concessionária."
        ),
    },
}


def get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável {name} não configurada.")
    return value


def get_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    return int(raw)


def normalize_fault_code(value) -> str:
    text = str(value or "").strip()
    match = re.match(r"(\d+)", text)
    return match.group(1) if match else text


def parse_growatt_datetime(value) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TIMEZONE)
        except ValueError:
            pass

    return None


def ensure_fault_table() -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS growatt_fault_events (
            id BIGSERIAL PRIMARY KEY,
            station_id TEXT NOT NULL,
            device_sn TEXT NOT NULL,
            fault_code TEXT NOT NULL,
            fault_code_raw TEXT,
            fault_message TEXT,
            fault_time TIMESTAMP NOT NULL,
            recovery_time TIMESTAMP,
            device_type TEXT,
            solution TEXT,
            raw_payload JSONB,
            notified_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (device_sn, fault_code_raw, fault_time)
        );
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql)


def upsert_fault_event(station_id: str, device_sn: str, fault: dict) -> tuple[int, bool]:
    fault_code_raw = str(
        fault.get("eventId")
        or fault.get("eventCode")
        or fault.get("alarmCode")
        or ""
    ).strip()
    fault_code = normalize_fault_code(fault_code_raw)

    fault_time = parse_growatt_datetime(
        fault.get("time") or fault.get("startTime")
    )
    if fault_time is None:
        raise RuntimeError(f"Falha sem horário válido: {fault}")

    recovery_time = parse_growatt_datetime(
        fault.get("recoveryTime") or fault.get("endTime")
    )

    fault_message = str(
        fault.get("eventName")
        or fault.get("faultDescription")
        or fault.get("alarmMessage")
        or ""
    ).strip()

    device_type = str(fault.get("deviceType") or "").strip()
    solution = str(
        fault.get("solution")
        or fault.get("eventSolution")
        or ""
    ).strip()

    raw_payload = json.dumps(fault, ensure_ascii=False)

    insert_sql = """
        INSERT INTO growatt_fault_events (
            station_id,
            device_sn,
            fault_code,
            fault_code_raw,
            fault_message,
            fault_time,
            recovery_time,
            device_type,
            solution,
            raw_payload,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW())
        ON CONFLICT (device_sn, fault_code_raw, fault_time)
        DO NOTHING
        RETURNING id;
    """

    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                insert_sql,
                (
                    station_id,
                    device_sn,
                    fault_code,
                    fault_code_raw,
                    fault_message,
                    fault_time.replace(tzinfo=None),
                    recovery_time.replace(tzinfo=None) if recovery_time else None,
                    device_type,
                    solution,
                    raw_payload,
                ),
            )
            row = cursor.fetchone()

            if row:
                return int(row[0]), True

            cursor.execute(
                """
                    UPDATE growatt_fault_events
                    SET
                        recovery_time = COALESCE(%s, recovery_time),
                        fault_message = COALESCE(NULLIF(%s, ''), fault_message),
                        device_type = COALESCE(NULLIF(%s, ''), device_type),
                        solution = COALESCE(NULLIF(%s, ''), solution),
                        raw_payload = %s::jsonb,
                        updated_at = NOW()
                    WHERE device_sn = %s
                      AND fault_code_raw = %s
                      AND fault_time = %s
                    RETURNING id;
                """,
                (
                    recovery_time.replace(tzinfo=None) if recovery_time else None,
                    fault_message,
                    device_type,
                    solution,
                    raw_payload,
                    device_sn,
                    fault_code_raw,
                    fault_time.replace(tzinfo=None),
                ),
            )
            existing = cursor.fetchone()

    if not existing:
        raise RuntimeError("Não foi possível localizar o evento após o upsert.")

    return int(existing[0]), False


def mark_notified(event_id: int) -> None:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                    UPDATE growatt_fault_events
                    SET notified_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s;
                """,
                (event_id,),
            )


def growatt_login() -> requests.Session:
    username = get_required("GROWATT_USERNAME")
    password = get_required("GROWATT_PASSWORD")
    server = os.getenv("GROWATT_WEB_SERVER", DEFAULT_WEB_SERVER).strip().rstrip("/")

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

    password_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()

    response = session.post(
        f"{server}/login",
        data={
            "account": username,
            "password": "",
            "validateCode": "",
            "isReadPact": "0",
            "passwordCrc": password_md5,
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
    date_text: str,
    page: int,
) -> dict:
    server = os.getenv("GROWATT_WEB_SERVER", DEFAULT_WEB_SERVER).strip().rstrip("/")

    response = session.post(
        f"{server}/log/getNewPlantFaultLog",
        data={
            "plantId": station_id,
            "date": date_text,
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
    faults: list[dict] = []
    page = 1

    while True:
        payload = query_fault_page(
            session=session,
            station_id=station_id,
            device_sn=device_sn,
            date_text=day_text,
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

    merged: dict[tuple[str, str], dict] = {}

    for day in days:
        day_text = day.isoformat()
        print(f"Consultando Fault Log de {day_text}...")

        for fault in fetch_faults_for_day(
            session=session,
            station_id=station_id,
            device_sn=device_sn,
            day_text=day_text,
        ):
            code = str(
                fault.get("eventId")
                or fault.get("eventCode")
                or fault.get("alarmCode")
                or ""
            )
            time_text = str(fault.get("time") or fault.get("startTime") or "")
            merged[(code, time_text)] = fault

    return list(merged.values())


def fetch_live_data() -> dict:
    token = get_required("GROWATT_API_TOKEN")
    device_sn = get_required("GROWATT_DEVICE_SN")

    response = requests.post(
        "https://openapi.growatt.com/v4/new-api/queryLastData",
        headers={"token": token},
        data={
            "deviceType": "min",
            "deviceSn": device_sn,
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
    if not devices:
        return {}

    return devices[0]


def br_number(value, decimals: int = 1) -> str:
    try:
        return (
            f"{float(value):,.{decimals}f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "-"


def build_fault_message(fault: dict, live: dict) -> str:
    raw_code = str(
        fault.get("eventId")
        or fault.get("eventCode")
        or fault.get("alarmCode")
        or ""
    ).strip()
    code = normalize_fault_code(raw_code)

    original_message = str(
        fault.get("eventName")
        or fault.get("faultDescription")
        or fault.get("alarmMessage")
        or "Falha Growatt"
    ).strip()

    event_time = parse_growatt_datetime(
        fault.get("time") or fault.get("startTime")
    )
    event_time_text = (
        event_time.strftime("%d/%m/%Y às %H:%M")
        if event_time
        else str(fault.get("time") or fault.get("startTime") or "-")
    )

    info = FAULT_INFO.get(code)
    title = info["title"] if info else original_message
    explanation = (
        info["explanation"]
        if info
        else "A Growatt registrou uma nova falha no inversor."
    )

    lines = [
        "🚨 SolCare — Nova falha na usina solar",
        "",
        f"⚠️ Erro {raw_code or code} — {title}",
        f"🕐 Detectado em: {event_time_text}",
        "",
        explanation,
    ]

    if live:
        lines.extend(
            [
                "",
                "Última telemetria disponível:",
                f"• Status: {live.get('statusText') or live.get('status') or '-'}",
                f"• Tensão AC: {br_number(live.get('vac1'), 1)} V",
                f"• Frequência: {br_number(live.get('fac'), 1)} Hz",
                f"• Potência AC: {br_number(live.get('pac'), 0)} W",
                f"• Horário: {live.get('time') or '-'}",
            ]
        )

    return "\n".join(lines)


def should_notify_fault(fault: dict) -> bool:
    event_time = parse_growatt_datetime(
        fault.get("time") or fault.get("startTime")
    )
    if event_time is None:
        return False

    max_age_minutes = get_int(
        "FAULT_NOTIFY_MAX_AGE_MINUTES",
        DEFAULT_NOTIFY_MAX_AGE_MINUTES,
    )

    age = datetime.now(TIMEZONE) - event_time
    return timedelta(0) <= age <= timedelta(minutes=max_age_minutes)


def notify_fault(fault: dict) -> None:
    try:
        live = fetch_live_data()
    except Exception as exc:
        print(f"Aviso: não consegui enriquecer o alerta com telemetria V4: {exc}")
        live = {}

    message = build_fault_message(fault, live)
    print(message)

    send_whatsapp_to(
        env("WHATSAPP_PHONE", required=True),
        env("WHATSAPP_APIKEY", required=True),
        message,
    )


def main() -> None:
    station_id = get_required("GROWATT_PLANT_ID")
    device_sn = get_required("GROWATT_DEVICE_SN")

    ensure_fault_table()

    session = growatt_login()

    try:
        faults = fetch_recent_faults(
            session=session,
            station_id=station_id,
            device_sn=device_sn,
        )
    finally:
        server = os.getenv("GROWATT_WEB_SERVER", DEFAULT_WEB_SERVER).strip().rstrip("/")
        try:
            session.get(f"{server}/logout", timeout=10)
        except Exception:
            pass

    if not faults:
        print("Nenhuma falha encontrada nos últimos dois dias.")
        return

    faults.sort(
        key=lambda item: str(item.get("time") or item.get("startTime") or "")
    )

    new_events = 0
    notified_events = 0

    for fault in faults:
        event_id, created = upsert_fault_event(
            station_id=station_id,
            device_sn=device_sn,
            fault=fault,
        )

        if not created:
            continue

        new_events += 1

        if not should_notify_fault(fault):
            print(
                "Evento novo importado sem notificação por ser antigo:",
                fault.get("eventId"),
                fault.get("time"),
            )
            continue

        notify_fault(fault)
        mark_notified(event_id)
        notified_events += 1

    print(
        "Monitoramento concluído:",
        {
            "faults_found": len(faults),
            "new_events": new_events,
            "notifications_sent": notified_events,
        },
    )


if __name__ == "__main__":
    main()
