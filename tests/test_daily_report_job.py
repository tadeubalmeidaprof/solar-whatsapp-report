import unittest
from unittest.mock import Mock, patch

import daily_report_job


class DailyReportJobTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "plantId": "10506478",
            "energyTodayKwh": 12.3,
            "energyMonthKwh": 45.6,
        }

    @patch("daily_report_job.save_monthly_snapshot")
    @patch("daily_report_job.send_daily_report")
    @patch("daily_report_job.fetch_growatt_payload")
    def test_consulta_growatt_uma_vez_e_reutiliza_payload(
        self,
        fetch_payload,
        send_report,
        save_snapshot,
    ):
        fetch_payload.return_value = self.payload

        daily_report_job.main()

        fetch_payload.assert_called_once_with()
        send_report.assert_called_once_with(self.payload)
        save_snapshot.assert_called_once_with(self.payload)

    @patch("daily_report_job.save_monthly_snapshot")
    @patch("daily_report_job.send_daily_report")
    @patch("daily_report_job.fetch_growatt_payload")
    def test_falha_no_whatsapp_nao_impede_snapshot(
        self,
        fetch_payload,
        send_report,
        save_snapshot,
    ):
        fetch_payload.return_value = self.payload
        send_report.side_effect = RuntimeError("falha de envio")

        with self.assertRaisesRegex(RuntimeError, "envio do relatório"):
            daily_report_job.main()

        fetch_payload.assert_called_once_with()
        save_snapshot.assert_called_once_with(self.payload)

    @patch("daily_report_job.save_monthly_snapshot")
    @patch("daily_report_job.send_daily_report")
    @patch("daily_report_job.fetch_growatt_payload")
    def test_falha_no_snapshot_nao_impede_envio(
        self,
        fetch_payload,
        send_report,
        save_snapshot,
    ):
        fetch_payload.return_value = self.payload
        save_snapshot.side_effect = RuntimeError("falha no banco")

        with self.assertRaisesRegex(RuntimeError, "snapshot mensal"):
            daily_report_job.main()

        fetch_payload.assert_called_once_with()
        send_report.assert_called_once_with(self.payload)


if __name__ == "__main__":
    unittest.main()
