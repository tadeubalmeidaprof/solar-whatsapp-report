import requests


CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
GREEN_API_DEFAULT_URL = "https://api.green-api.com"
GREEN_API_MAX_REPLY_BUTTONS = 3
GREEN_API_MAX_BUTTON_TEXT_LENGTH = 25


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


def send_green_api_interactive(
    api_url: str,
    id_instance: str,
    api_token: str,
    chat_id: str,
    body: str,
    buttons: list[dict[str, str]],
    *,
    header: str = "",
    footer: str = "",
) -> bool:
    message_body = str(body or "").strip()
    if not message_body:
        raise ValueError("Mensagem interativa da GREEN-API não pode ser vazia.")

    if not buttons or len(buttons) > GREEN_API_MAX_REPLY_BUTTONS:
        raise ValueError(
            "Mensagem interativa da GREEN-API deve ter entre 1 e 3 botões."
        )

    normalized_buttons = []
    seen_ids = set()

    for button in buttons:
        if not isinstance(button, dict):
            raise ValueError("Cada botão deve ser um objeto com buttonId e buttonText.")

        button_id = str(button.get("buttonId") or "").strip()
        button_text = str(button.get("buttonText") or "").strip()

        if not button_id or not button_text:
            raise ValueError("Cada botão deve ter buttonId e buttonText.")

        if len(button_text) > GREEN_API_MAX_BUTTON_TEXT_LENGTH:
            raise ValueError(
                "O texto de cada botão da GREEN-API deve ter no máximo 25 caracteres."
            )

        if button_id in seen_ids:
            raise ValueError("Os IDs dos botões da GREEN-API devem ser únicos.")

        seen_ids.add(button_id)
        normalized_buttons.append(
            {
                "buttonId": button_id,
                "buttonText": button_text,
            }
        )

    base_url = (api_url or GREEN_API_DEFAULT_URL).rstrip("/")
    url = (
        f"{base_url}/waInstance{id_instance}"
        f"/sendInteractiveButtonsReply/{api_token}"
    )

    payload = {
        "chatId": chat_id,
        "body": message_body,
        "buttons": normalized_buttons,
    }

    resolved_header = str(header or "").strip()
    if resolved_header:
        payload["header"] = resolved_header

    resolved_footer = str(footer or "").strip()
    if resolved_footer:
        payload["footer"] = resolved_footer

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=30,
        )
    except requests.RequestException:
        raise RuntimeError(
            "Falha de rede ao enviar botões pela GREEN-API."
        ) from None

    if not response.ok:
        raise RuntimeError(
            "Falha ao enviar botões pela GREEN-API: "
            f"HTTP {response.status_code}: {response.text[:300]}"
        )

    try:
        response_payload = response.json()
    except ValueError:
        raise RuntimeError("GREEN-API retornou uma resposta inválida.") from None

    if not response_payload.get("idMessage"):
        raise RuntimeError(
            f"GREEN-API não confirmou o envio interativo: {response.text[:300]}"
        )

    print("Mensagem interativa enviada com sucesso pela GREEN-API.")
    return True
