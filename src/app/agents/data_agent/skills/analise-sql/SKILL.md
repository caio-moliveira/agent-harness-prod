---
name: analise-sql
description: Para calcular com exatidão sobre dados — no banco (run_sql) OU em arquivos CSV/TSV da pasta (consultar_dados) — descobrindo o schema real e escrevendo SQL somente-leitura, sem somar à mão.
---

# Análise de dados em SQL (banco OU arquivos CSV/TSV)

## Quando usar

Use SEMPRE que a pergunta exigir cálculo exato — somas, contagens, médias, rankings, tendências,
cruzamentos — seja sobre o **banco de dados** conectado OU sobre **arquivos CSV/TSV** na pasta.
Regra de ouro: **você nunca soma/agrega linhas na mão** — o SQL calcula.

## Qual ferramenta

- **Arquivos CSV/TSV na pasta** → `listar_dados()` (mostra os arquivos como tabelas + colunas) e
  `consultar_dados(sql)` (roda SQL DuckDB sobre eles). Cada arquivo é uma tabela pelo nome (ex.:
  `vendas_2025.csv` → `vendas_2025`). Ex.:
  `SELECT regiao, SUM(receita) AS receita FROM vendas_2025 WHERE mes IN ('jul','ago','set') GROUP BY regiao ORDER BY receita DESC`
- **Banco de dados** conectado → `list_tables` / `describe_tables` / `run_sql`.

## Fluxo obrigatório

1. **Descobrir o schema real** — `list_tables`, depois `describe_tables` nas tabelas que vai usar.
   Nunca invente tabelas ou colunas; a consulta nasce do schema real.
2. **Escrever a consulta** — apenas `SELECT`/`WITH`/`EXPLAIN`/`SHOW`. Selecione só as colunas
   necessárias (evite `SELECT *`) e use `LIMIT` ao explorar.
3. **Executar é validar** — rode com `run_sql`. Se retornar erro, corrija a partir das tabelas
   disponíveis e execute de novo. Não dê a resposta final até a consulta rodar sem erro.
4. **Citar a fonte** — cada resultado traz uma linha `[proveniência]`; inclua a tabela + a consulta
   na resposta, para que os números sejam rastreáveis.

## Consultas com JOIN / agregação

Para perguntas que cruzam tabelas, planeje antes com `write_todos`:
- identifique todas as tabelas necessárias e as relações (FK = PK);
- monte `JOIN` com condição explícita, `WHERE` antes da agregação, `GROUP BY` com todas as colunas
  não agregadas, `ORDER BY` significativo e `LIMIT`.

## Regras

- **Somente leitura**: nunca `INSERT`/`UPDATE`/`DELETE`/`DROP`.
- Seja incansável: se a primeira tentativa falhar, ajuste e tente de novo antes de concluir "não
  encontrei".
- Explique o resultado em linguagem clara e cite as tabelas usadas.
