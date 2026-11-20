import unittest
from unittest.mock import patch

import fault_monitor

BASE = {
    "GREEN_API_ID": "1",
    "GREEN_API_TOKEN": "t",
    "GREEN_API_CHAT_ID": "5577999990000@c.us",
}


class FaultRecipientsTest(unittest.TestCase):
    @patch("fault_monitor.send_green_api")
    def test_sends_to_both_when_second_configured(self, send):
        env = dict(BASE, GREEN_API_CHAT_ID_2="557781527891@c.us")
        with patch.dict("os.environ", env, clear=True):
            fault_monitor.send_message("alerta")
        self.assertEqual(
            [c.kwargs["chat_id"] for c in send.call_args_list],
            ["5577999990000@c.us", "557781527891@c.us"],
        )

    @patch("fault_monitor.send_green_api")
    def test_only_primary_without_second(self, send):
        with patch.dict("os.environ", BASE, clear=True):
            fault_monitor.send_message("alerta")
        send.assert_called_once()

    @patch("fault_monitor.send_green_api")
    def test_second_failure_does_not_raise(self, send):
        send.side_effect = [True, RuntimeError("falhou")]
        env = dict(BASE, GREEN_API_CHAT_ID_2="557781527891@c.us")
        with patch.dict("os.environ", env, clear=True):
            fault_monitor.send_message("alerta")
        self.assertEqual(send.call_count, 2)

    @patch("fault_monitor.send_green_api")
    def test_primary_failure_raises(self, send):
        send.side_effect = RuntimeError("falhou")
        env = dict(BASE, GREEN_API_CHAT_ID_2="557781527891@c.us")
        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(RuntimeError):
                fault_monitor.send_message("alerta")
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
