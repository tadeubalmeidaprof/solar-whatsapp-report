from solar_queries import (
    compare_months,
    get_active_faults_summary,
    get_comprehensive_analysis,
    get_fault_code_info,
    get_generation_period,
    get_recent_generation,
    get_maintenance_status,
    get_plant_status,
    get_savings_summary,
    get_weather_summary,
)


def _no_arguments_tool(name: str, description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    }


TOOL_DEFINITIONS = [
    _no_arguments_tool(
        "consultar_resumo_usina",
        (
            "Consulta o estado atual da usina, incluindo status do inversor, "
            "potência atual e geração de hoje, mês, ano e total."
        ),
    ),
    _no_arguments_tool(
        "consultar_falhas_ativas",
        (
            "Consulta falhas ou alarmes Growatt que ainda estão ativos no "
            "monitoramento do SolCare."
        ),
    ),
    _no_arguments_tool(
        "consultar_manutencao",
        (
            "Consulta alertas abertos da análise preventiva de desempenho, "
            "como possível sujeira, sombreamento ou inversor offline."
        ),
    ),
    {
        "type": "function",
        "function": {
            "name": "consultar_geracao_periodo",
            "description": (
                "Consulta a geração diária acumulada em um intervalo de até 93 dias. "
                "Use para perguntas como ontem, última semana ou datas específicas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Data inicial no formato YYYY-MM-DD.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Data final no formato YYYY-MM-DD.",
                    },
                },
                "required": ["start_date", "end_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_ultimos_dias",
            "description": (
                "Consulta a geração dos últimos N dias incluindo hoje. "
                "Use esta ferramenta para frases como 'últimos 7 dias' e "
                "'últimos 30 dias', sem calcular as datas manualmente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 31,
                        "description": "Quantidade de dias, incluindo hoje.",
                    },
                },
                "required": ["days"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "comparar_meses",
            "description": (
                "Compara a geração de dois meses. Quando um deles é o mês atual, "
                "retorna também uma comparação justa do mesmo número de dias "
                "e sinaliza que os totais mensais brutos não são equivalentes. "
                "Use datas no formato YYYY-MM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "first_year_month": {
                        "type": "string",
                        "description": "Primeiro mês no formato YYYY-MM.",
                    },
                    "second_year_month": {
                        "type": "string",
                        "description": "Segundo mês no formato YYYY-MM.",
                    },
                },
                "required": ["first_year_month", "second_year_month"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_economia_mes",
            "description": (
                "Calcula a economia estimada de um mês com base na geração e "
                "nas tarifas configuradas no SolCare."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {
                        "type": "string",
                        "description": "Mês no formato YYYY-MM.",
                    },
                },
                "required": ["year_month"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_clima",
            "description": (
                "Consulta o clima registrado para uma data e, para hoje, pode "
                "consultar o Open-Meteo se ainda não houver registro."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_date": {
                        "type": "string",
                        "description": "Data no formato YYYY-MM-DD.",
                    },
                },
                "required": ["report_date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explicar_codigo_falha",
            "description": (
                "Explica um código de erro ou aviso Growatt conhecido e, quando "
                "disponível, informa uma orientação segura de verificação."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Código Growatt, por exemplo 302.",
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    _no_arguments_tool(
        "analisar_usina",
        (
            "Executa uma visão consolidada da usina: estado atual, falhas ativas, "
            "manutenção preventiva, clima do dia e economia do mês quando disponível. "
            "Use quando o usuário pedir uma análise completa ou perguntar se está tudo bem."
        ),
    ),
]


class AIToolError(RuntimeError):
    pass


_TOOL_HANDLERS = {
    "consultar_resumo_usina": ("get_plant_status", set(), set()),
    "consultar_falhas_ativas": ("get_active_faults_summary", set(), set()),
    "consultar_manutencao": ("get_maintenance_status", set(), set()),
    "consultar_geracao_periodo": (
        "get_generation_period",
        {"start_date", "end_date"},
        {"start_date", "end_date"},
    ),
    "consultar_ultimos_dias": (
        "get_recent_generation",
        {"days"},
        {"days"},
    ),
    "comparar_meses": (
        "compare_months",
        {"first_year_month", "second_year_month"},
        {"first_year_month", "second_year_month"},
    ),
    "consultar_economia_mes": (
        "get_savings_summary",
        {"year_month"},
        {"year_month"},
    ),
    "consultar_clima": (
        "get_weather_summary",
        {"report_date"},
        {"report_date"},
    ),
    "explicar_codigo_falha": (
        "get_fault_code_info",
        {"code"},
        {"code"},
    ),
    "analisar_usina": ("get_comprehensive_analysis", set(), set()),
}


def execute_tool(name: str, arguments: dict) -> dict:
    if not isinstance(arguments, dict):
        raise AIToolError("Argumentos da ferramenta devem ser um objeto.")

    spec = _TOOL_HANDLERS.get(name)
    if spec is None:
        raise AIToolError("Ferramenta não reconhecida.")

    handler_name, required, allowed = spec
    received = set(arguments)

    unexpected = received - allowed
    if unexpected:
        raise AIToolError("A ferramenta recebeu argumentos não permitidos.")

    missing = required - received
    if missing:
        raise AIToolError("A ferramenta não recebeu todos os argumentos obrigatórios.")

    handler = globals()[handler_name]

    try:
        return handler(**arguments)
    except (TypeError, ValueError) as exc:
        raise AIToolError(str(exc)) from exc
