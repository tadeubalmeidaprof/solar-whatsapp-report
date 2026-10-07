import unittest
from unittest.mock import patch

from ai.tools import AIToolError, TOOL_DEFINITIONS, execute_tool


class AIToolsTests(unittest.TestCase):
    def test_all_tool_schemas_reject_additional_properties(self):
        for tool in TOOL_DEFINITIONS:
            parameters = tool["function"]["parameters"]
            self.assertFalse(parameters["additionalProperties"])

    @patch("ai.tools.get_plant_status")
    def test_executes_no_argument_tool(self, get_plant_status):
        get_plant_status.return_value = {"status": "Normal"}

        result = execute_tool("consultar_resumo_usina", {})

        self.assertEqual(result, {"status": "Normal"})
        get_plant_status.assert_called_once_with()

    @patch("ai.tools.get_generation_period")
    def test_executes_tool_with_valid_arguments(self, get_generation_period):
        get_generation_period.return_value = {"total_generation_kwh": 42.0}

        result = execute_tool(
            "consultar_geracao_periodo",
            {
                "start_date": "2026-10-01",
                "end_date": "2026-10-02",
            },
        )

        self.assertEqual(result["total_generation_kwh"], 42.0)
        get_generation_period.assert_called_once_with(
            start_date="2026-10-01",
            end_date="2026-10-02",
        )

    @patch("ai.tools.get_recent_generation")
    def test_executes_recent_days_tool(self, get_recent_generation):
        get_recent_generation.return_value = {"days_with_data": 7}

        result = execute_tool(
            "consultar_ultimos_dias",
            {"days": 7},
        )

        self.assertEqual(result["days_with_data"], 7)
        get_recent_generation.assert_called_once_with(days=7)

    def test_rejects_unknown_tool(self):
        with self.assertRaises(AIToolError):
            execute_tool("apagar_banco", {})

    def test_rejects_unexpected_arguments(self):
        with self.assertRaises(AIToolError):
            execute_tool("consultar_resumo_usina", {"token": "x"})

    def test_rejects_missing_required_arguments(self):
        with self.assertRaises(AIToolError):
            execute_tool(
                "consultar_geracao_periodo",
                {"start_date": "2026-10-01"},
            )


if __name__ == "__main__":
    unittest.main()
