---
name: analise-sql
description: Para responder perguntas sobre o banco de dados com rigor — descobrir o schema real, escrever SQL somente-leitura correto e citar a proveniência dos números.
---

# Análise de dados em SQL

## Quando usar

Use quando a pergunta depender de dados que estão no **banco de dados** conectado (contagens,
somas, rankings, tendências, cruzamentos entre tabelas).

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
