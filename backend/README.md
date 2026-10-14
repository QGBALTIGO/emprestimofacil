# Cadastro e API para VPS

O site inclui cinco etapas: Cadastro, Endereço, Renda, Conta bancária e Revisão. No GitHub Pages e no Sites, `applicationsApiUrl` permanece `null`: o formulário funciona, mas o envio fica indisponível e nenhum dado é gravado. A consulta automática usa ViaCEP e OpenCEP como alternativa. Envia apenas o CEP para preencher rua, bairro, cidade e estado; uma falha não impede o preenchimento manual. Há cache na memória da página, cancelamento de consultas antigas, tentativas limitadas a 5 segundos por fonte e botão para tentar novamente. Nenhum CPF, senha ou dado bancário é colocado em localStorage, sessionStorage, URL ou cookies. O progresso existe apenas na página aberta; ao sair do cadastro, a senha é apagada.

## Executar na VPS

Instale Python 3.12, FFmpeg/ffprobe e Caddy. Na cópia do repositório:

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

Depois, execute `python3 -m backend.configure_site --vps` na cópia implantada na VPS, reconstrua com `python3 scripts/build-reference.py` e habilite `APPLICATIONS_ENABLED=true` no serviço. O comando configura os quatro caminhos públicos sem gravar credenciais; `--static` volta os quatro para `null`. Os endpoints são `/api/applications`, `/api/security`, `/api/pf/applications` e `/api/installments`. Faça essas alterações apenas na implantação da VPS; o site estático deve continuar sem endpoint até a migração. O endpoint também permanece bloqueado se `PUBLIC_ORIGIN` não estiver configurado. Reinicie o serviço ao alterar o ambiente.

## Contrato de envio

`POST /api/applications`, JSON, cabeçalho `Origin` igual a `PUBLIC_ORIGIN`, e `Idempotency-Key` com UUID. O corpo usa `schema_version: 1`, `applicant`, `password`, `address`, `income`, `disbursement_method`, `bank_account`, `pix`, `loan` e `consents`. Para conta bancária, use `disbursement_method: "bank"`, `bank_account` preenchido e `pix: null`. Para Pix, use `disbursement_method: "pix"`, `bank_account: null` e `pix: {"key_type":"cpf|email|phone|random","key":"chave normalizada"}`. CPF usa 11 dígitos e deve coincidir com o titular; e-mail tem até 77 caracteres em minúsculas, celular usa E.164 (`+55...`) e a chave aleatória usa o formato UUID. A validação é de formato, sem consulta ao DICT ou execução de transferência. A API mantém compatibilidade com pedidos anteriores: sem `disbursement_method`, exige conta bancária. O exemplo sintético completo está em `tests/backend_test.py`.

O servidor valida novamente CPF, idade mínima de 18 anos, e-mail, telefone, renda mínima de R$ 1.200,00, endereço, banco, agência/conta, autorizações e empréstimo de R$ 500 a R$ 3.500 em 3/6/9/12 parcelas **semanais**. A validação do CPF é matemática; a titularidade da conta, renda e elegibilidade ainda exigem análise externa.

Após gravar e confirmar a transação, responde `201`:

```json
{"application_id":"UUID do protocolo","status":"received"}
```

Somente essa resposta válida libera a tela de protocolo. Falha, timeout, JSON inesperado ou serviço ausente não exibem sucesso. Repetir o mesmo UUID com o mesmo conteúdo devolve o protocolo existente; mudar o conteúdo com o UUID anterior retorna 409. CPF e e-mail duplicados também retornam uma mensagem genérica com 409. Erros de validação retornam 422 sem reproduzir os dados enviados. O corpo tem limite de 16 KiB; há limite de 10 tentativas por IP em 10 minutos por processo, e recebimentos indisponíveis retornam 503. Para múltiplos workers ou maior volume, substitua SQLite e o limite em memória por armazenamento compartilhado antes de escalar.

As senhas recebem scrypt com salt aleatório; os dados pessoais, renda, conta e autorizações são criptografados com AES-256-GCM, nonce aleatório e vínculo ao protocolo. Os índices de CPF/e-mail e o resumo do pedido usam HMAC. O banco não guarda a senha original, confirmações de senha/e-mail nem PII em texto puro. O horário de recebimento e a versão dos consentimentos são registrados no servidor. Não há emissão automática de aprovação ou contrato. O painel `/admin` exige senha e TOTP, conforme descrito abaixo.

`GET /api/health` informa a disponibilidade do processo e se o recebimento foi habilitado. Faça backup do banco e da chave separadamente e aplique à operação uma política definida de acesso e retenção. A API recebe solicitações. A confirmação de e-mail/celular e a conexão Open Finance estão preparadas por meio dos provedores abaixo e precisam ser ativadas. Não há login de cliente, decisão de crédito ou assinatura contratual. O pré-cadastro original conserva suas cinco etapas e não exige códigos nem conexão bancária; estes recursos aparecem **apenas no cadastro PF**.

## Verificação

```bash
npm ci --ignore-scripts
npm run build
npm run test:unit
python3 -m unittest discover -s tests -p '*_test.py' -v
```

O GitHub Actions executa essas verificações e os testes de navegador, incluindo todas as etapas, máscaras, autorizações, falta de serviço, falha de envio, repetição idempotente e confirmação de protocolo. Todos os dados de teste são sintéticos.

A renda usa formatação brasileira durante a digitação, mantendo centavos no valor numérico enviado à API. Para `income.occupation: "other"`, `income.source_description` exige 3 a 200 caracteres; para as demais opções esse campo deve ser `null` ou omitido. A descrição é incluída na revisão e no conteúdo criptografado. As fixtures de CEP contêm endereços públicos consultados em 13/10/2026; os dados pessoais dos testes são sintéticos.

## Confirmação PF — Twilio Verify

Copie `backend/.env.example` para `/etc/emprestimofacil/backend.env`, fora do Git, com permissão 0600. Configure `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` e `TWILIO_VERIFY_SERVICE_SID`. Habilite SMS e e-mail no serviço Verify com códigos de seis dígitos. E-mail requer integração SendGrid e remetente/domínio verificado na Twilio. Configure as proteções antifraude e limites da conta do provedor. Não envie credenciais por mensagem nem as coloque no JSON público.

O servidor solicita e verifica os códigos diretamente na Twilio. Não gera códigos locais nem os devolve ao navegador. A aplicação limita a validade a dez minutos, o reenvio a intervalos de 60 segundos e a confirmação a cinco tentativas por desafio, além dos limites por IP/destino. Trocar e-mail ou celular invalida a sessão anterior. Um código consumido não confirma outro desafio.

Sob `/api/security`, as rotas são `GET /capabilities`, `POST/GET/DELETE /session`, `POST /codes/send` e `POST /codes/check`. O perfil inclui nome, e-mail, celular e CPF quando informado. A sessão expira em duas horas. O cookie opaco `__Host-ef-session` é Secure, HttpOnly e SameSite=Strict, sem dados pessoais. Todas as mutações exigem a origem HTTPS correta. Confirmar contatos não comprova identidade, renda ou elegibilidade.

Referências: [envio](https://www.twilio.com/docs/verify/api/verification) e [conferência](https://www.twilio.com/docs/verify/api/verification-check).

## Open Finance PF — Belvo OFDA

Configure `BELVO_SECRET_ID`, `BELVO_SECRET_PASSWORD`, `BELVO_ENVIRONMENT`, `BELVO_WEBHOOK_TOKEN`, `COMPANY_NAME` e `COMPANY_TERMS_URL`. A URL de termos precisa ser HTTPS e representar a operação. Use um token de webhook aleatório com no mínimo 32 caracteres. Comece com `sandbox`, identificado na interface. Produção exige contratação, habilitação e credenciais próprias no provedor. Estes arquivos não ativam a conta nem o serviço real.

Configure o webhook HTTPS `/api/security/bank/webhook` na Belvo com `Authorization: Bearer <BELVO_WEBHOOK_TOKEN>` e eventos de atualização histórica de OWNERS, ACCOUNTS e TRANSACTIONS. Os callbacks success/exit/event são gerados no domínio da operação com estado aleatório, prazo e vínculo à sessão. O navegador só recebe a URL temporária do widget hospedado em `widget.belvo.io`, sem segredos de servidor. O formulário não pede senha bancária.

`POST /bank/start` exige contatos confirmados, CPF válido e consentimento expresso. O callback valida o link diretamente na Belvo e confere o `external_id` da tentativa. O servidor aguarda os três recursos por webhook; `GET /bank/status` lê os dados pela API e confere o CPF em OWNERS. Dados e autorizações ficam criptografados. Banco conectado, dados disponíveis e crédito aprovado são estados distintos; este módulo não calcula score nem aprova empréstimos. A paginação é limitada e registra `truncated` quando o limite é atingido; dados incompletos não devem virar decisão automática.

`DELETE /bank` confirma revogação somente após excluir o link no provedor. A manutenção encaminha conexões expiradas para exclusão e mantém falhas na fila para tentar novamente; monitore `provider_pending`. `OPEN_FINANCE_REQUIRED=false` permite conexão complementar; `true` exige dados disponíveis e CPF conferido antes do envio PF. Os caminhos acima usam o prefixo `/api/security`.

Referência: [documentação Belvo](https://developers.belvo.com/) — Hosted Widget OFDA, Widget Access Token, Owners, Accounts, Transactions, Aggregation Webhooks e exclusão de Links. Revise o escopo contratado com o provedor para os recursos necessários à operação.

## Recebimento PF e arquivos privados

`POST /api/pf/applications` valida os metadados, origem, consentimentos e a sessão: nome, CPF, e-mail e celular devem coincidir com o perfil, os dois contatos precisam estar confirmados e a conexão precisa estar pronta quando exigida. Limite de corpo: 24 KiB; `Idempotency-Key` UUID. Retorna um protocolo com estado `uploading` e `required_files`, ainda sem confirmar recebimento completo.

Cada `PUT /api/pf/applications/{id}/files/{kind}` envia os bytes do arquivo, até 3 MiB, vinculados à sessão do titular. Os oito arquivos base são fachada, entrada, frente, verso, selfie, vídeo de identificação, comprovante de endereço e imagem do Instagram. CLT/motorista acrescenta o comprovante correspondente. O servidor verifica conteúdo real: imagens são decodificadas, normalizadas e têm metadados removidos; capturas exigem JPEG. Comprovantes PDF têm até 30 páginas, sem senha, formulários, anotações, JavaScript ou anexos. Instagram não aceita PDF. FFprobe valida MP4/WebM de 2–20 segundos, uma trilha de vídeo e dimensões limitadas; entrada não aceita áudio. Temporários são privados e removidos ao terminar. Não há antivírus, OCR ou prova de autenticidade documental/câmera física.

`POST /api/pf/applications/{id}/complete` retorna `received` somente após validar a sessão novamente e confirmar todos os arquivos exigidos. Repetir a mesma chave e conteúdo preserva o protocolo; alteração com a chave anterior retorna 409. IDs de outro usuário não permitem consultar documentos. A tela final mostra recebimento e cadastro aguardando análise, sem aprovação nem cobrança.

## Equipe, retenção e recuperação

Os documentos usam AES-256-GCM com nonce aleatório e vínculo ao registro, arquivos 0600, pastas 0700 e nomes aleatórios; não existem URLs públicas. A chave fica fora do Git e do backup. Esta proteção também depende do sistema operacional, dos acessos à VPS e da proteção da chave.

O painel `/admin` permite consultar registros e baixar documentos. Exige senha de 12–128 caracteres e TOTP, tem limites de tentativas, bloqueia reutilização do código e encerra a sessão em 15 minutos. Crie um usuário por pessoa em terminal privado, com o ambiente do serviço carregado:

```bash
.venv/bin/python -m backend.maintenance admin-create
```

O comando apresenta a configuração do autenticador apenas no terminal do operador e exige um código válido antes de criar o usuário. Não compartilhe essa chave. A trilha de acessos registra identidade opaca, ação, objeto e horário com cadeia HMAC, sem copiar CPF ou documentos. Ela não é imutável contra alguém que controla a VPS e a chave. A retenção documental não apaga essa trilha mínima; defina uma política separada para estes registros operacionais.

`DATA_RETENTION_DAYS` é 30 por padrão (1–365); PF incompleto expira após um dia. Sessões deixam de dar acesso após duas horas e são removidas fisicamente na próxima manutenção. A rotina apaga registros expirados, documentos e órfãos e encaminha links conhecidos para revogação. `BACKUP_RETENTION_DAYS` é 7 por padrão: dados apagados do banco ativo podem persistir no backup por até sete dias adicionais, além do intervalo da rotina. A política da operação deve informar os prazos reais e a retenção no provedor.

Instale os exemplos `emprestimofacil.service.example`, `emprestimofacil-maintenance.service.example` e `.timer.example`, ajustando usuário e caminhos. Use `/opt/emprestimofacil`, `/var/lib/emprestimofacil` e `/var/backups/emprestimofacil`; os diretórios privados pertencem ao usuário do serviço. O timer proposto executa descarte e backup às 03h diariamente, mas não é ativado automaticamente pelos arquivos. Verifique status/resultados e a fila de exclusão. O serviço usa um worker, apenas loopback, umask 0077, usuário dedicado e escrita limitada. Nunca sirva o diretório inteiro do repositório, arquivos de ambiente, banco ou backups pelo servidor web.

```bash
.venv/bin/python -m backend.maintenance purge
.venv/bin/python -m backend.maintenance backup --directory /var/backups/emprestimofacil
.venv/bin/python -m backend.maintenance audit-check
```

O backup faz cópia consistente do SQLite e documentos referenciados, todos já criptografados, sem incluir a chave. Configure cópia externa privada para sobreviver à perda da VPS. Para recuperar, pare serviço e manutenção, configure um **novo diretório vazio** com a chave original e execute `restore --directory CAMINHO_DO_BACKUP`. A rotina confere integridade e criptografia, invalida sessões e encaminha links conhecidos para revogação. Confira registros, arquivos, auditoria e manutenção antes de reabrir o recebimento. Não restaure por cima do banco em uso; ensaie a recuperação com dados sintéticos.

Os testes usam provedores substituídos, sem SMS, e-mail, banco ou decisão de crédito real. A câmera, contatos e localização são emulados nos navegadores; é necessário homologar entrega dos códigos e captura em aparelhos físicos por HTTPS antes da ativação. O servidor testa também uploads, sessão, limites, CPF divergente, MFA, isolamento, retenção e restauração.

A integração de parcelas com a Cakto possui configuração, contrato, credenciais e retenção próprios. Leia [CAKTO.md](CAKTO.md) antes de ativá-la. Não utilize as contas ou dados sintéticos da suíte para gerar cobranças de produção.

## Compra independente de afiliados

A página `/afiliados.html` e os endpoints `/api/sales/` vendem o produto **AFILIADOS QG BALTIGO**, por R$ 7,49. Leia [CAKTO_SALES.md](CAKTO_SALES.md) para a ativação via ambiente da VPS. Esta compra não interfere nos cadastros ou na análise de empréstimos.
