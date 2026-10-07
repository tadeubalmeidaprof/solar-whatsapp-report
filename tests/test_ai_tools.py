import unittest
from unittest.mock import patch

from ai.tools import AIToolError, TOOL_DEFINITIONS, execute_tool


class AIToolsTests(unittest.TestCase):
    def test_tool_schemas_do_not_accept_arbitrary_arguments(self):
        for tool in TOOL_DEFINITIONS:
            parameters = tool["function"]["parameters"]
            self.assertEqual(parameters["properties"], {})
            self.assertFalse(parameters["additionalProperties"])

    @patch("ai.tools.get_plant_status")
    def test_executes_known_tool(self, get_plant_status):
        get_plant_status.return_value = {"status": "Normal"}

        result = execute_tool("consultar_resumo_usina", {})

        self.assertEqual(result, {"status": "Normal"})
        get_plant_status.assert_called_once_with()

    def test_rejects_unknown_tool(self):
        with self.assertRaises(AIToolError):
            execute_tool("apagar_banco", {})

    def test_rejects_unexpected_arguments(self):
        with self.assertRaises(AIToolError):
            execute_tool("consultar_resumo_usina", {"token": "x"})


if __name__ == "__main__":
    unittest.main()
