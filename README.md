# Empréstimo Fácil

Site responsivo com seleção de valor e parcelamento semanal, integração para cotações, conteúdo institucional, perguntas frequentes e logos PNG transparentes. Os textos institucionais e a FAQ seguem o conteúdo fornecido para a marca Empréstimo Fácil. O código não calcula parcelas a partir de taxas inventadas.

## Publicar e editar

A raiz contém `index.html` e `assets/`, prontos para hospedagem estática. Depois de editar os arquivos em `src/`, execute `python3 scripts/build-reference.py` para reconstruir a página. O gerador usa somente a biblioteca padrão do Python.

## Dados comerciais necessários

Configure `src/business-config.json` com o WhatsApp oficial em formato internacional ou a URL HTTPS da instituição que recebe solicitações. Para exibir valores de parcelas, informe uma tabela oficial em `quotes` ou um endpoint HTTPS em `quotesUrl` que retorne `{ "quotes": [...] }`.

Cada cotação requer: `amount_requested`, `installments_requested`, `installment_frequency` igual a `weekly`, `installment_amount`, `total_amount`, `monthly_rate` e `annual_cet`. A periodicidade das parcelas é semanal; `monthly_rate` continua representando a taxa de juros mensal. O endpoint recebe `installment_frequency=weekly`; respostas mensais ou sem periodicidade são rejeitadas. Taxa e CET são decimais (por exemplo, 0.02 significa 2%). Pode conter `expires_at` em ISO 8601. Parcela, total e prazo precisam ser coerentes. Não coloque segredos ou credenciais no JSON público.

**Estado atual:** a FAQ informa a faixa de juros recebida no conteúdo comercial, mas ainda não há tabela de cotações individuais, endpoint de crédito ou canal para receber propostas. A página mostra que as condições devem ser consultadas e não confirma aprovação ou envio de solicitação. A ajuda não oferece links de contato ou portal. O código não concede crédito, não faz análise bancária e não cria contratos.

## Testes

`npm ci` instala dependências somente de desenvolvimento. `npm run test:unit` verifica seleção, limites, cotações, expiração, validação e URLs de atendimento. `npm test` executa a suíte Playwright. No GitHub Actions, Chrome, Firefox e WebKit cobrem 11 tamanhos de tela cada, incluindo celular, tablet e PC. As capturas e o relatório ficam nos artefatos da execução. Dispositivos são emulados; isso não é teste em todos os aparelhos físicos.
