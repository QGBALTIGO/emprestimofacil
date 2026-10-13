# Empréstimo Fácil

Site responsivo com seleção de valor e parcelamento semanal, integração para cotações, conteúdo institucional, perguntas frequentes e logos PNG transparentes. Os textos institucionais e a FAQ seguem o conteúdo fornecido para a marca Empréstimo Fácil. O formulário usa o modelo de apresentação da referência: valor sobre a barra, parcelas 3x/6x/9x e 12x em destaque, custos detalhados e botão de solicitação. O cálculo inicial usa a taxa mínima informada, 15,80% ao mês, e prestações semanais fixas. A taxa semanal equivalente é `(1 + taxa_mensal)^(7 / 30) - 1`; o mês de 30 dias é a convenção explícita deste cálculo. Ele não inclui IOF nem tarifas e não produz um CET fictício. A interface identifica esse preço como inicial (a partir de), antes dos encargos. Uma cotação oficial semanal sempre tem prioridade sobre o cálculo inicial.

## Publicar e editar

A raiz contém `index.html` e `assets/`, prontos para hospedagem estática. Depois de editar os arquivos em `src/`, execute `python3 scripts/build-reference.py` para reconstruir a página. O gerador usa somente a biblioteca padrão do Python.

## Dados comerciais necessários

Configure `src/business-config.json` com o WhatsApp oficial em formato internacional ou a URL HTTPS da instituição que recebe solicitações. Para exibir valores de parcelas, informe uma tabela oficial em `quotes` ou um endpoint HTTPS em `quotesUrl` que retorne `{ "quotes": [...] }`.

Cada cotação requer: `amount_requested`, `installments_requested`, `installment_frequency` igual a `weekly`, `installment_amount`, `total_amount`, `monthly_rate` e `annual_cet`. A periodicidade das parcelas é semanal; `monthly_rate` continua representando a taxa de juros mensal. O endpoint recebe `installment_frequency=weekly`; respostas mensais ou sem periodicidade são rejeitadas. Taxa e CET são decimais (por exemplo, 0.02 significa 2%). Pode conter `expires_at` em ISO 8601 e os encargos `iof_amount` e `fees_amount`, em reais. Os encargos precisam ser não negativos e não podem superar a diferença entre total a pagar e valor solicitado. Se não forem fornecidos, o formulário não os inventa. A taxa anual equivalente de juros é calculada a partir da taxa mensal; o CET usa exclusivamente o valor recebido na cotação. Parcela, total e prazo precisam ser coerentes. Não coloque segredos ou credenciais no JSON público.

A referência pública usa ofertas pré-calculadas recebidas do servidor (`/quotes`) e não uma fórmula de parcela no navegador. As ofertas da referência são mensais; não devem ser relabeladas como semanais. Nesta edição, a consulta pública à referência retornou HTTP 403. O cálculo inicial foi ativado a pedido do responsável para parcelas semanais normais. Para substituir esse cálculo por ofertas definitivas, forneça a tabela semanal ou um endpoint autorizado. A fórmula de prestações fixas segue a metodologia matemática descrita pelo Banco Central, adaptando a taxa ao intervalo semanal; isso não significa que o Banco Central ou a Simplic tenham homologado estes preços semanais. Fonte da fórmula: https://www3.bcb.gov.br/CALCIDADAO/publico/exibirMetodologiaFinanciamentoPrestacoesFixas.do?method=exibirMetodologiaFinanciamentoPrestacoesFixas

**Estado atual:** a FAQ informa a faixa de juros recebida no conteúdo comercial, mas ainda não há tabela de cotações individuais, endpoint de crédito ou canal para receber propostas. A página mostra o valor inicial antes dos encargos e não confirma aprovação ou envio de solicitação. A ajuda não oferece links de contato ou portal. O código não concede crédito, não faz análise bancária e não cria contratos.

## Testes

`npm ci` instala dependências somente de desenvolvimento. `npm run test:unit` verifica seleção, limites, cotações, expiração, validação e URLs de atendimento. `npm test` executa a suíte Playwright. No GitHub Actions, Chrome, Firefox e WebKit cobrem 11 tamanhos de tela cada, incluindo celular, tablet e PC. As capturas e o relatório ficam nos artefatos da execução. Dispositivos são emulados; isso não é teste em todos os aparelhos físicos.
