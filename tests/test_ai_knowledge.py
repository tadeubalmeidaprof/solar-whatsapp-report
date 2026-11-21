import unittest

from ai.knowledge import KNOWLEDGE_BASE, get_knowledge, search_knowledge
from ai.tools import TOOL_DEFINITIONS, execute_tool

TITLES = {entry["id"]: entry["titulo"] for entry in KNOWLEDGE_BASE}

# Perguntas reais no estilo do WhatsApp -> verbete que deve vir em 1º lugar.
EVAL_SET = {
    "Quantas placas tem minha usina?": "ficha_usina",
    "Qual a potência do meu sistema?": "ficha_usina",
    "Quanto minha usina deveria gerar por mês?": "geracao_esperada",
    "O calor atrapalha a geração?": "temperatura",
    "Gera energia com chuva?": "nuvens_chuva",
    "Por que minha conta não zerou?": "custo_disponibilidade",
    "O que é fio B?": "fio_b",
    "Os créditos vencem?": "compensacao_creditos",
    "Posso usar meus créditos em outra casa?": "compensacao_creditos",
    "Como limpar as placas?": "limpeza",
    "De quanto em quanto tempo devo lavar os painéis?": "limpeza",
    "Faltou energia e o sistema parou, é normal?": "falta_energia",
    "Por que o inversor fica desligado à noite?": "inversor_noite",
    "O inversor desliga várias vezes por tensão alta": "tensao_rede",
    "O que é falha de isolamento?": "isolamento",
    "Quanto tempo duram as placas?": "degradacao_modulos",
    "Dá pra instalar mais placas?": "ampliar_sistema",
    "Qual a diferença entre kW e kWh?": "potencia_vs_energia",
    "Qual o melhor horário para ligar a bomba d'água?": "simultaneidade",
    "Uma árvore está fazendo sombra, isso afeta?": "sombreamento",
    "O que significa energia injetada na conta?": "ler_conta",
    "Placa bifacial gera pelos dois lados?": "bifacial",
    "Por que a potência não passa de 8 kW?": "clipping",
    "Caiu um raio perto de casa, o que verifico?": "dps_raios",
    "É perigoso mexer no inversor?": "seguranca",
    "Como vocês sabem se a usina está bem?": "como_funciona_solcare",
    "Que horas é o pico de geração?": "curva_normal",
    "Em que época do ano gera mais?": "sazonalidade",
    "Quanto tempo leva pra pagar o investimento?": "vale_a_pena",
    "Vale a pena energia solar?": "vale_a_pena",
    "Qual a garantia do inversor?": "garantia",
    "Preciso fazer revisão anual?": "manutencao_preventiva",
}


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_eval_set_top1(self):
        misses = []
        for question, expected in EVAL_SET.items():
            results = search_knowledge(question)
            top = results[0]["titulo"] if results else None
            if top != TITLES[expected]:
                misses.append(f"{question!r} -> {top!r} (esperado {TITLES[expected]!r})")
        self.assertEqual(misses, [])

    def test_unrelated_question_returns_nothing(self):
        self.assertFalse(get_knowledge("Qual a capital da França?")["found"])

    def test_ids_are_unique(self):
        ids = [entry["id"] for entry in KNOWLEDGE_BASE]
        self.assertEqual(len(ids), len(set(ids)))


class KnowledgeToolTests(unittest.TestCase):
    def test_tool_is_registered_and_executes(self):
        names = [tool["function"]["name"] for tool in TOOL_DEFINITIONS]
        self.assertIn("consultar_base_conhecimento", names)
        result = execute_tool("consultar_base_conhecimento", {"pergunta": "O que é fio B?"})
        self.assertTrue(result["found"])
        self.assertIn("2026", result["verbetes"][0]["conteudo"])


if __name__ == "__main__":
    unittest.main()
