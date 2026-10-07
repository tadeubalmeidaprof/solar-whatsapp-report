import unittest
from unittest.mock import patch

from bot import (
    MENU_MESSAGE,
    MONTH_BUTTON_ID,
    TODAY_BUTTON_ID,
    UNKNOWN_MESSAGE,
    build_reply,
    is_menu_request,
)


class BotTests(unittest.TestCase):
    def test_greeting_opens_menu(self):
        self.assertTrue(is_menu_request("Olá!"))
        self.assertEqual(build_reply("Olá!"), MENU_MESSAGE)

    @patch("bot.get_generation_summary")
    def test_today_button_returns_today_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,4",
            "month_kwh": "312,7",
        }

        reply = build_reply(TODAY_BUTTON_ID)

        self.assertIn("18,4 kWh hoje", reply)

    @patch("bot.get_generation_summary")
    def test_month_button_returns_month_generation(self, summary):
        summary.return_value = {
            "today_kwh": "18,4",
            "month_kwh": "312,7",
        }

        reply = build_reply(MONTH_BUTTON_ID)

        self.assertIn("312,7 kWh neste mês", reply)

    @patch("bot.get_generation_summary")
    def test_numeric_fallback_still_works(self, summary):
        summary.return_value = {
            "today_kwh": "18,4",
            "month_kwh": "312,7",
        }

        self.assertIn("18,4 kWh hoje", build_reply("1"))
        self.assertIn("312,7 kWh neste mês", build_reply("2"))

    @patch("bot.get_generation_summary")
    def test_button_label_as_text_is_supported(self, summary):
        summary.return_value = {
            "today_kwh": "18,4",
            "month_kwh": "312,7",
        }

        self.assertIn(
            "18,4 kWh hoje",
            build_reply("☀️ Geração de hoje"),
        )

    @patch("bot.ask_solcare_ai")
    def test_free_text_is_routed_to_ai(self, ask_ai):
        ask_ai.return_value = "🟢 Sua usina está normal."

        reply = build_reply("Como está minha usina?")

        self.assertEqual(reply, "🟢 Sua usina está normal.")
        ask_ai.assert_called_once_with("Como está minha usina?")

    @patch("bot.ask_solcare_ai")
    def test_ai_failure_preserves_legacy_fallback(self, ask_ai):
        ask_ai.return_value = None

        self.assertEqual(build_reply("mensagem desconhecida"), UNKNOWN_MESSAGE)

    @patch("bot.ask_solcare_ai")
    @patch("bot.get_generation_summary")
    def test_known_buttons_do_not_call_ai(self, summary, ask_ai):
        summary.return_value = {
            "today_kwh": "18,4",
            "month_kwh": "312,7",
        }

        build_reply(TODAY_BUTTON_ID)

        ask_ai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
