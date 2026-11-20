import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from ai import assistant, provider
from ai.intent_router import detect_local_intent

TODAY = date(2026, 11, 20)
YESTERDAY = date(2026, 11, 19)
TZ = ZoneInfo("America/Bahia")


class PeriodAwareRouterTests(unittest.TestCase):
    def test_month_performance_goes_to_ai(self):
        for text in (
            "Minha usina perdeu rendimento esse mês?",
            "Como foi o desempenho da usina este mês?",
            "O rendimento da semana foi bom?",
            "Desempenho de outubro foi normal?",
            "Rendeu bem nos últimos 7 dias?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(detect_local_intent(text, today=TODAY, last_complete_day=YESTERDAY))

    def test_undated_performance_uses_last_complete_day(self):
        intent = detect_local_intent("Minha usina está rendendo bem?", today=TODAY, last_complete_day=YESTERDAY)
        self.assertEqual(intent["arguments"]["report_date"], YESTERDAY.isoformat())

    def test_explicit_today_is_respected(self):
        intent = detect_local_intent("Rendeu bem hoje?", today=TODAY, last_complete_day=YESTERDAY)
        self.assertEqual(intent["arguments"]["report_date"], TODAY.isoformat())


class LastCompleteDayTests(unittest.TestCase):
    def test_before_window_end_uses_yesterday(self):
        self.assertEqual(assistant._last_complete_day(datetime(2026, 11, 20, 5, 40, tzinfo=TZ)), YESTERDAY)

    def test_after_window_end_uses_today(self):
        self.assertEqual(assistant._last_complete_day(datetime(2026, 11, 20, 18, 0, tzinfo=TZ)), TODAY)


class DegradationShortcutTests(unittest.TestCase):
    @patch("ai.assistant._safe_tool_result")
    def test_month_question_skips_degradation_shortcut(self, tool):
        self.assertIsNone(assistant._try_local_degradation_intent("Minha usina perdeu rendimento esse mês?", ""))
        tool.assert_not_called()

    @patch("ai.assistant.save_conversation_exchange")
    @patch("ai.assistant._format_slow_degradation_tool_result", return_value="ok")
    @patch("ai.assistant._safe_tool_result", return_value={"ok": True})
    def test_generic_loss_question_uses_shortcut(self, tool, _fmt, _save):
        self.assertEqual(assistant._try_local_degradation_intent("Minha usina perdeu rendimento?", ""), "ok")
        tool.assert_called_once_with("consultar_degradacao_lenta", {})


def _response(status, payload):
    response = Mock(status_code=status, ok=200 <= status < 300)
    response.json.return_value = payload
    response.text = str(payload)
    return response


class ProviderRetryTests(unittest.TestCase):
    ENV = {"GROQ_API_KEY": "k"}

    @patch("ai.provider.requests.post")
    def test_retries_once_on_tool_use_failed(self, post):
        post.side_effect = [
            _response(400, {"error": {"code": "tool_use_failed", "message": "bad call"}}),
            _response(200, {"choices": [{"message": {"content": "oi"}}]}),
        ]
        with patch.dict("os.environ", self.ENV, clear=True):
            self.assertEqual(provider.create_chat_completion([], [])["content"], "oi")
        self.assertEqual(post.call_count, 2)

    @patch("ai.provider.requests.post")
    def test_other_400_reports_detail(self, post):
        post.return_value = _response(400, {"error": {"code": "invalid_request_error", "message": "model not found"}})
        with patch.dict("os.environ", self.ENV, clear=True):
            with self.assertRaises(provider.AIProviderError) as ctx:
                provider.create_chat_completion([], [])
        self.assertIn("model not found", str(ctx.exception))
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
