import hmac
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from flask import Flask, jsonify, request

from bot import (
    MENU_BODY,
    MENU_BUTTONS,
    MENU_FOOTER,
    MENU_HEADER,
    MENU_MESSAGE,
    build_reply,
    is_menu_request,
)
from config import env, required_env
from whatsapp import send_green_api, send_green_api_interactive


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_executor = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="green-webhook",
)

_RECENT_MESSAGE_TTL_SECONDS = 3600
_MAX_RECENT_MESSAGE_IDS = 1000
_recent_message_ids: dict[str, float] = {}
_recent_message_lock = Lock()


def authorized_chat_id() -> str:
    return required_env("AUTHORIZED_CHAT_ID")


def reply_chat_id(incoming_chat_id: str) -> str:
    return env("GREEN_API_REPLY_CHAT_ID") or incoming_chat_id


def authorized_instance_id() -> str:
    return required_env("GREEN_API_ID")


def _expected_authorization_header() -> str:
    token = required_env("GREEN_API_WEBHOOK_TOKEN")
    lowered = token.lower()

    if lowered.startswith("bearer ") or lowered.startswith("basic "):
        return token

    return f"Bearer {token}"


def webhook_authorized(authorization_header: str) -> bool:
    expected = _expected_authorization_header()
    actual = str(authorization_header or "").strip()
    return hmac.compare_digest(actual, expected)


def _green_api_settings() -> tuple[str, str, str]:
    return (
        env("GREEN_API_URL", "https://api.green-api.com"),
        required_env("GREEN_API_ID"),
        required_env("GREEN_API_TOKEN"),
    )


def _extract_text(message_data: dict) -> str:
    message_type = str(message_data.get("typeMessage") or "").strip()

    if message_type == "textMessage":
        data = message_data.get("textMessageData")
        if isinstance(data, dict):
            return str(data.get("textMessage") or "").strip()

    if message_type == "extendedTextMessage":
        data = message_data.get("extendedTextMessageData")
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()

    return ""


def _extract_button_selection(message_data: dict) -> str:
    message_type = str(message_data.get("typeMessage") or "").strip()

    if message_type in {
        "templateButtonsReplyMessage",
        "templateButtonReplyMessage",
    }:
        data = (
            message_data.get("templateButtonReplyMessage")
            or message_data.get("templateButtonsReplyMessage")
            or {}
        )
        if isinstance(data, dict):
            return str(
                data.get("selectedId")
                or data.get("selectedButtonId")
                or data.get("selectedDisplayText")
                or ""
            ).strip()

    if message_type == "buttonsResponseMessage":
        data = message_data.get("buttonsResponseMessage")
        if isinstance(data, dict):
            return str(
                data.get("selectedButtonId")
                or data.get("selectedButtonText")
                or ""
            ).strip()

    if message_type == "interactiveButtonsResponse":
        data = message_data.get("interactiveButtonsResponse")
        if isinstance(data, dict):
            return str(
                data.get("selectedId")
                or data.get("selectedButtonId")
                or data.get("buttonId")
                or data.get("selectedDisplayText")
                or ""
            ).strip()

    if message_type in {
        "interactiveButtonsReply",
        "interactiveButtonReply",
    }:
        data = (
            message_data.get("interactiveButtonsReply")
            or message_data.get("interactiveButtonReply")
            or {}
        )
        if isinstance(data, dict):
            return str(
                data.get("selectedId")
                or data.get("selectedButtonId")
                or data.get("buttonId")
                or data.get("selectedDisplayText")
                or ""
            ).strip()

    return ""


def extract_incoming_message(
    payload: dict,
) -> tuple[str, str, str] | None:
    if not isinstance(payload, dict):
        return None

    if payload.get("typeWebhook") != "incomingMessageReceived":
        return None

    sender_data = payload.get("senderData")
    message_data = payload.get("messageData")

    if not isinstance(sender_data, dict) or not isinstance(message_data, dict):
        return None

    chat_id = str(sender_data.get("chatId") or "").strip()
    if not chat_id:
        return None

    body = _extract_text(message_data) or _extract_button_selection(message_data)
    if not body:
        return None

    message_id = str(payload.get("idMessage") or "").strip()
    return message_id, chat_id, body


def _is_duplicate_message(message_id: str) -> bool:
    if not message_id:
        return False

    now = time.monotonic()

    with _recent_message_lock:
        expired_before = now - _RECENT_MESSAGE_TTL_SECONDS

        expired_ids = [
            stored_id
            for stored_id, stored_at in _recent_message_ids.items()
            if stored_at < expired_before
        ]
        for stored_id in expired_ids:
            _recent_message_ids.pop(stored_id, None)

        if message_id in _recent_message_ids:
            return True

        _recent_message_ids[message_id] = now

        if len(_recent_message_ids) > _MAX_RECENT_MESSAGE_IDS:
            oldest_id = min(
                _recent_message_ids,
                key=_recent_message_ids.get,
            )
            _recent_message_ids.pop(oldest_id, None)

    return False


def _send_text(chat_id: str, message: str) -> None:
    api_url, id_instance, api_token = _green_api_settings()
    send_green_api(
        api_url=api_url,
        id_instance=id_instance,
        api_token=api_token,
        chat_id=chat_id,
        message=message,
    )


def _send_menu(chat_id: str) -> None:
    api_url, id_instance, api_token = _green_api_settings()

    try:
        send_green_api_interactive(
            api_url=api_url,
            id_instance=id_instance,
            api_token=api_token,
            chat_id=chat_id,
            header=MENU_HEADER,
            body=MENU_BODY,
            footer=MENU_FOOTER,
            buttons=MENU_BUTTONS,
        )
        logger.info("Menu interativo enviado pela GREEN-API.")
    except (RuntimeError, ValueError):
        logger.exception(
            "Falha no menu interativo; tentando fallback textual."
        )
        send_green_api(
            api_url=api_url,
            id_instance=id_instance,
            api_token=api_token,
            chat_id=chat_id,
            message=MENU_MESSAGE,
        )
        logger.info("Menu textual de fallback enviado pela GREEN-API.")


def process_message(chat_id: str, body: str) -> None:
    try:
        if chat_id != authorized_chat_id():
            logger.info("Mensagem ignorada de chat não autorizado.")
            return

        target_chat_id = reply_chat_id(chat_id)

        if is_menu_request(body):
            _send_menu(target_chat_id)
            return

        _send_text(target_chat_id, build_reply(body))
        logger.info("Resposta do bot enviada pela GREEN-API.")
    except Exception:
        logger.exception("Falha ao processar mensagem recebida da GREEN-API.")


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.post("/webhook/green-api")
def green_api_webhook():
    try:
        if not webhook_authorized(request.headers.get("Authorization", "")):
            logger.warning("Webhook recusado por falha de autenticação.")
            return "", 401
    except RuntimeError:
        logger.exception("GREEN_API_WEBHOOK_TOKEN não está configurado.")
        return "", 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return "", 400

    if payload.get("typeWebhook") != "incomingMessageReceived":
        return "", 200

    instance_data = payload.get("instanceData")
    if not isinstance(instance_data, dict):
        return "", 400

    try:
        instance_id = str(instance_data.get("idInstance") or "").strip()
        if instance_id != authorized_instance_id():
            logger.warning("Webhook ignorado por ID de instância divergente.")
            return "", 403
    except RuntimeError:
        logger.exception("GREEN_API_ID não está configurado.")
        return "", 503

    incoming = extract_incoming_message(payload)
    if incoming is None:
        message_data = payload.get("messageData")
        message_type = (
            str(message_data.get("typeMessage") or "").strip()
            if isinstance(message_data, dict)
            else ""
        )
        logger.info(
            "Webhook recebido sem conteúdo acionável: typeMessage=%s",
            message_type or "desconhecido",
        )
        return "", 200

    message_id, chat_id, body = incoming

    if _is_duplicate_message(message_id):
        logger.info("Webhook duplicado ignorado: %s", message_id)
        return "", 200

    _executor.submit(process_message, chat_id, body)
    return "", 200
