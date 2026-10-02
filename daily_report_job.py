from growatt_client import fetch_growatt_payload
from save_monthly_snapshot import save_monthly_snapshot
from send_daily_report import send_daily_report


def main() -> None:
    payload = fetch_growatt_payload()
    print("Payload Growatt:", payload)

    errors = []

    try:
        send_daily_report(payload)
    except Exception as exc:
        errors.append(f"envio do relatório: {exc}")
        print(f"Falha no envio do relatório: {exc}")

    try:
        save_monthly_snapshot(payload)
    except Exception as exc:
        errors.append(f"snapshot mensal: {exc}")
        print(f"Falha ao salvar snapshot mensal: {exc}")

    if errors:
        raise RuntimeError(
            "Fluxo diário concluído com falha em uma ou mais etapas: "
            + " | ".join(errors)
        )

    print("Fluxo diário concluído com sucesso.")


if __name__ == "__main__":
    main()
