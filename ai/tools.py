from solar_queries import (
    get_active_faults_summary,
    get_maintenance_status,
    get_plant_status,
)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "consultar_resumo_usina",
            "description": (
                "Consulta o estado atual da usina, incluindo status do inversor, "
                "potência atual e geração de hoje, mês, ano e total."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_falhas_ativas",
            "description": (
                "Consulta falhas ou alarmes Growatt que ainda estão ativos no "
                "monitoramento do SolCare."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_manutencao",
            "description": (
                "Consulta alertas abertos da análise preventiva de desempenho, "
                "como possível sujeira, sombreamento ou inversor offline."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


class AIToolError(RuntimeError):
    pass


def execute_tool(name: str, arguments: dict) -> dict:
    if arguments:
        raise AIToolError("Esta ferramenta não aceita argumentos.")

    handlers = {
        "consultar_resumo_usina": get_plant_status,
        "consultar_falhas_ativas": get_active_faults_summary,
        "consultar_manutencao": get_maintenance_status,
    }

    handler = handlers.get(name)
    if handler is None:
        raise AIToolError("Ferramenta não reconhecida.")

    return handler()
