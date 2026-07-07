# PRD — Agent Harness para Áreas de Negócio (Jurídico, Financeiro, Marketing)
---

## 1. Visão Geral

### 1.1 Resumo executivo
Aplicação de agente de IA que permite a usuários de áreas de negócio não-técnicas (jurídico, financeiro, marketing) obter análises, relatórios e apresentações a partir de contexto documental próprio (planilhas, Word, PDFs em pasta) combinado a dados históricos estruturados (banco de dados), utilizando skills especializadas por domínio para transformar dados brutos em significado acionável.

### 1.2 Problema a resolver
Áreas de negócio acumulam conhecimento em dois formatos que hoje não conversam entre si:
- **Documentos não estruturados** (contratos, relatórios, briefings) parados em pastas, sem busca semântica nem síntese automatizada
- **Dados históricos estruturados** em bancos de dados, acessíveis apenas via ferramentas de BI ou consultas manuais de TI

O trabalho de cruzar essas duas fontes para produzir um relatório, uma análise ou uma apresentação é manual, repetitivo e depende de conhecimento tácito de quem já fez aquele tipo de análise antes.

### 1.3 Objetivo do produto
Permitir que um usuário de negócio, sem escrever código, aponte o agente para sua pasta de documentos e sua fonte de dados, escolha (ou deixe o agente escolher) a skill de análise adequada, e receba um artefato final pronto — relatório, análise ou apresentação — com rastreabilidade da fonte de cada afirmação.

### 1.4 Não-objetivos (explicitamente fora de escopo nesta fase)
- Geração ou execução de código arbitrário pelo agente
- Edição colaborativa em tempo real de documentos
- Substituição de sistemas de BI existentes (o agente consome dados, não os substitui)
- Automação de decisões (o agente analisa e recomenda; decisão final é humana)

---

## 2. Personas e Casos de Uso

### 2.1 Personas primárias

| Persona | Departamento | Dor principal |
|---|---|---|
| Analista Jurídico | Jurídico | Resumir/comparar contratos manualmente, risco de perder cláusula relevante |
| Analista Financeiro | Financeiro | Montar análise de variação orçamentária cruzando planilhas e sistema histórico |
| Analista de Marketing | Marketing | Consolidar performance de campanha a partir de múltiplas fontes e apresentar para stakeholders |

### 2.2 Casos de uso principais (exemplos por domínio)

**Jurídico**
- Resumir um contrato novo (partes, prazo, cláusulas de rescisão, riscos identificados)
- Comparar duas versões de um contrato e destacar mudanças materiais
- Cruzar cláusulas de um novo contrato com histórico de disputas/processos no banco de dados

**Financeiro**
- Gerar análise de variação orçamentária (planejado vs. realizado) com narrativa explicativa
- Cruzar dados de faturamento por cliente (banco) com contratos vigentes (pasta) para identificar inconsistências
- Montar relatório mensal de fechamento com tendência histórica

**Marketing**
- Consolidar relatório de performance de campanha (KPIs, comparação com período anterior)
- Gerar apresentação executiva a partir de dados de múltiplas campanhas
- Analisar briefings de campanha (Word) e sugerir alinhamento com resultados históricos (banco)

---

## 3. Escopo Funcional

### 3.1 Ingestão de contexto documental (pasta)
- **RF-01**: O sistema deve permitir configurar uma pasta (ou conjunto de pastas) como fonte de contexto por usuário/departamento
- **RF-02**: O sistema deve extrair conteúdo de PDF (texto e tabelas, com OCR para digitalizados), Word (preservando estrutura de seções/cláusulas) e Excel (preservando schema e relações entre células, não apenas texto)
- **RF-03**: Cada documento ingerido deve ser fragmentado (chunking) com metadados: tipo de documento, departamento de origem, data, autor (quando disponível), seção/localização no documento original
- **RF-04**: O sistema deve reprocessar automaticamente documentos novos ou alterados na pasta monitorada (ingestão incremental)

### 3.2 Acesso a dados históricos (banco de dados)
- **RF-05**: O sistema deve expor ao agente apenas consultas pré-aprovadas e parametrizadas por domínio (não SQL livre)
- **RF-06**: Cada domínio (jurídico, financeiro, marketing) deve ter seu próprio conjunto de consultas disponíveis, mapeado às suas necessidades de análise
- **RF-07**: Resultados de consulta devem retornar com metadados de proveniência (qual tabela/fonte, data de extração)

### 3.3 Skills de domínio
- **RF-08**: O sistema deve permitir cadastrar skills por processo de negócio (ex: "resumo de contrato", "análise de variação orçamentária", "relatório de campanha"), cada uma contendo: quando usar, fontes de dado necessárias, passo a passo do raciocínio, formato de saída esperado
- **RF-09**: O agente deve ser capaz de selecionar automaticamente a skill mais adequada à solicitação do usuário, ou permitir seleção manual
- **RF-10**: Skills devem ser versionadas e passíveis de revisão/aprovação antes de entrarem em produção

### 3.4 Geração de artefatos
- **RF-11**: O sistema deve gerar relatórios em Word, apresentações em PowerPoint e análises estruturadas (texto/tabelas) a partir do conteúdo produzido pelo agente
- **RF-12**: A geração de artefato deve usar templates com identidade visual configurável por organização, desacoplados do raciocínio do agente
- **RF-13**: Todo artefato gerado deve conter rastreabilidade — cada afirmação relevante deve ser vinculada à fonte (documento + trecho, ou consulta + parâmetros) que a originou

### 3.5 Controle de acesso
- **RF-14**: O sistema deve aplicar controle de acesso baseado em papel (RBAC) tanto na recuperação de documentos quanto na execução de consultas ao banco
- **RF-15**: Um usuário nunca deve receber, nem em busca semântica nem em resposta gerada, conteúdo de departamento ao qual não tem acesso
- **RF-16**: Ações de envio do artefato para fora do sistema (e-mail, publicação) exigem confirmação explícita do usuário

### 3.6 Memória e aprendizado contínuo
- **RF-17**: O sistema deve registrar, ao final de cada sessão, um log estruturado de eventos (documentos consultados, consultas executadas, skill utilizada, artefato gerado)
- **RF-18**: Um processo de reflexão deve extrair, periodicamente, preferências de formato e padrões recorrentes por departamento (memória semântica)
- **RF-19**: Quando um artefato gerado exigir correção manual relevante, esse sinal deve ser capturado para eventualmente refinar a skill correspondente
- **RF-20**: Refinamentos de skill originados do aprendizado devem passar por aprovação antes de entrarem em produção (nunca automático)

---

## 4. Requisitos Não-Funcionais

| Categoria | Requisito |
|---|---|
| Segurança | Controle de acesso a documento e a consulta deve ser aplicado no nível do filtro de busca/consulta, não apenas checado após recuperação |
| Confiabilidade | Toda afirmação em artefato gerado deve ser rastreável a uma fonte; ausência de fonte deve ser sinalizada explicitamente, não omitida |
| Auditabilidade | Todo acesso a documento sensível e toda consulta ao banco devem ficar registrados com usuário, timestamp e escopo |
| Desempenho | Ingestão incremental de novos documentos não deve bloquear o uso do sistema pelos usuários já ativos |
| Usabilidade | Fluxo de solicitação de análise não deve exigir conhecimento técnico (sem SQL, sem sintaxe de programação) |
| Extensibilidade | Adição de nova skill ou novo domínio de negócio não deve exigir alteração da arquitetura central |

---

## 5. Arquitetura em Alto Nível

### 5.1 Componentes principais
1. **Camada de ingestão** — parsing por tipo de documento (PDF/Word/Excel), chunking com metadados, indexação vetorial
2. **Camada de dados estruturados** — conjunto de consultas parametrizadas por domínio, expostas via protocolo padronizado de ferramentas (MCP)
3. **Orquestrador do agente** — grafo de execução: interpretação da solicitação → seleção de skill → recuperação (documentos + banco) → aplicação da skill → geração de conteúdo estruturado
4. **Repositório de skills** — versionado, uma skill por processo de negócio, com fluxo de aprovação
5. **Camada de renderização** — templates de Word/PowerPoint desacoplados do raciocínio, aplicação de identidade visual
6. **Camada de RBAC** — metadados de controle de acesso aplicados como filtro em toda busca e consulta
7. **Camada de memória e reflexão** — log episódico de sessões, extração periódica de memória semântica, proposta de refinamento de skills

### 5.2 Fluxo típico de uma solicitação
1. Usuário solicita análise em linguagem natural
2. Sistema identifica departamento/escopo do usuário (RBAC)
3. Agente seleciona skill adequada (ou usuário seleciona manualmente)
4. Agente recupera documentos relevantes (filtrados por escopo) e executa consultas parametrizadas necessárias
5. Skill orienta o raciocínio: quais pontos analisar, como estruturar a resposta
6. Conteúdo estruturado é gerado com rastreabilidade de fonte por afirmação
7. Camada de renderização produz o artefato final (Word/PPTX/texto)
8. Sessão é registrada para alimentar aprendizado futuro

---

## 6. Skills — Exemplos de Especificação

### 6.1 Skill: Resumo de Contrato (Jurídico)
- **Quando usar**: usuário solicita resumo, síntese ou pontos de atenção de um contrato
- **Fontes necessárias**: documento do contrato (pasta); opcionalmente, histórico de disputas relacionadas ao mesmo cliente/fornecedor (banco)
- **Passo a passo**: identificar partes, objeto, prazo, valor, cláusulas de rescisão, cláusulas de penalidade, comparar com histórico de disputas se disponível
- **Formato de saída**: relatório estruturado (Word) com seções fixas: Partes, Objeto, Prazo, Riscos Identificados, Recomendação

### 6.2 Skill: Análise de Variação Orçamentária (Financeiro)
- **Quando usar**: usuário solicita comparação entre planejado e realizado, ou análise de tendência financeira
- **Fontes necessárias**: planilha orçamentária (pasta), histórico de realizado (banco)
- **Passo a passo**: calcular variação absoluta e percentual por categoria, identificar maiores desvios, contextualizar com tendência histórica
- **Formato de saída**: relatório com tabela de variações + narrativa explicativa + gráfico de tendência

### 6.3 Skill: Relatório de Performance de Campanha (Marketing)
- **Quando usar**: usuário solicita consolidação de resultados de campanha
- **Fontes necessárias**: briefing da campanha (pasta), dados de performance histórica (banco)
- **Passo a passo**: consolidar KPIs, comparar com período/campanha anterior, destacar principais aprendizados
- **Formato de saída**: apresentação executiva (PowerPoint) com estrutura fixa: Contexto, Resultados, Comparação, Recomendações

---

## 7. Métricas de Sucesso

| Métrica | Como medir |
|---|---|
| Tempo de produção de um artefato | Comparar tempo médio manual vs. tempo com o agente |
| Taxa de retrabalho | % de artefatos gerados que exigiram correção manual relevante |
| Taxa de rastreabilidade | % de afirmações no artefato final com fonte identificada |
| Adoção por departamento | Número de sessões ativas por departamento ao longo do tempo |
| Qualidade percebida | Avaliação qualitativa dos usuários de negócio sobre utilidade do artefato gerado |
| Evolução das skills | Número de refinamentos de skill aprovados a partir de sinais de retrabalho |

---

## 8. Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| Vazamento de dado entre departamentos | RBAC aplicado no nível de filtro de busca/consulta, nunca apenas checagem posterior |
| Alucinação em relatório (afirmação sem fonte) | Exigir rastreabilidade obrigatória; sinalizar explicitamente quando uma afirmação não tem fonte |
| Degradação de qualidade por acúmulo de memória mal curada | Processo de consolidação periódica com remoção de duplicatas e arquivamento |
| Skill aprendida incorporando comportamento ruim | Toda skill nova ou refinada passa por aprovação antes de produção |
| Resistência de adoção por usuários não-técnicos | Fluxo de solicitação 100% em linguagem natural, sem exposição de detalhes técnicos |

---

## 9. Roadmap Proposto (fases)

**Fase 1 — MVP de um domínio único**
- Ingestão de documentos de um departamento piloto (ex: financeiro)
- Uma ou duas skills essenciais
- Geração de relatório em Word com rastreabilidade básica

**Fase 2 — Múltiplos domínios + RBAC completo**
- Expansão para jurídico e marketing
- RBAC integrado à busca vetorial e às consultas ao banco
- Geração de apresentações (PowerPoint) além de relatórios

**Fase 3 — Memória e aprendizado contínuo**
- Log episódico de sessões
- Extração de memória semântica (preferências por departamento)
- Fluxo de proposta e aprovação de refinamento de skills

**Fase 4 — Maturidade operacional**
- Consolidação periódica de memória
- Métricas de sucesso instrumentadas e acompanhadas
- Expansão do repositório de skills por demanda dos departamentos

---

## 10. Questões em Aberto

- Qual será o banco de dados histórico de referência por domínio (schema já existe ou precisa ser modelado)?
- Quem aprova skills novas/refinadas — um responsável técnico, um comitê por área, ou ambos?
- Qual o volume esperado de documentos por pasta/departamento (impacta estratégia de ingestão incremental)?
- Haverá necessidade de multi-tenant (múltiplas organizações) ou é uso interno de uma única organização?