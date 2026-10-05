import unittest
from unittest.mock import patch

import webhook


AUTHORIZED_CHAT_ID = "5511999999999@c.us"
INSTANCE_ID = "123456"
WEBHOOK_TOKEN = "secret-test"

BASE_ENV = {
    "AUTHORIZED_CHAT_ID": AUTHORIZED_CHAT_ID,
    "GREEN_API_URL": "https://api.green-api.com",
    "GREEN_API_ID": INSTANCE_ID,
    "GREEN_API_TOKEN": "api-token-test",
    "GREEN_API_WEBHOOK_TOKEN": WEBHOOK_TOKEN,
}


class WebhookParsingTests(unittest.TestCase):
    def test_extracts_text_message(self):
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "idMessage": "message-text",
            "senderData": {"chatId": AUTHORIZED_CHAT_ID},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Olá"},
            },
        }

        self.assertEqual(
            webhook.extract_incoming_message(payload),
            ("message-text", AUTHORIZED_CHAT_ID, "Olá"),
        )

    def test_extracts_extended_text_message(self):
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "idMessage": "message-extended",
            "senderData": {"chatId": AUTHORIZED_CHAT_ID},
            "messageData": {
                "typeMessage": "extendedTextMessage",
                "extendedTextMessageData": {"text": "menu"},
            },
        }

        self.assertEqual(
            webhook.extract_incoming_message(payload),
            ("message-extended", AUTHORIZED_CHAT_ID, "menu"),
        )

    def test_extracts_template_button_reply(self):
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "idMessage": "message-button",
            "senderData": {"chatId": AUTHORIZED_CHAT_ID},
            "messageData": {
                "typeMessage": "templateButtonsReplyMessage",
                "templateButtonReplyMessage": {
                    "selectedId": "generation_today",
                    "selectedDisplayText": "☀️ Geração de hoje",
                },
            },
        }

        self.assertEqual(
            webhook.extract_incoming_message(payload),
            (
                "message-button",
                AUTHORIZED_CHAT_ID,
                "generation_today",
            ),
        )

    def test_extracts_legacy_button_response(self):
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "idMessage": "message-legacy-button",
            "senderData": {"chatId": AUTHORIZED_CHAT_ID},
            "messageData": {
                "typeMessage": "buttonsResponseMessage",
                "buttonsResponseMessage": {
                    "selectedButtonId": "generation_month",
                    "selectedButtonText": "📊 Geração do mês",
                },
            },
        }

        self.assertEqual(
            webhook.extract_incoming_message(payload),
            (
                "message-legacy-button",
                AUTHORIZED_CHAT_ID,
                "generation_month",
            ),
        )


class WebhookSecurityTests(unittest.TestCase):
    def setUp(self):
        webhook._recent_message_ids.clear()

    def test_authorization_accepts_bearer_token(self):
        with patch.dict("os.environ", BASE_ENV, clear=True):
            self.assertTrue(
                webhook.webhook_authorized(
                    f"Bearer {WEBHOOK_TOKEN}"
                )
            )
            self.assertFalse(
                webhook.webhook_authorized("Bearer wrong-token")
            )

    def test_health_does_not_require_secrets(self):
        with patch.dict("os.environ", {}, clear=True):
            client = webhook.app.test_client()
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_webhook_rejects_missing_authorization(self):
        with patch.dict("os.environ", BASE_ENV, clear=True):
            client = webhook.app.test_client()
            response = client.post(
                "/webhook/green-api",
                json={},
            )

        self.assertEqual(response.status_code, 401)

    def test_webhook_rejects_other_instance(self):
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "instanceData": {"idInstance": "999999"},
            "idMessage": "message-other-instance",
            "senderData": {"chatId": AUTHORIZED_CHAT_ID},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Olá"},
            },
        }

        with patch.dict("os.environ", BASE_ENV, clear=True):
            client = webhook.app.test_client()
            response = client.post(
                "/webhook/green-api",
                headers={
                    "Authorization": f"Bearer {WEBHOOK_TOKEN}",
                },
                json=payload,
            )

        self.assertEqual(response.status_code, 403)

    @patch("webhook._executor.submit")
    def test_valid_webhook_is_dispatched_once(self, submit):
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "instanceData": {"idInstance": int(INSTANCE_ID)},
            "idMessage": "message-valid",
            "senderData": {"chatId": AUTHORIZED_CHAT_ID},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "Olá"},
            },
        }

        with patch.dict("os.environ", BASE_ENV, clear=True):
            client = webhook.app.test_client()

            first = client.post(
                "/webhook/green-api",
                headers={
                    "Authorization": f"Bearer {WEBHOOK_TOKEN}",
                },
                json=payload,
            )
            second = client.post(
                "/webhook/green-api",
                headers={
                    "Authorization": f"Bearer {WEBHOOK_TOKEN}",
                },
                json=payload,
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        submit.assert_called_once_with(
            webhook.process_message,
            AUTHORIZED_CHAT_ID,
            "Olá",
        )


class MessageProcessingTests(unittest.TestCase):
    @patch("webhook.send_green_api")
    @patch("webhook.send_green_api_interactive")
    def test_greeting_sends_interactive_menu(
        self,
        send_interactive,
        send_text,
    ):
        with patch.dict("os.environ", BASE_ENV, clear=True):
            webhook.process_message(AUTHORIZED_CHAT_ID, "Olá!")

        send_interactive.assert_called_once()
        send_text.assert_not_called()

    @patch("webhook.send_green_api")
    @patch("webhook.send_green_api_interactive")
    def test_interactive_failure_uses_text_fallback(
        self,
        send_interactive,
        send_text,
    ):
        send_interactive.side_effect = RuntimeError(
            "Falha simulada."
        )

        with patch.dict("os.environ", BASE_ENV, clear=True):
            webhook.process_message(AUTHORIZED_CHAT_ID, "menu")

        send_interactive.assert_called_once()
        send_text.assert_called_once()

    @patch("webhook.send_green_api")
    @patch("webhook.send_green_api_interactive")
    def test_unauthorized_chat_is_ignored(
        self,
        send_interactive,
        send_text,
    ):
        with patch.dict("os.environ", BASE_ENV, clear=True):
            webhook.process_message(
                "5500000000000@c.us",
                "Olá",
            )

        send_interactive.assert_not_called()
        send_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
