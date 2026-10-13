# Empréstimo Fácil

Página estática com simulador responsivo de empréstimo pessoal. A barra atualiza o valor solicitado, a parcela e o total durante o movimento. Inclui 3, 6, 9 e 12 parcelas, botões de mais e menos, resumo, perguntas frequentes e menu para celular.

## Usar

Abra `index.html` no navegador. As imagens, a logo, os estilos e as fontes estão incorporados; não é necessário instalar dependências ou executar um build para publicar o arquivo. Para hospedagem estática, use a raiz deste repositório como diretório público.

## Editar e reconstruir

Os arquivos editáveis estão em `src/`; os recursos visuais usados na geração estão em `assets/`. Depois de editar, execute com Python 3:

```sh
python3 scripts/build-reference.py
```

O comando atualiza `index.html` na raiz. Utiliza somente a biblioteca padrão do Python.

## Valores do simulador

- R$ 500 a R$ 3.500, em passos de R$ 250.
- 3, 6, 9 ou 12 parcelas mensais, totalizando 52 combinações.
- Cálculo ilustrativo de parcelas fixas pelo sistema Price. O total é a parcela arredondada para centavos multiplicada pelo prazo.
- Não inclui IOF, tarifas ou o custo efetivo total de uma contratação.

A taxa de referência em `src/reference-capture.json` foi obtida matematicamente para manter o exemplo de R$ 2.000 em 12 parcelas de R$ 367,15. É exibida como 14,88% ao mês e **não é uma taxa comercial confirmada**. O registro em `src/quotes.json` é histórico da referência fornecida, não uma tabela de cotações comerciais.

A página não coleta CPF, senha ou dados bancários, não envia propostas, não aprova crédito e não contrata empréstimos. As condições reais dependem da instituição responsável.

## Verificação

Na versão enviada, foram conferidos os valores e arredondamentos das 52 combinações, a atualização síncrona via eventos `input` e `change`, os limites, os botões, o resumo, a navegação e a integridade dos recursos. A inspeção visual em navegador não estava disponível no ambiente de trabalho.
