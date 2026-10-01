import requests


CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
GREEN_API_DEFAULT_URL = "https://api.green-api.com"


def send_whatsapp_to(phone: str, apikey: str, message: str) -> bool:
    try:
        response = requests.get(
            CALLMEBOT_URL,
            params={
                "phone": phone,
                "text": message,
                "apikey": apikey,
            },
            timeout=30,
        )
    except requests.RequestException:
        raise RuntimeError(
            "Falha de rede ao enviar WhatsApp pelo CallMeBot."
        ) from None

    response_text = response.text.lower()

    if not response.ok:
        raise RuntimeError(
            f"Falha ao enviar WhatsApp pelo CallMeBot: HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    if "invalid" in response_text or "error" in response_text:
        raise RuntimeError(f"CallMeBot retornou erro: {response.text[:300]}")

    print("WhatsApp enviado com sucesso pelo CallMeBot.")
    return True


def send_green_api(
    api_url: str,
    id_instance: str,
    api_token: str,
    chat_id: str,
    message: str,
) -> bool:
    base_url = (api_url or GREEN_API_DEFAULT_URL).rstrip("/")
    url = f"{base_url}/waInstance{id_instance}/sendMessage/{api_token}"

    try:
        response = requests.post(
            url,
            json={
                "chatId": chat_id,
                "message": message,
            },
            timeout=30,
        )
    except requests.RequestException:
        raise RuntimeError(
            "Falha de rede ao enviar WhatsApp pela GREEN-API."
        ) from None

    if not response.ok:
        raise RuntimeError(
            f"Falha ao enviar WhatsApp pela GREEN-API: HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("GREEN-API retornou uma resposta inválida.") from None

    if not payload.get("idMessage"):
        raise RuntimeError(
            f"GREEN-API não confirmou o envio: {response.text[:300]}"
        )

    print("WhatsApp enviado com sucesso pela GREEN-API.")
    return True
