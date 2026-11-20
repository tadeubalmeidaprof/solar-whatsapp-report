import unittest
from unittest.mock import Mock, patch

from whatsapp import send_green_api_interactive


class GreenApiInteractiveTests(unittest.TestCase):
    @patch("whatsapp.requests.post")
    def test_sends_interactive_buttons_with_expected_contract(self, post):
        response = Mock()
        response.ok = True
        response.json.return_value = {"idMessage": "message-123"}
        post.return_value = response

        result = send_green_api_interactive(
            api_url="https://api.green-api.com",
            id_instance="123456",
            api_token="token-test",
            chat_id="5511999999999@c.us",
            header="SolCare",
            body="Selecione uma opção:",
            footer="Digite menu para voltar.",
            buttons=[
                {
                    "buttonId": "generation_today",
                    "buttonText": "☀️ Geração de hoje",
                },
                {
                    "buttonId": "generation_month",
                    "buttonText": "📊 Geração do mês",
                },
            ],
        )

        self.assertTrue(result)
        post.assert_called_once_with(
            (
                "https://api.green-api.com/waInstance123456"
                "/sendInteractiveButtonsReply/token-test"
            ),
            json={
                "chatId": "5511999999999@c.us",
                "header": "SolCare",
                "body": "Selecione uma opção:",
                "footer": "Digite menu para voltar.",
                "buttons": [
                    {
                        "buttonId": "generation_today",
                        "buttonText": "☀️ Geração de hoje",
                    },
                    {
                        "buttonId": "generation_month",
                        "buttonText": "📊 Geração do mês",
                    },
                ],
            },
            timeout=30,
        )

    def test_rejects_more_than_three_buttons(self):
        with self.assertRaises(ValueError):
            send_green_api_interactive(
                api_url="https://api.green-api.com",
                id_instance="123456",
                api_token="token-test",
                chat_id="5511999999999@c.us",
                body="Teste",
                buttons=[
                    {
                        "buttonId": str(index),
                        "buttonText": f"Botão {index}",
                    }
                    for index in range(4)
                ],
            )

    def test_rejects_duplicate_button_ids(self):
        with self.assertRaises(ValueError):
            send_green_api_interactive(
                api_url="https://api.green-api.com",
                id_instance="123456",
                api_token="token-test",
                chat_id="5511999999999@c.us",
                body="Teste",
                buttons=[
                    {"buttonId": "same", "buttonText": "Primeiro"},
                    {"buttonId": "same", "buttonText": "Segundo"},
                ],
            )


if __name__ == "__main__":
    unittest.main()


class NormalizeChatIdTest(unittest.TestCase):
    def test_accepts_formatted_phone(self):
        from whatsapp import normalize_chat_id
        self.assertEqual(normalize_chat_id("+55 (77) 99999-8888"), "5577999998888@c.us")

    def test_keeps_existing_chat_id(self):
        from whatsapp import normalize_chat_id
        self.assertEqual(normalize_chat_id(" 5577999998888@c.us "), "5577999998888@c.us")
        self.assertEqual(normalize_chat_id("123@lid"), "123@lid")
