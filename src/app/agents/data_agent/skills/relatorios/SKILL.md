---
name: relatorios
description: Para produzir entregáveis (relatório Word, apresentação PPTX ou planilha Excel) a partir de dados, com estrutura clara e fontes rastreáveis.
---

# Relatórios e entregáveis

## Quando usar

Use quando o usuário pedir um **relatório, documento, apresentação ou planilha** — não para uma
resposta curta no chat.

## Antes de começar: proponha o plano

Se o entregável for grande ou tiver várias etapas (coletar dados de várias tabelas/documentos, várias
seções), chame `propor_plano(titulo, passos)` e **aguarde a aprovação** antes de executar. Para um
entregável simples, siga direto.

## Reúna e fundamente os dados

1. Levante os números com as ferramentas certas (`run_sql` para banco; `get_document_structure` /
   `get_node_content` / `search_documents` / `read_document` para documentos).
2. Para **cada** dado, guarde a **fonte** (a tabela + a consulta que o produziu, ou o documento e a
   página). Itens sem fonte saem marcados como `[SEM FONTE]`.

## Gere o arquivo com a ferramenta certa

- **Relatório / apresentação** → `gerar_artefato(titulo, formato, secoes, ...)` com `formato`
  `"docx"` ou `"pptx"`. Monte `secoes` com títulos e itens; inclua `fonte` em cada item.
- **Planilha** → `gerar_planilha(titulo, planilhas)`; cada aba tem `colunas` e `linhas` — inclusive
  para exportar resultados de uma consulta SQL.
- **NUNCA** crie `.docx`/`.pptx`/`.xlsx` com `write_file` (o arquivo sairia corrompido). `write_file`
  é só para texto (`.md`, `.txt`, `.csv`).

Estes entregáveis passam por confirmação do usuário antes de serem gerados — avise que o arquivo
ficou aguardando aprovação.

## Qualidade

- Estruture em seções com títulos claros; lidere com o resultado, detalhe depois.
- Toda afirmação relevante deve ter fonte.
- Seja conciso; não invente números que não vieram das ferramentas.
