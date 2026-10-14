# Revisão para campanha no Telegram

Data: 14/10/2026. Site: https://emprestimofacil.pro. Demanda informada: mais de 200 visitantes simultâneos. Plano de e-mail mantido gratuito por escolha do responsável. Condições atuais de crédito preservadas.

## Resultado

As falhas reproduzidas abaixo foram corrigidas e publicadas na VPS. O site respondeu sem erro aos testes de carga realizados, com os limites de e-mail e de provedores descritos adiante. Esse resultado permite avaliar uma campanha gradual; não comprova capacidade ilimitada, ausência de todos os bugs nem a jornada completa de 200 pessoas usando serviços externos ao mesmo tempo.

| Falha encontrada | Correção e verificação |
|---|---|
| Cadastros simultâneos travavam a gravação | Hash fora da transação; gravações serializadas; limite de duas derivações de senha. Antes: 79 recebidos, 19 erros e 2 pedidos sem resposta em 100 tentativas. Depois: 100 recebidos e gravados, sem falhas. |
| Login administrativo podia limpar a senha durante a digitação | Verificação inicial de sessão separada do login. Regressão com resposta 401 atrasada, login real na API isolada e 33 combinações de navegador/tela. |
| Fotos grandes consumiam memória desnecessária | Redução antes das cópias de orientação e cor; dois envios em validação por vez. Orientação, dimensões e remoção de metadados conferidas. |
| Consulta automática do Pix consumia repetidamente o orçamento do provedor | Conferência automática a cada cinco minutos; consulta manual preservada. Aviso da Cakto continua exigindo autenticação e pedido correspondente pela API. |
| Falha temporária da Cakto podia perder a conferência de um aviso | Fila persistente de conciliação, repetição e deduplicação. Testes de indisponibilidade, recuperação, assinatura e preço divergente. |
| Limite Cakto podia deixar tentativa em criação sem pedido | Reserva da chamada antes de persistir a tentativa; limite cheio recusa a emissão. Pedido já criado aguarda conferência, sem cobrar novamente. |
| Quota gratuita de e-mail tinha mensagem genérica | Reserva local de 100 tentativas/dia e 3.000/mês UTC; erro controlado e prazo de nova tentativa; ritmo de envio limitado. |
| Páginas públicas disputavam recursos com cadastros | Entrega e cache dos arquivos públicos no Nginx, compressão HTML e configuração pública sem gravação de limite por IP. Portais/API privados permanecem sem cache. |
| Cópias diárias multiplicavam armazenamento documental | Reaproveitamento dos documentos criptografados entre backups. Dois backups reais e restauração isolada conferidos, sem restaurar por cima da produção. |
| Link antigo podia abrir cadastro estático sem API | GitHub Pages encaminha à VPS e preserva `#cadastro-pf`. Checkout antigo também encaminha ao checkout próprio. |

## Evidências

| Bateria | Resultado |
|---|---|
| Site e formulários: Chromium, Firefox e WebKit, 320 a 2560 px | 858 execuções passaram na rodada geral. |
| Checkout após mudança da conferência automática | 165 execuções passaram. |
| Área do cliente com HTTPS local, autenticação e dados sintéticos | 108 execuções passaram. |
| Administração após correção do login, com API e banco isolados | 33 execuções passaram; filtros, notas privadas, documentos, isolamento e paginação acima de 100 registros. |
| Redirecionamento antigo preservando cadastro PF | 33 execuções passaram. |
| Backend, validação, criptografia, códigos, MFA, backups, pagamentos e integrações simuladas | 111 testes passaram. |
| Funções no navegador | Seis baterias passaram, incluindo máscaras, CPF, datas, valores, idempotência, capturas, permissões, consentimento e recibos. |
| Carga isolada limitada a 768 MB | 300 requisições simultâneas responderam; 100 cadastros iniciais fictícios foram recebidos e gravados. Percentil 95 dos cadastros: 6,41 s; pico de memória: cerca de 139 MB. |
| Carga pública na produção HTTPS | Três rodadas de 300 requisições em paralelo: 900 respostas HTTP 200, saúde posterior HTTP 200. Percentil 95 por rodada: 3,90 s, 4,98 s e 1,12 s. Somente leituras de início/configuração/capabilities. |
| Memória com imagens de 25 milhões de pixels | 12 JPEGs em duas validações paralelas: pico reduzido de 609,7 para 376,4 MB. PNG RGBA: 549,9 MB, dentro do limite isolado de 768 MB. Não representa a soma com todos os outros fluxos simultâneos. |
| Produção | Integridade SQLite confirmada; 2 pré-cadastros, 1 PF, 8 documentos e 2 compras existentes preservados; 57 serviços anteriormente ativos preservados. |
| Segurança e integrações reais | Segredos e chave de criptografia preservados; acesso anônimo ao admin/cliente e webhook sem autenticação recusados; autenticação Cakto e oferta de R$ 7,49 conferidas por leitura. Nenhuma compra, pagamento ou e-mail real criado pela rodada de carga. |
| Backups | Dois backups privados concluídos, oito documentos reutilizados; restauração conferida em diretório isolado e sessões invalidadas. |

Os totais das baterias contam execuções, incluindo repetições após correções; não representam testes únicos. Os dados de autenticação dos portais e dos provedores simulados pertencem às fixtures, não a clientes reais. WebKit automatizado não equivale a homologação em iPhone físico.

## Limites para anunciar

- **E-mail:** o plano gratuito do [Resend](https://resend.com/pricing) permite 100 e-mails/dia e 3.000/mês. Isso é quantidade de envios, não quantidade de visitantes. Reenvios, tentativas e outros envios da conta reduzem a disponibilidade. O limite renova por calendário UTC; em Cuiabá, o dia UTC vira às 20h. Novas confirmações PF podem ficar temporariamente indisponíveis quando a cota acabar.
- **Cakto:** compra independente com total R$ 8,48 informado antes do Pix. Geração do QR foi confirmada pelo comprador em rodada anterior. Pagamento efetivo, liquidação e reembolso real não foram executados pelo agente. A aplicação limita suas chamadas a 50/minuto; 200 visitas ao checkout não equivalem a 200 novos Pix por minuto. Crédito e cobranças de empréstimo permanecem separados.
- **Documentos:** armazenamento ativo limitado a 6 GiB, com reserva de disco de 5 GiB e retenção atual de 30 dias. A quantidade de cadastros comportada depende do tamanho dos anexos. O painel permite acompanhar o consumo; quota cheia recusa novo arquivo e preserva dados já recebidos.
- **Aparelhos físicos:** câmera, vídeo, localização, seleção de contatos e download no Safari precisam de conferência em aparelhos reais. Navegadores, API e simulações de permissões foram testados; isso não substitui essa etapa.
- **Operação de crédito:** recebimento não aprova nem desembolsa dinheiro. Análise, proposta, assinatura e crédito dependem da operação responsável. Os textos institucionais e a declaração de mais de 100 mil movimentações mensais foram preservados conforme autorização do responsável, sem auditoria independente desses fatos. Identificação completa da instituição na contratação e consistência das condições precisam ser conferidas pela operação.
- **SMS e Open Finance:** permanecem opcionais/desativados, sem contas de produção e sem homologação. Seus testes usam provedores substituídos. Não foi contratada integração paga.
- **Continuidade:** backup e restauração local conferidos. Não foi confirmada uma cópia fora da VPS; perda completa do servidor continua exigindo essa proteção externa. Recuperação de acesso do cliente permanece por atendimento e confirmação de identidade.

## Acompanhamento

Começar com uma parcela da audiência, acompanhar os cartões de e-mail, armazenamento e conciliação no painel e expandir conforme os cadastros reais. Usar o domínio principal ou `cadastro.emprestimofacil.pro` para o PF. O subdomínio `pix.emprestimofacil.pro` mantém o checkout independente. Esta revisão não alterou os prazos semanais e não habilitou pagamentos diários.
