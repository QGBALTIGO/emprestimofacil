# Cadastro e API para VPS

O site inclui cinco etapas: Cadastro, Endereço, Renda, Conta bancária e Revisão. No GitHub Pages e no Sites, `applicationsApiUrl` permanece `null`: o formulário funciona, mas o envio fica indisponível e nenhum dado é gravado. Nenhum CPF, senha ou dado bancário é colocado em localStorage, sessionStorage, URL ou cookies. O progresso existe apenas na página aberta; ao sair do cadastro, a senha é apagada.

## Executar na VPS

Instale Python 3.12 e Caddy. Na cópia do repositório:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Configure no ambiente do serviço, fora do repositório:

```text
PUBLIC_ORIGIN=https://emprestimo.seudominio.com.br
APPLICATIONS_DB=/var/lib/emprestimofacil/applications.sqlite
DATA_ENCRYPTION_KEY=<32 bytes aleatórios, codificados em base64>
APPLICATIONS_ENABLED=false
```

Gere a chave uma única vez com `python3 -c 'import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())'`. Guarde-a no ambiente do serviço e em um backup privado: perder a chave impede recuperar os dados, e trocar a chave exige migrar os registros existentes. A pasta do banco deve ser exclusiva do serviço; o programa aplica permissões 0700 à pasta e 0600 ao arquivo.

Execute com um worker, sem logs de acesso e acessível apenas ao proxy local:

```bash
.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1 --no-access-log
```

Use `backend/Caddyfile.example` com o domínio definitivo para ativar HTTPS. O backend serve apenas `/`, `/assets/` e os endpoints definidos; não expõe código, banco ou diretórios internos. O navegador e a API devem usar a mesma origem: não há CORS nem envio a domínios de terceiros.

Antes de habilitar o recebimento, finalize os textos de termos, privacidade e autorização em `src/registration-runtime.js`, identificando a empresa/controlador e instituição responsável, canal de direitos sobre os dados, finalidades, destinatários, bases aplicáveis e retenção. Não foram atribuídos os códigos LH1010/LH1003 à nova marca nem implementadas consultas a bases externas. Os textos iniciais descrevem o fluxo disponível e não constituem homologação jurídica ou autorização de acesso a sistemas de crédito.

Depois, configure `applicationsApiUrl` como `/api/applications` em `src/business-config.json`, reconstrua com `python3 scripts/build-reference.py` e habilite `APPLICATIONS_ENABLED=true` no serviço. Faça essas alterações apenas na implantação da VPS; o site estático deve continuar sem endpoint até a migração. O endpoint também permanece bloqueado se `PUBLIC_ORIGIN` não estiver configurado. Reinicie o serviço ao alterar o ambiente.

## Contrato de envio

`POST /api/applications`, JSON, cabeçalho `Origin` igual a `PUBLIC_ORIGIN`, e `Idempotency-Key` com UUID. O corpo usa `schema_version: 1`, `applicant`, `password`, `address`, `income`, `bank_account`, `loan` e `consents`. O exemplo sintético completo está em `tests/backend_test.py`.

O servidor valida novamente CPF, idade mínima de 18 anos, e-mail, telefone, renda mínima de R$ 1.200,00, endereço, banco, agência/conta, autorizações e empréstimo de R$ 500 a R$ 3.500 em 3/6/9/12 parcelas **semanais**. A validação do CPF é matemática; a titularidade da conta, renda e elegibilidade ainda exigem análise externa.

Após gravar e confirmar a transação, responde `201`:

```json
{"application_id":"UUID do protocolo","status":"received"}
```

Somente essa resposta válida libera a tela de protocolo. Falha, timeout, JSON inesperado ou serviço ausente não exibem sucesso. Repetir o mesmo UUID com o mesmo conteúdo devolve o protocolo existente; mudar o conteúdo com o UUID anterior retorna 409. CPF e e-mail duplicados também retornam uma mensagem genérica com 409. Erros de validação retornam 422 sem reproduzir os dados enviados. O corpo tem limite de 16 KiB; há limite de 10 tentativas por IP em 10 minutos por processo, e recebimentos indisponíveis retornam 503. Para múltiplos workers ou maior volume, substitua SQLite e o limite em memória por armazenamento compartilhado antes de escalar.

As senhas recebem scrypt com salt aleatório; os dados pessoais, renda, conta e autorizações são criptografados com AES-256-GCM, nonce aleatório e vínculo ao protocolo. Os índices de CPF/e-mail e o resumo do pedido usam HMAC. O banco não guarda a senha original, confirmações de senha/e-mail nem PII em texto puro. O horário de recebimento e a versão dos consentimentos são registrados no servidor. Não há painel público para consultar esses dados nem emissão automática de aprovação ou contrato.

`GET /api/health` informa a disponibilidade do processo e se o recebimento foi habilitado. Faça backup do banco e da chave separadamente e aplique à operação uma política definida de acesso e retenção. Esta API recebe solicitações; login, envio de e-mail, verificação de titularidade, análise de crédito e assinatura contratual não estão integrados.

## Verificação

```bash
npm ci --ignore-scripts
npm run build
npm run test:unit
python3 -m unittest discover -s tests -p backend_test.py -v
```

O GitHub Actions executa essas verificações e os testes de navegador, incluindo todas as etapas, máscaras, autorizações, falta de serviço, falha de envio, repetição idempotente e confirmação de protocolo. Todos os dados de teste são sintéticos.
