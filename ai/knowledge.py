"""Base de conhecimento do SolCare.

Respostas conceituais (o que é, como funciona, por que, vale a pena, posso...)
que não dependem de dados ao vivo da usina. A IA consulta esta base pela
ferramenta consultar_base_conhecimento e deve combinar o que vem daqui com as
ferramentas de dados quando a pergunta pedir números da usina.

Regras para editar:
- Escreva só o que é verdade e verificável; na dúvida, deixe a ressalva no texto.
- Números da usina vêm de pv_site.json / ferramentas, não daqui, exceto a ficha.
- Mantenha cada verbete curto (o modelo resume para o WhatsApp).
"""

from __future__ import annotations

import re
import unicodedata

KNOWLEDGE_BASE: list[dict] = [
    # ------------------------------------------------------------------ usina
    {
        "id": "ficha_usina",
        "titulo": "Ficha técnica da usina",
        "palavras": "potencia kwp placas paineis modulos quantos inversor tamanho sistema ficha tecnica capacidade watts marca modelo bifacial",
        "conteudo": (
            "Usina residencial on-grid em Ibitira (Rio do Antônio, BA). "
            "Potência dos painéis (DC): 8,4 kWp, com 12 módulos DAH DHJ-66Y18/DG de 700 W, bifaciais. "
            "Inversor Growatt da linha MIN, com potência nominal AC de 8 kW. "
            "Coeficiente de temperatura dos módulos: -0,24% por °C acima de 25 °C na célula. "
            "Ligação considerada no cálculo de economia: monofásica. "
            "Está na regra nova da Lei 14.300 (GD II): paga a parcela escalonada do Fio B, 60% em 2026. "
            "Inclinação e orientação do telhado ainda não foram cadastradas no sistema."
        ),
    },
    {
        "id": "geracao_esperada",
        "titulo": "Quanto a usina deveria gerar",
        "palavras": "quanto deveria gerar esperado media normal expectativa por dia por mes kwh ideal producao esperada estimativa irradiacao hsp horas de sol pleno",
        "conteudo": (
            "Na região de Rio do Antônio (sudoeste da Bahia), a irradiação média fica em torno de 5 a 6 kWh/m² por dia "
            "(as chamadas horas de sol pleno). Com 8,4 kWp e perdas típicas de 20 a 25% (temperatura, cabos, inversor, sujeira), "
            "a referência é algo próximo de 33 a 40 kWh em um dia bom de céu limpo, e cerca de 950 a 1.150 kWh por mês, "
            "variando com a estação e as nuvens. Isso é uma faixa de referência teórica: para dizer se um dia ou mês "
            "específico foi normal, use os dados reais e o diagnóstico de desempenho, que comparam com o histórico da própria usina."
        ),
    },
    {
        "id": "sazonalidade",
        "titulo": "Variação ao longo do ano",
        "palavras": "estacao verao inverno epoca do ano sazonal mes melhor pior chuva seca por que gerou menos este mes variacao anual",
        "conteudo": (
            "A geração muda ao longo do ano. Dias mais longos e sol mais alto aumentam a produção; meses chuvosos e "
            "nublados reduzem bastante. No sudoeste baiano, a estação chuvosa costuma concentrar-se de novembro a março, "
            "o que pode reduzir a geração mesmo sendo verão, enquanto o período seco tem céu mais limpo mas acumula mais "
            "poeira nas placas. Comparar um mês com o mesmo mês de outro ano é mais justo do que comparar meses de estações diferentes."
        ),
    },
    {
        "id": "temperatura",
        "titulo": "Efeito do calor",
        "palavras": "calor temperatura quente esquenta perde eficiencia alta temperatura sol forte gera menos meio dia coeficiente",
        "conteudo": (
            "Painéis solares perdem eficiência quando esquentam. Nos módulos desta usina, a perda é de cerca de 0,24% "
            "para cada grau acima de 25 °C na célula. Em sol forte, a célula costuma ficar 20 a 30 °C acima da temperatura "
            "do ar, então num dia de 32 °C a célula pode passar de 55 °C e a potência cair perto de 7%. O coeficiente "
            "desses módulos é baixo (bom) em comparação com painéis comuns, que perdem 0,35 a 0,40% por °C. "
            "Sem medir a temperatura do módulo, o sistema não atribui uma perda exata ao calor."
        ),
    },
    {
        "id": "nuvens_chuva",
        "titulo": "Nuvens, chuva e dias nublados",
        "palavras": "nuvem nublado chuva chuvoso dia ruim gera com chuva gera nublado sombra de nuvem luz difusa",
        "conteudo": (
            "Em dia nublado a usina continua gerando com a luz difusa, mas bem menos: algo como 10 a 50% de um dia limpo, "
            "conforme a espessura das nuvens. Nuvens passageiras fazem a curva de potência oscilar ao longo do dia, o que é normal. "
            "A chuva em si não estraga nada e ainda ajuda a tirar parte da poeira das placas."
        ),
    },
    {
        "id": "bifacial",
        "titulo": "Módulos bifaciais",
        "palavras": "bifacial dois lados parte de tras traseira reflexo albedo ganho bifacial",
        "conteudo": (
            "Os módulos desta usina são bifaciais: captam luz também pela face traseira, vinda do reflexo do telhado ou do chão. "
            "O ganho extra depende da altura dos módulos e da cor da superfície embaixo; em telhado com módulos rentes, "
            "costuma ser pequeno (poucos por cento). Superfícies claras e mais espaço atrás aumentam o ganho."
        ),
    },
    {
        "id": "clipping",
        "titulo": "Limite do inversor (clipping)",
        "palavras": "limite inversor clipping teto 8 kw nao passa de potencia maxima achatada curva reta pico limitado sobredimensionado",
        "conteudo": (
            "O inversor converte no máximo 8 kW, enquanto os painéis somam 8,4 kWp. Em momentos raros de sol muito forte e "
            "painéis frios, a potência pode encostar em 8 kW e a curva fica achatada no topo (clipping). Com essa proporção "
            "(cerca de 1,05), a perda anual por isso é desprezível; é uma escolha de projeto comum, não defeito."
        ),
    },
    {
        "id": "inversor_noite",
        "titulo": "Inversor desligado ou offline",
        "palavras": "inversor desligado apagado offline noite sem sinal tela apagada nao aparece dados aplicativo shinephone sem conexao wifi atualizado",
        "conteudo": (
            "O inversor desliga sozinho ao anoitecer e religa de manhã quando há luz suficiente; aparecer offline à noite é normal. "
            "De dia, ficar offline no aplicativo pode ser só falta de internet do datalogger (Wi-Fi), sem que a usina pare de gerar. "
            "Os dados da Growatt também podem chegar com alguns minutos de atraso. Se ficar offline de dia por horas, vale verificar "
            "o roteador e o status do inversor, ou perguntar aqui pelas falhas ativas."
        ),
    },
    {
        "id": "falta_energia",
        "titulo": "Falta de energia da rede",
        "palavras": "faltou energia falta de luz apagao queda de energia sistema funciona sem rede nao gera sem energia anti ilhamento bateria backup",
        "conteudo": (
            "Sistemas on-grid como este desligam automaticamente quando falta energia da concessionária. É uma proteção obrigatória "
            "(anti-ilhamento) para não energizar a rede enquanto técnicos trabalham nela. Por isso, durante uma queda de energia a "
            "casa fica sem luz mesmo com sol. Quando a rede volta, o inversor espera alguns minutos e religa sozinho. "
            "Para ter energia na falta da rede seria preciso um sistema híbrido com baterias."
        ),
    },
    {
        "id": "tensao_rede",
        "titulo": "Desligamentos por tensão da rede",
        "palavras": "tensao alta sobretensao rede fora da faixa grid voltage desarma desliga varias vezes ao dia ac voltage frequencia concessionaria coelba",
        "conteudo": (
            "Um motivo comum de o inversor desligar e religar durante o dia é a tensão da rede sair da faixa permitida, sobretudo "
            "sobretensão em redes rurais ou de final de linha: quando muitas usinas injetam ao mesmo tempo, a tensão sobe e o "
            "inversor se desconecta por segurança. Se isso se repetir, o caminho é registrar reclamação de nível de tensão na "
            "concessionária (na Bahia, a Neoenergia Coelba) e avisar o integrador. Não se deve alterar os limites de proteção do inversor por conta própria."
        ),
    },
    {
        "id": "isolamento",
        "titulo": "Falha de isolamento",
        "palavras": "isolamento isolation fault resistencia baixa pv isolation umidade orvalho manha cedo fuga a terra aterramento",
        "conteudo": (
            "Falha de isolamento indica possível fuga de corrente do lado dos painéis para a terra. Pode aparecer de manhã cedo com "
            "orvalho ou após chuva e sumir quando seca, mas se repetir com frequência pode indicar cabo danificado, conector com "
            "umidade ou problema em módulo. É uma verificação para técnico com equipamento de medição; não abra caixas de junção."
        ),
    },
    {
        "id": "sombreamento",
        "titulo": "Sombra nas placas",
        "palavras": "sombra sombreamento arvore antena caixa dagua parte da placa sombreada horario perda sombra",
        "conteudo": (
            "Mesmo uma sombra pequena pode reduzir bastante a geração, porque os módulos ligados em série tendem a ser limitados "
            "pelo mais fraco (os diodos de bypass atenuam, mas não eliminam). Sombra de árvore que cresceu, antena ou caixa d'água "
            "costuma aparecer sempre no mesmo horário: na curva de geração surge uma queda recorrente. Podar e evitar novos obstáculos ajuda."
        ),
    },
    # ------------------------------------------------------------- manutenção
    {
        "id": "limpeza",
        "titulo": "Limpeza das placas",
        "palavras": "limpar limpeza lavar lavagem paineis placas sujeira poeira suja como limpar quando limpar frequencia de quanto em quanto tempo produto sabao agua mangueira",
        "conteudo": (
            "Sujeira e poeira costumam reduzir alguns por cento da geração, e mais em períodos secos sem chuva ou com fezes de "
            "pássaros. Recomendações gerais: limpar cedo pela manhã ou no fim da tarde, com placas frias; usar água limpa e escova "
            "macia ou rodo de espuma; não usar sabão abrasivo, produtos químicos nem jato de alta pressão; não pisar nos módulos. "
            "Trabalho em telhado exige equipamento de segurança, então em muitos casos vale chamar o integrador. "
            "Depois de limpar, informe aqui (ex.: 'limpei as placas hoje') para o sistema medir o efeito."
        ),
    },
    {
        "id": "manutencao_preventiva",
        "titulo": "Manutenção preventiva",
        "palavras": "manutencao preventiva revisao periodica anual o que verificar inspecao tecnico string box dps reaperto termografia",
        "conteudo": (
            "Uma inspeção preventiva por técnico, em geral anual, costuma incluir: verificação visual de módulos (trincas, "
            "manchas, pontos quentes), estado de cabos e conectores, fixação da estrutura, quadro de proteção (string box, "
            "disjuntores e DPS), aterramento e leitura de alarmes do inversor. O SolCare acompanha a geração e avisa quando há "
            "indício de queda persistente, mas isso não substitui a inspeção física."
        ),
    },
    {
        "id": "dps_raios",
        "titulo": "Raios e DPS",
        "palavras": "raio raios caiu um raio descarga atmosferica tempestade trovoada relampago dps protetor de surto queimou protecao",
        "conteudo": (
            "O DPS (dispositivo de proteção contra surtos) protege o inversor e os equipamentos de surtos causados por raios e "
            "manobras na rede. Depois de tempestades fortes vale verificar se o DPS não está sinalizando que atuou ou queimou; "
            "ele é peça de reposição e deve ser trocado por técnico."
        ),
    },
    {
        "id": "seguranca",
        "titulo": "Segurança",
        "palavras": "seguranca perigo choque mexer abrir desligar posso mexer corrente continua dc risco",
        "conteudo": (
            "Os painéis geram tensão contínua alta sempre que há luz, mesmo com o inversor desligado, e esse lado do sistema não "
            "pode ser simplesmente 'desligado' durante o dia. Não abra o inversor, caixas de junção nem conectores. Para "
            "desligar em emergência, siga o procedimento do integrador (normalmente desligar o disjuntor AC e a chave DC do "
            "inversor) e chame um técnico."
        ),
    },
    {
        "id": "degradacao_modulos",
        "titulo": "Vida útil e degradação dos módulos",
        "palavras": "vida util quantos anos dura duram duracao degradacao natural perde potencia por ano envelhecimento placas velhas garantia de performance 25 30 anos",
        "conteudo": (
            "Módulos solares duram décadas e perdem potência lentamente: tipicamente da ordem de 0,4 a 0,5% ao ano após o "
            "primeiro ano. Fabricantes dão garantia de desempenho de 25 a 30 anos; os percentuais exatos dos módulos desta "
            "usina estão no datasheet e no certificado de garantia. Uma queda rápida, de vários por cento em semanas, não é "
            "envelhecimento normal e merece investigação."
        ),
    },
    {
        "id": "garantia",
        "titulo": "Garantias",
        "palavras": "garantia fabricante quanto tempo inversor placa defeito troca acionar garantia",
        "conteudo": (
            "Há em geral garantias separadas: do produto (defeito de fabricação) e de desempenho dos módulos, do inversor, e da "
            "instalação feita pelo integrador. Os prazos variam por fabricante e contrato; consulte os certificados entregues na "
            "instalação. Para acionar, o integrador costuma abrir o chamado com o número de série e o histórico de falhas."
        ),
    },
    {
        "id": "ampliar_sistema",
        "titulo": "Ampliar a usina",
        "palavras": "ampliar aumentar sistema colocar mais placas instalar mais placas expandir adicionar modulos mais potencia mais paineis",
        "conteudo": (
            "Ampliar exige avaliar a capacidade do inversor (8 kW AC, já com 8,4 kWp de painéis), espaço no telhado e um novo "
            "pedido à concessionária para alterar a potência homologada. Em muitos casos é preciso outro inversor ou um maior. "
            "Converse com o integrador antes de comprar módulos."
        ),
    },
    # ---------------------------------------------------------- conta de luz
    {
        "id": "compensacao_creditos",
        "titulo": "Créditos de energia",
        "palavras": "credito creditos saldo sobra excedente injetada compensacao scee validade vence 60 meses transferir outra casa outra conta",
        "conteudo": (
            "A energia que a usina gera e a casa não usa na hora é injetada na rede e vira crédito em kWh no Sistema de "
            "Compensação de Energia Elétrica. Os créditos abatem o consumo dos meses seguintes e valem por 60 meses a partir "
            "da fatura em que foram gerados; depois disso expiram sem devolução em dinheiro. Podem ser usados em outras "
            "unidades consumidoras do mesmo titular (CPF/CNPJ) na área da mesma distribuidora, mediante cadastro."
        ),
    },
    {
        "id": "fio_b",
        "titulo": "Fio B e Lei 14.300",
        "palavras": "fio b lei 14300 marco legal taxacao do sol taxa cobranca uso da rede tusd por que nao zerou gd i gd ii direito adquirido 2045 escalonamento",
        "conteudo": (
            "O Fio B é a parte da tarifa que remunera a rede da distribuidora. Pela Lei 14.300/2022, sistemas com pedido de "
            "conexão feito a partir de 7 de janeiro de 2023 deixam de compensar uma fatia crescente do Fio B sobre a energia "
            "injetada e compensada: 15% em 2023, 30% em 2024, 45% em 2025, 60% em 2026, 75% em 2027 e 90% em 2028; a partir "
            "de 2029 vale a regra a ser definida pela ANEEL. Quem pediu conexão até 7 de janeiro de 2023 mantém a regra antiga "
            "até 2045. A energia consumida no mesmo momento em que é gerada não passa pela rede e não paga Fio B. "
            "Esta usina está na regra nova (GD II), ou seja, paga o Fio B escalonado; o cálculo de economia do SolCare já considera essa cobrança."
        ),
    },
    {
        "id": "custo_disponibilidade",
        "titulo": "Taxa mínima e por que a conta não zera",
        "palavras": "conta nao zerou taxa minima custo de disponibilidade 30 kwh 50 100 monofasico bifasico trifasico iluminacao publica impostos ainda pago valor minimo",
        "conteudo": (
            "Mesmo com geração alta, a conta não zera. Continuam sendo cobrados: o custo mínimo de disponibilidade "
            "(equivalente a 30 kWh na ligação monofásica, 50 kWh na bifásica e 100 kWh na trifásica), a contribuição de "
            "iluminação pública e impostos. Para quem está na regra nova da Lei 14.300, a distribuidora cobra o maior valor entre "
            "o custo de disponibilidade e a parcela do Fio B sobre a energia compensada, não os dois somados."
        ),
    },
    {
        "id": "ler_conta",
        "titulo": "Como ler a conta de luz",
        "palavras": "como ler conta fatura energia injetada consumida compensada saldo acumulado coelba neoenergia o que significa campo",
        "conteudo": (
            "Na fatura de quem gera energia aparecem, em geral: energia consumida da rede (kWh), energia injetada (o excedente que a "
            "usina mandou para a rede), energia compensada (créditos usados naquele mês) e saldo de créditos acumulados. A geração "
            "total do inversor é maior que a energia injetada, porque parte é consumida na hora pela casa. Os nomes exatos dos campos "
            "variam por distribuidora; na Bahia, a distribuidora é a Neoenergia Coelba."
        ),
    },
    {
        "id": "simultaneidade",
        "titulo": "Consumo durante o dia",
        "palavras": "simultaneidade usar de dia consumir durante o dia economizar mais horario ligar aparelhos maquina ar condicionado chuveiro bomba",
        "conteudo": (
            "A energia consumida no mesmo momento em que é gerada é a mais vantajosa: não passa pela rede, não vira crédito e não "
            "sofre a cobrança do Fio B. Por isso, sempre que possível, concentre o uso de aparelhos de maior consumo (bomba d'água, "
            "máquina de lavar, ferro, ar-condicionado) entre cerca de 9h e 15h, quando a geração é maior."
        ),
    },
    {
        "id": "vale_a_pena",
        "titulo": "Retorno do investimento",
        "palavras": "vale a pena compensa retorno payback investimento quanto tempo se paga economia anual",
        "conteudo": (
            "O retorno depende do valor investido, da tarifa, do consumo e da regra de compensação (Fio B). Em residências no Brasil, "
            "o retorno costuma ficar na faixa de alguns anos, enquanto os módulos duram mais de 25 anos. Para números desta usina, "
            "use a economia mensal calculada pelo SolCare e o valor que foi pago na instalação."
        ),
    },
    # ----------------------------------------------------------- monitoramento
    {
        "id": "como_funciona_solcare",
        "titulo": "Como o SolCare analisa a usina",
        "palavras": "como voce sabe voces sabem como funciona solcare bot de onde vem os dados confiavel precisao como calcula diagnostico score analisa monitora",
        "conteudo": (
            "O SolCare lê a geração e os alarmes do inversor pela plataforma da Growatt, cruza com dados de clima (radiação, "
            "nuvens e temperatura) e compara cada dia com o histórico da própria usina em dias de clima parecido. Com isso indica "
            "se a geração está dentro do esperado, se o clima explica uma queda e se há sinal de perda persistente. Os resultados são "
            "indícios estatísticos: não substituem a inspeção de um técnico."
        ),
    },
    {
        "id": "potencia_vs_energia",
        "titulo": "kW e kWh",
        "palavras": "kw kwh diferenca potencia energia o que e kwh kwp watt",
        "conteudo": (
            "kW mede potência, a 'velocidade' de geração num instante. kWh mede energia, o total acumulado ao longo do tempo: "
            "gerar 4 kW durante 2 horas dá 8 kWh. kWp (quilowatt-pico) é a potência nominal dos painéis em condição padrão de teste. "
            "A conta de luz é cobrada em kWh."
        ),
    },
    {
        "id": "curva_normal",
        "titulo": "Formato normal da curva de geração",
        "palavras": "curva normal formato sino grafico potencia ao longo do dia que horas comeca termina pico meio dia",
        "conteudo": (
            "Num dia limpo a curva de potência tem forma de sino: começa logo após o nascer do sol, sobe até o pico perto do meio-dia "
            "solar (por volta de 11h30 a 12h30 na Bahia) e desce até o fim da tarde. Serrilhados indicam nuvens passando; quedas "
            "bruscas até zero no meio do dia podem indicar desligamento do inversor (por exemplo, tensão da rede) e merecem atenção."
        ),
    },
]


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


_STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "um", "uma", "que", "por", "pra", "para", "com", "se", "eu", "minha", "meu", "minhas", "meus",
    "e", "ta", "esta", "isso", "isto", "qual", "quais", "como", "quando", "porque", "pq", "ser",
    "tem", "ter", "mais", "menos", "muito", "ao", "aos", "ou", "sobre", "usina", "solar", "sistema",
    "quanto", "quantos", "tempo", "devo", "posso", "pode", "perto", "casa", "bem",
}


def _terms(value: str) -> list[str]:
    return [t for t in _normalize(value).split() if t not in _STOPWORDS and len(t) > 1]


def _matches(term: str, words: set[str]) -> bool:
    if term in words:
        return True
    # Prefixo comum de 5+ letras cobre plural e flexões (limpar/limpeza, placa/placas).
    stem = term[:5]
    return len(term) >= 5 and any(word.startswith(stem) for word in words)


def search_knowledge(question: str, limit: int = 3) -> list[dict]:
    terms = _terms(question)
    if not terms:
        return []

    scored = []
    for entry in KNOWLEDGE_BASE:
        key_words = set(_normalize(entry["palavras"] + " " + entry["titulo"]).split())
        body_words = set(_normalize(entry["conteudo"]).split())
        score = 0.0
        for term in terms:
            if _matches(term, key_words):
                score += 2.0
            elif _matches(term, body_words):
                score += 0.5
        # Pares de palavras da pergunta presentes nas palavras-chave valem mais
        # (ex.: "fio b", "conta nao zerou", "falta de energia").
        key_text = " " + _normalize(entry["palavras"] + " " + entry["titulo"]) + " "
        question_words = _normalize(question).split()
        for first, second in zip(question_words, question_words[1:]):
            if f" {first} {second} " in key_text:
                score += 1.5
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {"titulo": entry["titulo"], "conteudo": entry["conteudo"], "relevancia": round(score, 1)}
        for score, entry in scored[:limit]
        if score >= 2.0
    ]


def get_knowledge(question: str) -> dict:
    question = str(question or "").strip()
    if not question:
        raise ValueError("Pergunta vazia.")
    results = search_knowledge(question)
    if not results:
        return {
            "found": False,
            "message": "Nada na base de conhecimento cobre essa pergunta.",
        }
    return {"found": True, "verbetes": results}
