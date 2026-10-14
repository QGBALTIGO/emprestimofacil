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

Somente essa resposta válida libera a tela de protocolo. Falha, timeout, JSON inesperado ou serviço ausente não exibem sucesso. Repetir o mesmo UUID com o mesmo conteúdo devolve o protocolo existente; mudar o conteúdo com o UUID anterior retorna 409. CPF e e-mail não são reservados por um pré-cadastro sem confirmação: solicitações distintas podem usar os mesmos dados de identidade. Apenas a chave idempotente é única. A atualização migra bancos legados em uma transação, preservando registros e removendo os índices únicos de CPF/e-mail. Erros de validação retornam 422 sem reproduzir os dados enviados. O corpo tem limite de 16 KiB; há limite de 10 tentativas por IP em 10 minutos por processo, e recebimentos indisponíveis retornam 503. Para múltiplos workers ou maior volume, substitua SQLite e o limite em memória por armazenamento compartilhado antes de escalar.

As senhas recebem scrypt com salt aleatório; os dados pessoais, renda, conta e autorizações são criptografados com AES-256-GCM, nonce aleatório e vínculo ao protocolo. Os índices de CPF/e-mail e o resumo do pedido usam HMAC. O banco não guarda a senha original, confirmações de senha/e-mail nem PII em texto puro. O horário de recebimento e a versão dos consentimentos são registrados no servidor. Não há emissão automática de aprovação ou contrato. O painel `/admin` exige senha e TOTP, conforme descrito abaixo.

`GET /api/health` informa a disponibilidade do processo e se o recebimento foi habilitado. Faça backup do banco e da chave separadamente e aplique à operação uma política definida de acesso e retenção. A API recebe solicitações. A confirmação de e-mail/celular e a conexão Open Finance estão preparadas por meio dos provedores abaixo e precisam ser ativadas. A área `/cliente` permite acompanhar o pré-cadastro usando protocolo, e-mail e senha; não há decisão de crédito ou assinatura contratual. O pré-cadastro original conserva suas cinco etapas e não exige códigos nem conexão bancária; estes recursos aparecem **apenas no cadastro PF**.

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

`POST /api/pf/applications` valida os metadados, origem, consentimentos e a sessão: nome, CPF, e-mail e celular devem coincidir com o perfil, os contatos exigidos pela política precisam estar confirmados e a conexão precisa estar pronta quando exigida. Limite de corpo: 24 KiB; `Idempotency-Key` UUID. Retorna um protocolo com estado `uploading` e `required_files`, ainda sem confirmar recebimento completo.

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


## Revisão de 14/10/2026 — painel, área do cliente e e-mail

A área `/cliente` exige **protocolo + e-mail + senha do pré-cadastro** e abre apenas aquele registro. A senha continua scrypt; o cookie `__Host-ef-client` é opaco, HttpOnly, Secure e SameSite=Strict, com validade de 30 minutos. Há limite de tentativas por IP/e-mail, resposta genérica para credenciais incorretas, bloqueio de outra origem e invalidação de sessões ao restaurar backup. O resumo contém primeiro nome, CPF mascarado, valor, parcelamento e andamento; não devolve documentos, dados bancários ou notas internas. Cadastros PF não são ligados a contas por coincidência de CPF/e-mail não comprovados. Não foi implementada recuperação de senha por um contato ainda não confirmado.

O painel `/admin` funciona com origem HTTPS válida e banco privado mesmo quando `APPLICATIONS_ENABLED=false`, para que a equipe confira as pendências antes de abrir o recebimento. Tem filtros por tipo/situação/protocolo, paginação real de 25 registros e contagem total, visão das integrações e registro de andamento com mensagem para o titular e nota interna criptografada. Os estados são recebido, em análise, complemento solicitado e encerrado; nenhum aprova crédito, cria contrato, desembolsa ou gera cobrança. Configurado é diferente de homologado: o painel não afirma entrega de e-mail/SMS ou conexão bancária apenas porque existem credenciais.

Para o administrador indicado `kaykys468@gmail.com`, o operador da VPS pode gerar um convite único para o usuário escolher sua própria senha e cadastrar o autenticador:

```bash
.venv/bin/python -m backend.maintenance admin-invite --username kaykys468@gmail.com --invite-file /root/emprestimofacil-private/admin-setup-link.txt
```

Carregue previamente o ambiente privado do serviço. O link é gravado somente no arquivo escolhido, em pasta 0700/arquivo 0600, e nunca impresso pelo comando. Use uma pasta privada fora da árvore web, não envie a senha/segredo por chat e não publique o arquivo em Library, Git, logs ou backups públicos. O convite expira em 30 minutos; gerar outro revoga o anterior. O usuário abre o link por HTTPS, adiciona a chave ao seu autenticador, escolhe uma senha de 12–128 caracteres e confirma um código válido. Só então a conta é criada e o convite consumido. O fragmento do link é retirado do endereço imediatamente e não vai ao servidor em pedidos de página. A chave temporária nunca fica em armazenamento persistente do navegador. Aguarde o próximo código para o primeiro login, pois o código usado na criação não pode ser repetido. O comando `admin-create` no terminal privado continua disponível.

### Resend opcional

O envio de códigos por e-mail pode usar Resend com um domínio/remetente verificado pelo próprio operador:

```text
EMAIL_PROVIDER=resend
RESEND_API_KEY=<chave privada do Resend>
EMAIL_FROM=contato@dominio-verificado.example
```

`EMAIL_PROVIDER=twilio` preserva a integração anterior. O remetente de testes `@resend.dev` é recusado para não aparentar um serviço público pronto. A API Resend recebe somente destinatário e texto do código; CPF, documentos e dados bancários não são enviados. O servidor gera o código criptograficamente, guarda somente HMAC com salt dentro da sessão criptografada, limita reenvio/tentativas e expira em 10 minutos. O código não aparece em resposta da API, relatório ou URL. Uma falha do provedor não cria desafio válido nem mostra envio confirmado. Códigos consumidos não podem ser repetidos. Referência: [Send Email](https://resend.com/docs/api-reference/emails/send-email) e [idempotência](https://resend.com/docs/dashboard/emails/idempotency-keys).

As capabilities distinguem `email_verification`, `sms_verification` e `client_portal`. `verification` representa a disponibilidade dos canais exigidos pela política: e-mail e SMS quando `PHONE_VERIFICATION_REQUIRED=true`, ou somente e-mail quando `false`. Com Resend sozinho e a política somente por e-mail, o PF pode ser enviado após confirmar o código. O celular permanece não confirmado. Nenhuma conta externa é criada automaticamente pelo backend e uma configuração preenchida não comprova que o provedor aceitou a operação.

### Limites de armazenamento PF

O titular com contato confirmado tem no máximo um cadastro documental incompleto simultâneo e três novos cadastros por período de 24 horas. Repetir a mesma chave idempotente retoma o registro e não consome outra vaga. O limite é associado a cada contato confirmado (HMAC de e-mail e de celular), inclusive entre sessões e mudanças de CPF. Reusar qualquer um desses contatos preserva o orçamento de envio. Os padrões `PF_DAILY_LIMIT=3`, `MAX_PRIVATE_STORAGE_MB=1024` e `MIN_FREE_DISK_MB=256` restringem quantidade, volume privado ativo e reserva de disco. Os arquivos são gravados sob uma trava global para impedir ultrapassar a quota em uploads simultâneos. Falta de armazenamento retorna 507 com mensagem genérica; excesso diário retorna 429 e cadastro incompleto duplicado retorna 409. Os limites são preventivos e não substituem monitoramento da VPS, retenção ou capacidade reservada para seus outros serviços.

A suíte automatizada usa provedores substituídos e cobre portal, isolamento por protocolo, migração de banco, paginação acima de 100 registros, notas criptografadas, convite único/expiração/origem, e-mail Resend/erro/replay e quotas PF. Em 14/10/2026 houve também homologação separada com o Resend real: domínio verificado, mensagem entregue segundo o provedor e sete verificações do fluxo de código com banco temporário, sem criar solicitação em produção. SMS, pagamentos e Open Finance ainda dependem de contas e homologação próprias. Capturas em aparelhos físicos continuam distintas dos testes de navegador.


## Modo de confirmação por e-mail e revisão adicional

`PHONE_VERIFICATION_REQUIRED=true` mantém o fluxo legado com códigos de e-mail e SMS. `false` permite receber o PF após confirmar somente o e-mail; o celular cadastral permanece um dado informado e **não é marcado como confirmado**. O Open Finance continua opcional quando `OPEN_FINANCE_REQUIRED=false`. O padrão exige SMS para preservar instalações existentes; a mudança deve ser explícita no ambiente privado, seguida de reinício apenas do serviço Empréstimo Fácil. As capabilities e o estado da sessão devolvem `phone_required`, e `verification`/`pf_submission` refletem os canais efetivamente exigidos. E-mail ainda não configurado ou não confirmado não libera o PF.

A quota documental usa somente os contatos realmente confirmados. No modo sem SMS, um celular alegado não reserva vagas de outro titular que informou o mesmo número: o orçamento fica preso ao e-mail confirmado. Quando há confirmação de SMS, o celular também participa do limite. Trocar e-mail invalida a sessão e os códigos anteriores; mudar a política para exigir SMS não transforma uma sessão confirmada somente por e-mail em sessão apta a enviar.

O painel administrativo informa requisitos faltantes por integração, obrigatoriedade de SMS/Open Finance, sandbox/produção e links das documentações oficiais. Nunca recebe credenciais em formulário. O operador configura o arquivo privado `/etc/emprestimofacil/backend.env` (0600); o módulo `backend.configure_integrations` permite importar as credenciais de um arquivo privado com validação e gravação atômica, sem alterar a chave de dados, o banco, a origem ou os controles de ativação. Uma configuração preenchida não equivale a entrega, consentimento ou cobrança homologados.

A revisão adicional corrige retorno malformado de provedores para erros controlados; bloqueia substituir o link Belvo já vinculado por outro link na mesma tentativa; serializa revogação da sessão com atualizações em andamento; confere oferta e moeda explícita da Cakto; deduplica webhooks também dentro da trava da parcela/compra; rejeita códigos TOTP não ASCII e senha visualmente vazia; e aplica cabeçalhos de proteção também a erros de origem, formato e indisponibilidade. Cadastro PF incompleto deixa de ser acessível após 24 horas mesmo se a manutenção ainda não o tiver removido. Parcelas pendentes continuam visíveis para a equipe depois do prazo normal de retenção, conforme a regra de não descartar dívida ativa automaticamente.

Os testes adicionais exercitam chamadas simultâneas, revogação, códigos consumidos, respostas nulas/inesperadas, retorno bancário divergente, pagamento incerto, assinatura adulterada e política de confirmação por e-mail. Os contatos, documentos e pagamentos são sintéticos; transportes dos provedores são substituídos. Isso verifica o programa e seus contratos simulados, sem declarar envio real de códigos, consulta de bancos ou recebimento de dinheiro.
