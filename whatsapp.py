import requests


CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"


def send_whatsapp_to(phone: str, apikey: str, message: str) -> bool:
    response = requests.get(
        CALLMEBOT_URL,
        params={
            "phone": phone,
            "text": message,
            "apikey": apikey,
        },
        timeout=30,
    )

    response_text = response.text.lower()

    if not response.ok:
        raise RuntimeError(
            f"Falha ao enviar WhatsApp: HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    if "invalid" in response_text or "error" in response_text:
        raise RuntimeError(f"CallMeBot retornou erro: {response.text[:300]}")

    print("WhatsApp enviado com sucesso.")
    return True
