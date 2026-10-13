# Pagamento de parcelas pela Cakto

O módulo recebe parcelas de empréstimos **já contratados, assinados e desembolsados**. Não cobra para analisar, cadastrar ou liberar crédito. Não transforma o pré-cadastro em contrato e não executa desembolso, decisão de crédito, assinatura, saque ou reembolso automaticamente.

## Ativação

1. Confirme com a Cakto que sua conta e operação podem receber pagamentos de parcelas de empréstimos. Os termos públicos tratam de produtos/serviços e não constituem aprovação específica deste uso. Não disfarce a operação como um curso ou produto diferente. A conta deve estar concluída, ativa, principal e fora de encerramento; a API pública não permite consultar previamente estes quatro estados.
2. Na Cakto, crie uma chave dedicada com `read`, `write`, `offers`, `orders` e `payments`. Não são necessários escopos de saques ou cartão. Guarde `CAKTO_CLIENT_ID` e `CAKTO_CLIENT_SECRET` somente no arquivo privado de ambiente da VPS, fora do Git e do JSON do site. Tokens OAuth ficam em memória e são reutilizados até perto da expiração. O teste de autenticação não demonstra autorização operacional para receber empréstimos.
3. Configure o webhook HTTPS da VPS em `/api/installments/cakto/webhook`. Copie o segredo gerado pela Cakto para `CAKTO_WEBHOOK_SECRET`. Cadastre eventos de Pix gerado, pagamento aprovado, reembolso e chargeback. A rotina exige `X-Cakto-Timestamp` e `X-Cakto-Signature` (HMAC-SHA256), com tolerância de cinco minutos, e não depende do segredo recebido no corpo.
4. Configure uma oferta `active`, `unique`, em `BRL`, com o preço exato de cada valor de parcela. A API cobra a oferta; não recebe um valor arbitrário do formulário. Configure o produto para a operação verdadeira, sem entregas de conteúdo ou mensagens de venda inadequadas. Confira a expiração máxima do Pix no produto. Não são criadas ofertas ou produtos automaticamente por estes arquivos.
5. Importe somente contratos reais já assinados, com comprovante de desembolso e autorização para tratar os dados necessários ao pagamento. O operador deve conferir os documentos; o programa valida os campos e a sequência, sem verificar assinatura digital ou transferência bancária por conta própria.
6. Após homologar em ambiente fornecido pela Cakto, configure `CAKTO_PAYMENTS_ENABLED=true` e `CAKTO_LOAN_COLLECTION_APPROVED=true`, habilite o serviço na mesma origem HTTPS e reconstrua com `python3 -m backend.configure_site --vps` seguido de `python3 scripts/build-reference.py`. O site estático mantém `paymentApiUrl: null`.

Não há ambiente de sandbox público configurado por este projeto. A Cakto documenta acesso a staging mediante solicitação. Nunca use as fixtures sintéticas para emitir cobranças reais. Nenhuma credencial recebida em conversa é incorporada aos arquivos do projeto.

## Registrar contratos e emitir links

O recebimento de proposta PF e o registro de dívida são independentes. Não existe endpoint público para criar contratos. O operador usa um JSON privado, até 32 KiB, no terminal da VPS:

```bash
.venv/bin/python -m backend.billing_cli connection
.venv/bin/python -m backend.billing_cli import --file /CAMINHO_PRIVADO/contrato.json
```

O modelo está em `backend/billing_models.py`; um exemplo **sintético** está em `tests/billing_test.py`. Exige:

- `id` UUID e referência interna;
- cliente com nome, CPF válido, nascimento, e-mail e celular;
- principal em centavos, entre R$ 500 e R$ 3.500;
- assinatura realizada, data com fuso e SHA-256 do documento assinado;
- desembolso realizado, data com fuso e referência do comprovante;
- autorização de dados de pagamento;
- 3, 6, 9 ou 12 parcelas semanais, numeradas em sequência, com vencimentos após o desembolso, total coerente, valor em centavos, `offer_id` e `product_id`.

O importador não calcula juros, CET, multas ou antecipação. Os valores devem vir do contrato correto, revisado na operação. Contratos são imutáveis; não altere uma dívida silenciosamente. O programa não abre arquivos indicados pela referência de desembolso nem atesta documentos só porque um campo é `true`.

No painel `/admin`, entre com senha e TOTP, abra **Parcelas e pagamentos** e gere o link da parcela. O link só é emitido quando a integração está habilitada. Gerar outro link invalida os acessos anteriores daquela parcela. O código de acesso fica no fragmento `#`, é removido da URL pelo navegador antes da requisição e é trocado por cookie opaco Secure/HttpOnly/SameSite=Strict. O link expira em sete dias e a sessão em 30 minutos. Compartilhe somente com o titular, por um canal da operação. Este projeto não envia mensagens automaticamente.

O cliente abre `/pagar#CODIGO_PRIVADO`, confere a parcela e solicita o Pix. Nome, e-mail, celular e CPF vêm do contrato no servidor. O dispositivo/sessão recebe identificador opaco; não se coletam impressões invasivas do aparelho. O preço da oferta, produto, moeda e valor contratual são conferidos antes de chamar a Cakto. O payload e a chave de idempotência são persistidos criptografados antes da requisição.

## Confirmação e conciliação

O Pix mostra QR Code gerado localmente a partir do BR Code, copia e cola e a expiração retornada pela Cakto. O tempo solicitado é de 900 segundos, sujeito ao limite do produto/adquirente. Nenhum QR é gerado por serviço externo. As credenciais e os dados completos do contrato não chegam ao navegador.

O botão **Verificar pagamento** consulta a Cakto no servidor. A baixa exige `paid` com data de pagamento e correspondência de pedido, método Pix, produto, referência `sck`, CPF, e-mail e valor em reais. `authorized`, `processing` e `partially_paid` não são pagamento integral confirmado. O webhook autentica os bytes recebidos, deduplica o evento e consulta novamente o pedido. Eventos desconhecidos são ignorados. Reembolsos e chargebacks alteram o estado com base no pedido atual; eventos antigos não sobrescrevem o estado atual da Cakto.

Não há nova cobrança se a geração tiver resultado incerto. O ID do pedido, quando recebido, é salvo antes das demais conferências. O operador deve conciliar uma tentativa sem resposta antes de permitir outra operação. A Cakto mantém idempotência por 24 horas; o projeto não depende de reemitir cobranças indefinidamente com uma chave antiga.

```bash
.venv/bin/python -m backend.billing_cli reconcile --installment UUID_DA_PARCELA
.venv/bin/python -m backend.billing_cli bind --installment UUID_DA_PARCELA --order UUID_DO_PEDIDO_EXISTENTE
```

`bind` vincula apenas pedido já existente, conferindo a referência, o CPF, produto e valor. Não cria cobrança. O Pix expirado não é assumido como pagamento nem descartado para criar outro imediatamente. Uma nova tentativa depende de confirmação do pedido anterior como cancelado/recusado/bloqueado, ou de intervenção operacional apropriada. Cobrança parcial, juros por atraso, renegociação, cancelamento de contrato, estorno e reembolso automático não estão implementados.

## Proteção, retenção e limites

Contratos e payloads de pagamento usam AES-256-GCM, vinculados aos respectivos IDs. Os nomes dos clientes, CPF, contatos e dados do pedido não ficam em texto puro no SQLite. Links e cookies usam índices HMAC. A página não usa localStorage/sessionStorage nem põe CPF em URLs. A equipe acessa o módulo com o mesmo controle MFA e auditoria do cadastro PF.

`PAYMENT_RETENTION_DAYS` é 365 por padrão (180–3650). Contratos com parcelas pendentes não são apagados automaticamente só pela idade. Depois da quitação integral, o prazo é contado pelo menos a partir da última baixa. Sessões/links expirados e deduplicação antiga são removidos pela manutenção diária. Defina com a operação os prazos aplicáveis e informe-os na política. Backups preservam dados pelo prazo adicional configurado; restaurar invalida links e sessões de pagamento e mantém os pedidos existentes para evitar cobrança duplicada.

O servidor usa um worker e limites por IP, parcela e conta do provedor. Em um único processo, consultas são limitadas a 50 chamadas/minuto para reservar margem na Cakto; uma consulta já confirmada pode ser reaproveitada por até 30 segundos. A tela consulta periodicamente quando visível e aguardando pagamento. Antes de escalar, implemente processamento de eventos, filas, coordenação compartilhada e monitoramento apropriados ao volume contratado.

Os testes usam provedores substituídos e contratos sintéticos. A autenticação real deve ser testada separadamente, com consultas e sem geração de cobrança. Homologue os eventos, Pix, expiração, baixa, reversões e limites com a Cakto antes de habilitar pagamentos de clientes.

Referências oficiais: [autenticação](https://docs.cakto.com.br/authentication), [criar Pix](https://docs.cakto.com.br/api-reference/payments/create-pix), [consultar pedido](https://docs.cakto.com.br/api-reference/orders/retrieve), [webhooks](https://docs.cakto.com.br/conceitos/webhooks), [idempotência](https://docs.cakto.com.br/conceitos/idempotencia) e [termos da Cakto](https://www.cakto.com.br/termos-de-uso).
