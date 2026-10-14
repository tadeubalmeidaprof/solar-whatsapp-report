# Explicação quantitativa da geração Growatt

A ferramenta `explicar_geracao_diaria` explica a energia observada no intervalo analisado. A configuração confirmada em `pv_site.json` é 8,4 kWp DC, inversor de 8 kW AC e módulos DAH DHJ-66Y18/DG de 700 W. A ficha DAH da família informa coeficiente de Pmax de -0,24%/°C. Inclinação e orientação não foram informadas.

## Dados e alinhamento temporal

- Integração trapezoidal da curva real em horários observados, sem preencher intervalos superiores a 15 minutos.
- Radiação Open-Meteo no timestamp HH:00 alinhada ao intervalo anterior HH-1:00..HH:00; posição solar calculada no centro do intervalo.
- Dados horários de GHI, DNI, DHI, temperatura ambiente e vento em m/s, por consultas de intervalo ao arquivo e ao endpoint recente.
- No dia atual, usa somente horas encerradas há pelo menos 20 minutos. Todo histórico é comparado no mesmo intervalo de relógio. O padrão continua 07:00–17:00; a resposta não apresenta a energia dessa janela como total de um dia inteiro.
- Cobertura mínima de 90% do intervalo, com pelo menos 85% por hora aproveitada. Gaps longos são excluídos e aparecem na cobertura. Expectativa e referência usam as mesmas durações efetivamente observadas.

## Modelos

Sem geometria confirmada: regressão robusta Huber com regularização ridge, saída normalizada pela potência DC e termos de GHI, GHI², interação GHI × temperatura ambiente e geometria solar. Não é um performance ratio físico, não trata GHI como POA e não apresenta temperatura ambiente como temperatura do módulo. A regressão absorve características constantes da instalação; não identifica perdas isoladas de sujeira, calor ou sombra.

Com `PV_SURFACE_TILT_DEG` e `PV_SURFACE_AZIMUTH_DEG` configurados juntos: posição solar NREL/pvlib, transposição Perez para irradiância POA, temperatura Faiman, potência DC PVWatts com o coeficiente térmico do módulo, eficiência genérica PVWatts do inversor e limite AC de 8 kW. Ganho robusto calibrado à usina. Os parâmetros térmicos e a curva do inversor são aproximados; o modelo não equivale a um modelo de diodo único com parâmetros elétricos completos.

## Validação e limites

O dia explicado nunca é usado no ajuste de sua expectativa. A validação avança no tempo: pelo menos sete dias anteriores de ajuste e cinco posteriores para medição de erro. O modelo precisa superar uma referência de potência mediana por hora nos mesmos dados de validação. RMSE normalizado acima de 30% também bloqueia a estimativa; esse teto é um critério operacional, não uma promessa de precisão.

Retorna MAE, RMSE, viés, comparação com a referência e previsões de validação. A faixa usa percentis 10 e 90 dos resíduos históricos de validação. É uma faixa empírica, sem garantia de cobertura probabilística futura. Não é intervalo de confiança causal. Clima fora do domínio de radiação/temperatura usado no ajuste pode bloquear o resultado.

Se a geração ficar abaixo da faixa já condicionada ao clima, o bot recomenda investigar o desvio sem declarar defeito. Se a redução para a referência acompanhar menor radiação e a geração estiver dentro da faixa, comunica compatibilidade climática sem provar causalidade exclusiva. Não aplica novo ganho bifacial, sombreamento ou perdas térmicas porcentuais sem evidência independente.

## Operação

`pv_site.json` contém parâmetros confirmados; `PV_DC_CAPACITY_KWP` e `PV_AC_CAPACITY_KW` permitem override. A localização continua nas variáveis existentes `STATION_LATITUDE`/`STATION_LONGITUDE`. Não há alteração de schema, gravação de dados pela ferramenta ou envio automático de alertas. A resposta local não exige uma rodada da Groq para perguntas comuns.

O workflow `generation-model-smoke.yml` valida o fluxo real somente de leitura, sem enviar WhatsApp ou imprimir localização, identificadores ou curvas nos logs. Um smoke aprovado com `warming_up`/`model_not_validated` confirma execução do fluxo, não precisão do modelo. O campo `available` precisa ser avaliado separadamente.

Referências: [ficha DAH](https://static.souenergy.com.br/catalog/product/file/Datasheet_-_DAH_700_Bifacial_-_DHJ-66Y18DG.pdf), [PVWatts/pvlib](https://pvlib-python.readthedocs.io/en/v0.13.1/reference/generated/pvlib.pvsystem.pvwatts_dc.html), [Faiman](https://pvlib-python.readthedocs.io/en/v0.13.1/reference/generated/pvlib.temperature.faiman.html), [inversor PVWatts](https://pvlib-python.readthedocs.io/en/v0.13.1/reference/generated/pvlib.inverter.pvwatts.html).
