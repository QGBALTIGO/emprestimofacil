# Empréstimo Fácil

Site responsivo com seleção de valor e prazo, cotação oficial, encaminhamento para atendimento, perguntas frequentes e logos PNG transparentes. Não contém parcelas geradas com juros inventados ou informações institucionais de terceiros.

## Publicar e editar

A raiz contém `index.html` e `assets/`, prontos para hospedagem estática. Depois de editar os arquivos em `src/`, execute `python3 scripts/build-reference.py` para reconstruir a página. O gerador usa somente a biblioteca padrão do Python.

## Dados comerciais necessários

Configure `src/business-config.json` com o WhatsApp oficial em formato internacional ou a URL HTTPS da instituição que recebe solicitações. Para exibir valores de parcelas, informe uma tabela oficial em `quotes` ou um endpoint HTTPS em `quotesUrl` que retorne `{ "quotes": [...] }`.

Cada cotação requer: `amount_requested`, `installments_requested`, `installment_amount`, `total_amount`, `monthly_rate` e `annual_cet`. Taxa e CET são decimais (por exemplo, 0.02 significa 2%). Pode conter `expires_at` em ISO 8601. Parcela, total e prazo precisam ser coerentes. Não coloque segredos ou credenciais no JSON público.

**Estado atual:** o usuário ainda não forneceu taxas comerciais nem recebedor de propostas. A página mostra que as condições devem ser consultadas e não confirma aprovação ou envio de solicitação. O encaminhamento fica indisponível até que o canal real seja informado. O código não concede crédito, não faz análise bancária e não cria contratos.

## Testes

`npm ci` instala dependências somente de desenvolvimento. `npm run test:unit` verifica seleção, limites, cotações, expiração, validação e URLs de atendimento. `npm test` executa a suíte Playwright. No GitHub Actions, Chrome, Firefox e WebKit cobrem 10 tamanhos de tela cada, incluindo celular, tablet e PC. As capturas e o relatório ficam nos artefatos da execução. Dispositivos são emulados; isso não é teste em todos os aparelhos físicos.
