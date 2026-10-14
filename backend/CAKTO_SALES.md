# Compra independente: AFILIADOS QG BALTIGO

Página: `/afiliados.html` (também `/afiliados` na VPS). Oferta conferida: `https://pay.cakto.com.br/3bd8an5_676851`, código da oferta `3bd8an5`, **AFILIADOS QG BALTIGO**, pagamento único de **R$ 7,49**. O sufixo do link não é concatenado ao `offerId`. O serviço é adesão à comunidade de afiliados. Não há desconto em parcelas, aprovação de crédito, bloqueio de cadastro PF ou transferência de dados do formulário de empréstimo para esta compra.

O código está preparado; GitHub Pages e Sites servem apenas a página estática. Sem a API, o formulário permanece indisponível e oferece o link da oferta oficial. Não existem QR Codes ou confirmações fictícias na versão publicada.

## Ativar na VPS

1. Instale as dependências de `backend/requirements.txt`. Use o serviço HTTPS e os exemplos de systemd/Caddy descritos em `README.md`. O banco e a chave devem ficar fora da pasta pública.
2. Configure `/etc/emprestimofacil/backend.env` (arquivo privado, modo 0600):

```dotenv
PUBLIC_ORIGIN=https://seu-dominio.com.br
APPLICATIONS_DB=/var/lib/emprestimofacil/applications.sqlite
DATA_ENCRYPTION_KEY=CHAVE_BASE64_DE_32_BYTES
CAKTO_CLIENT_ID=CONFIGURE_NO_SERVIDOR
CAKTO_CLIENT_SECRET=CONFIGURE_NO_SERVIDOR
CAKTO_WEBHOOK_SECRET=SEGREDO_DO_WEBHOOK_CAKTO
CAKTO_SALES_OFFER_ID=3bd8an5
CAKTO_SALES_ENABLED=true
SALES_RETENTION_DAYS=30
BALTIGO_SUPPORT_WHATSAPP=
```

Para gerar a chave localmente: `.venv/bin/python -c 'import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())'`. Guarde a chave com o banco; mudá-la impede ler os registros existentes. Nunca copie o arquivo real de ambiente para o Git. `BALTIGO_SUPPORT_WHATSAPP` é opcional: somente números com código do país e DDD. O botão é exibido após confirmação real e inclui apenas o protocolo da compra.

As variáveis `APPLICATIONS_ENABLED`, `CAKTO_PAYMENTS_ENABLED` e `CAKTO_LOAN_COLLECTION_APPROVED` são independentes e podem continuar `false`. Nenhuma credencial recebida em conversa foi gravada no código. Não é necessário alterar o JavaScript com segredos nem mudar os endpoints do cadastro PF.

3. Na Cakto, configure um webhook **V1** para `https://seu-dominio.com.br/api/sales/cakto/webhook`, com eventos de Pix gerado, pagamento aprovado, reembolso e chargeback. Coloque o segredo desse webhook em `CAKTO_WEBHOOK_SECRET`. Este consumidor espera `data` como objeto; não use webhook V2 com array. As chaves precisam dos escopos `read`, `write`, `offers`, `orders`, `payments`.
4. Com o ambiente privado carregado, execute:

```bash
.venv/bin/python -m backend.sales_cli check
python3 scripts/build-reference.py
```

O comando `check` só autentica e consulta a oferta; **não gera cobrança**. Exige oferta ativa, única, BRL, nome e preço iguais aos apresentados. Se o produto ou preço mudar, ajuste a implementação e a tela juntos antes de ativar. Inicie/reinicie o serviço e abra `/afiliados.html`. Nenhum build de Worker ou servidor Node é necessário: a mesma API FastAPI hospeda esta página e os endpoints `/api/sales/`.

## Geração, confirmação e recuperação

A sessão usa cookie Secure/HttpOnly/SameSite=Strict com validade de 24 horas, sem armazenamento de CPF em URL ou localStorage. A compra exige confirmação explícita do produto e valor. Os dados e o payload enviados à Cakto são armazenados cifrados com AES-256-GCM antes da requisição. O preço e a oferta vêm do servidor; campos arbitrários de valor são rejeitados.

O Pix tem validade solicitada de 900 segundos; a tela usa a expiração efetivamente devolvida. QR PNG é gerado localmente a partir do BR Code. O botão **Verificar pagamento** consulta a Cakto. `paid` só é aceito com valor, produto, oferta, CPF, e-mail, referência e data de pagamento correspondentes. Status parcial/processando/expirado não é confirmação. Webhooks verificam HMAC-SHA256 de `timestamp.corpo-cru`, rejeitam assinaturas antigas, deduplicam eventos e consultam o pedido novamente.

Repetir a tentativa reaproveita a compra. Se o resultado da geração for incerto, a sessão não emite outro Pix, nem mesmo com outra chave. A retomada da página recupera a compra pelo cookie. Se o ID do pedido não chegou, um evento autenticado com referência correspondente pode recuperá-lo; alternativamente, o operador identifica o pedido existente na Cakto e usa:

```bash
.venv/bin/python -m backend.sales_cli bind --purchase UUID_DA_COMPRA --order UUID_DO_PEDIDO
.venv/bin/python -m backend.sales_cli reconcile --purchase UUID_DA_COMPRA
```

Esses comandos só consultam pedidos existentes e persistem a conciliação; não criam cobranças, reembolsos ou acessos ao Telegram. A entrega do serviço pertence ao vendedor/Cakto; o site não promete liberar uma comunidade automaticamente. Não teste com CPF/documentos reais de terceiros nem gere pagamentos de produção com as fixtures.

Retenção padrão: 30 dias, configurável de 1 a 365. A manutenção existente inclui compras, eventos e sessões; backups validam os dados cifrados e restaurar invalida as sessões. Defina e comunique os prazos adequados à operação. A API usa os mesmos limites por conta Cakto, além de limites por IP e sessão. Execute com um worker conforme o serviço existente; múltiplos processos exigem coordenação compartilhada.

## Testes

`python -m unittest discover -s tests -p '*_test.py' -v` usa provedores sintéticos. Os testes de navegador verificam indisponibilidade, máscaras, geração, QR, cópia, expiração, checagem e retorno confirmado. Nenhum teste cria cobrança real. Homologação real depende das credenciais/ambiente disponibilizados pela Cakto.

Referências: [Pix](https://docs.cakto.com.br/api-reference/payments/create-pix), [oferta](https://docs.cakto.com.br/api-reference/offers/retrieve), [webhooks](https://docs.cakto.com.br/conceitos/webhooks).
