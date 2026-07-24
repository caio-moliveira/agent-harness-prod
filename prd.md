# PRD — Data Agent: converse com seus dados, receba entregáveis rastreáveis

---

## 1. Visão Geral

### 1.1 Resumo executivo

**Data Agent** permite que um usuário de negócio não-técnico converse em linguagem natural com suas
próprias fontes de dados — uma pasta de documentos (PDF, Word, Excel, CSV) e/ou um banco de dados
somente leitura — e receba de volta análises, relatórios e apresentações prontos, com cada afirmação
relevante rastreável até a fonte que a originou (documento + trecho, ou tabela + consulta).

O produto é **genérico e horizontal**: não é segmentado por departamento nem exige conhecimento
técnico. Qualquer usuário aponta o agente para sua própria pasta/banco, opcionalmente anexa skills
(instruções reutilizáveis para processos recorrentes) e conversa.

### 1.2 Problema a resolver

Usuários de negócio acumulam conhecimento em dois formatos que hoje não conversam entre si:

- **Documentos não estruturados** (contratos, relatórios, planilhas, briefings) parados em pastas,
  sem busca semântica nem síntese automatizada
- **Dados históricos estruturados** em bancos de dados, acessíveis apenas via ferramentas de BI ou
  consultas manuais de TI

Cruzar essas duas fontes para produzir um relatório, uma análise ou uma apresentação é hoje manual,
repetitivo, e depende de conhecimento tácito de quem já fez aquele tipo de análise antes.

### 1.3 Objetivo do produto

Permitir que um usuário de negócio, sem escrever código, aponte o agente para sua pasta de
documentos e/ou sua fonte de dados, escolha (ou deixe o agente escolher) a skill de análise
adequada, e receba um artefato final pronto — relatório, análise ou apresentação — com
rastreabilidade da fonte de cada afirmação.

### 1.4 Não-objetivos (explicitamente fora de escopo)

- Geração ou execução de código arbitrário pelo agente
- Edição colaborativa em tempo real de documentos
- Substituição de sistemas de BI existentes (o agente consome dados, não os substitui)
- Automação de decisões (o agente analisa e recomenda; decisão final é humana)
- **Segmentação por departamento e controle de acesso baseado em papel (RBAC)** — o produto é de
  uso individual por usuário/pasta/banco; múltiplos departamentos com isolamento entre si não é um
  requisito desta fase (ver §10)
- **Bridge remoto de pasta** (daemon local dando acesso ao disco do usuário sem upload) — os dois
  mecanismos de conexão já disponíveis (path do host para self-hosted, upload pelo navegador para
  uso hospedado) cobrem os cenários de implantação relevantes
- **WebMCP / exposição das ações do app como ferramentas para agentes de terceiros** — visão de
  produto distinta (plataforma de automação), não perseguida nesta fase

---

## 2. Personas e Casos de Uso

### 2.1 Persona primária

**Analista de negócio não-técnico** — de qualquer área (jurídico, financeiro, marketing, operações
etc.), sem conhecimento de SQL ou programação, que precisa cruzar documentos próprios com dados
históricos para produzir uma entrega (relatório, análise, apresentação).

Os exemplos abaixo são **ilustrativos de departamento** — nenhum é tratado como segmento isolado do
sistema; qualquer usuário pode configurar qualquer combinação de skill + pasta + banco.

### 2.2 Casos de uso ilustrativos

- Resumir um documento novo (partes, prazo, cláusulas, riscos identificados) e comparar duas versões
  destacando mudanças materiais
- Gerar análise de variação (planejado vs. realizado) cruzando uma planilha da pasta com histórico
  no banco conectado, com narrativa explicativa
- Consolidar performance de um processo (KPIs, comparação com período anterior) a partir de dados de
  múltiplas fontes e gerar uma apresentação executiva
- Fazer uma pergunta ad-hoc sobre os arquivos da pasta ("qual região vendeu mais em julho?") e obter
  a resposta calculada exatamente via SQL, com a consulta usada citada como fonte

---

## 3. Escopo Funcional

### 3.1 Conexão de fontes

- **RF-01**: O usuário deve poder conceder uma pasta de documentos ao agente por dois caminhos:
  informando um path do host (implantação self-hosted, validado contra uma lista de raízes
  permitidas) ou fazendo upload pelo navegador (implantação hospedada, sem exigir path do host).
- **RF-02**: O usuário deve poder conectar um banco de dados somente leitura.
- **RF-03**: O acesso à pasta é confinado por sessão (virtual, sem escape via `..`/paths absolutos) e
  é somente leitura por padrão; escrita é uma capacidade explícita por agente.

### 3.2 Ingestão e exploração documental (vectorless)

- **RF-04**: Documentos (PDF, Word, Excel) são indexados **sem embeddings nem chunking**: cada
  arquivo vira um nó numa **árvore de estrutura** (estilo PageIndex, construída localmente) que o
  agente navega para ler exatamente a seção que precisa, com proveniência (documento + seção/página).
- **RF-05**: PDFs digitalizados (sem camada de texto) são sinalizados como tal; o agente é orientado
  a recorrer à imagem da página quando a extração de texto falhar.
- **RF-06**: A ingestão é incremental — documentos novos ou alterados na pasta são reprocessados sem
  bloquear o uso do sistema pelos usuários já ativos.

### 3.3 Cálculo exato via SQL

- **RF-07**: Cálculos sobre arquivos CSV/TSV/Excel da pasta (soma, contagem, média, ranking,
  cruzamento) são feitos por **SQL somente-leitura via DuckDB** sobre os arquivos — nunca por
  agregação manual do modelo. Cada arquivo é exposto como uma tabela nomeada pelo próprio arquivo.
- **RF-08**: O mesmo princípio vale para o banco de dados conectado — consultas são **SQL livre
  (`SELECT`/`WITH`/`EXPLAIN`/`SHOW`) sobre o schema real**, não um conjunto fixo de consultas
  pré-aprovadas; o agente descobre o schema antes de consultar e nunca inventa tabelas/colunas.
- **RF-09**: Todo resultado de cálculo traz a tabela e a consulta usada, para que o número seja
  rastreável na resposta final.

### 3.4 Subagentes isolados

- **RF-10**: O banco de dados conectado nunca é uma ferramenta direta do agente principal — é
  alcançado exclusivamente por um subagente isolado e somente leitura (`text_sql_agent`), delegado
  via `task()`, para que o loop de exploração de schema/consulta fique fora do contexto principal.
- **RF-11**: Pesquisa na web (quando habilitada) segue o mesmo padrão — um subagente isolado
  (`deep_research`) que devolve um resultado destilado, nunca uma ferramenta direta.

### 3.5 Geração de artefatos, preview e aprovação (HITL)

- **RF-12**: O sistema gera relatórios (Word), apresentações (PowerPoint) e planilhas (Excel) a
  partir do conteúdo produzido pelo agente.
- **RF-13**: Toda afirmação relevante no artefato deve trazer sua fonte (documento + seção, ou
  tabela + consulta); a ausência de fonte é sinalizada explicitamente como `[SEM FONTE]`, nunca
  omitida.
- **RF-14**: Antes de o binário ser gerado, o usuário pode expandir um **preview estruturado**
  (seções, afirmações e suas fontes, com `[SEM FONTE]` em destaque) sem precisar baixar o arquivo.
- **RF-15**: A geração do artefato fica **pendente de aprovação explícita do usuário** (gate HITL)
  antes de ser considerada concluída; plano e execução seguem o mesmo mecanismo de confirmação
  inline no chat.
- **RF-16**: Toda ação de efeito colateral fora do sistema (download de um arquivo gerado) só ocorre
  após a aprovação.

### 3.6 Escrita versionada com desfazer

- **RF-17**: Quando o agente tem permissão de escrita numa pasta, sobrescrever um arquivo existente
  exige confirmação explícita do usuário (gate HITL) e é sempre precedido de um snapshot da versão
  anterior.
- **RF-18**: O usuário pode listar versões e restaurar a versão anterior de um arquivo sobrescrito.

### 3.7 Biblioteca de skills

- **RF-19**: O agente carrega duas camadas de skills via *progressive disclosure*: **skills
  bundled** (sempre disponíveis — hoje `analise-sql` e `relatorios`) e a **biblioteca de skills do
  usuário**, anexada por agente.
- **RF-20**: Uma skill do usuário é um documento estruturado (nome, descrição, quando usar, fontes
  necessárias, passo a passo, formato de saída) editável pelo próprio usuário, sem escrever código.
- **RF-21**: Toda skill do usuário passa por uma **state machine de aprovação**
  (`draft → in_review → approved`); **somente skills `approved` são carregadas no agente** — uma
  edição em uma skill aprovada a devolve para `draft`, exigindo nova aprovação antes de voltar a
  valer.
- **RF-22**: O usuário aprova suas próprias skills diretamente na biblioteca (não há papel de
  revisor distinto do autor nesta fase — ver §10).
- **RF-23**: O usuário pode importar skills de um registro externo confiável, além de autorar as
  suas.

### 3.8 Contexto de ambiente da pasta (`AGENTS.md`)

Segue o padrão aberto [agents.md](https://agents.md/) — o mesmo que o próprio `deepagents`
implementa nativamente (`MemoryMiddleware`) e que o `AGENTS.md` na raiz deste repositório também
segue. É um arquivo diferente: este vive dentro de **cada pasta que o usuário conecta**, para
orientar o agente naquele ambiente de dados — não o `AGENTS.md` do repositório (esse é para quem
desenvolve o harness).

- **RF-24**: Ao montar o agente de uma sessão, o sistema lê um arquivo `AGENTS.md` (se existir) na
  raiz da pasta concedida — um documento livre, editado pelo próprio usuário na sua pasta, com
  resumos de arquivos, onde encontrar o quê, e qualquer orientação de comportamento que o usuário
  queira dar ao agente naquele ambiente.
- **RF-25**: O conteúdo do `AGENTS.md` é incorporado ao contexto fixo da sessão (não repetido por
  turno, já que não muda durante a conversa) e sujeito a um limite de tamanho para não estourar o
  contexto do modelo.
- **RF-26**: Diferente das skills, `AGENTS.md` não passa por aprovação — é conteúdo descritivo de
  baixo risco (orientação/contexto), não uma instrução de processo que o agente executa às cegas.
  É **somente leitura**: o agente nunca escreve de volta nele (deliberadamente diferente do
  comportamento padrão do `MemoryMiddleware` do deepagents, que instrui auto-edição — aqui esse
  papel já é coberto pela memória de longo prazo com reflexão, RF-29/RF-31, sem sobreposição).

### 3.9 Menções rápidas no composer

- **RF-27**: No campo de mensagem, digitar `/` abre um seletor das skills disponíveis para o agente
  atual (bundled + aprovadas e anexadas); digitar `@` abre um seletor dos arquivos da pasta
  conectada.
- **RF-28**: Selecionar um item insere seu nome exato como texto na mensagem — a menção é um atalho
  de digitação (evita erro de nome/path), não uma diretiva que força o comportamento do agente; o
  agente já tem acesso pleno a arquivos e skills e decide o uso como decidiria a partir de qualquer
  menção em linguagem natural.

### 3.10 Memória de longo prazo e aprendizado contínuo

- **RF-29**: A cada turno, o sistema injeta automaticamente: memória de longo prazo relevante
  (mem0), preferências aprendidas por reflexão sobre o que o usuário já aprovou, e um índice do
  trabalho já realizado neste agente (para não refazer o que já foi entregue).
- **RF-30**: Dentro de uma mesma conversa, o que já foi lido/consultado é lembrado (ledger de
  leitura), para não reler o mesmo arquivo nem repetir a mesma consulta.
- **RF-31**: Um processo de reflexão periódico extrai preferências de formato e padrões recorrentes
  a partir do que o usuário aprova ou corrige.

---

## 4. Requisitos Não-Funcionais

| Categoria | Requisito |
|---|---|
| Segurança | Acesso à pasta é confinado por sessão (virtual, sem escape de path) e somente leitura por padrão; escrita é capacidade explícita e gate-ada por confirmação |
| Segurança | Uma skill nunca é carregada no agente sem status `approved` — a ausência dessa checagem em qualquer novo caminho de carregamento é uma regressão de segurança, não só funcional |
| Confiabilidade | Toda afirmação em artefato gerado deve ser rastreável a uma fonte; ausência de fonte é sinalizada explicitamente, nunca omitida |
| Auditabilidade | Eventos de sessão (consultas, leituras, artefatos gerados, aprovações/rejeições HITL) ficam registrados com atribuição de usuário e timestamp |
| Desempenho | Ingestão incremental de novos documentos não bloqueia o uso do sistema pelos usuários já ativos; cálculos são feitos por SQL, nunca por agregação manual do modelo |
| Usabilidade | Fluxo de solicitação em linguagem natural, sem exigir SQL ou sintaxe de programação; menções `/` e `@` reduzem erro de digitação de nomes/paths |
| Extensibilidade | Adicionar uma nova skill não exige alteração de arquitetura — é autoria de conteúdo (SKILL.md) seguida de aprovação |

---

## 5. Arquitetura em Alto Nível

### 5.1 Componentes principais

1. **Agente principal** — Deep Agent (`deepagents`/LangGraph) construído por sessão, com tools de
   arquivo, cálculo (DuckDB), artefatos, memória e planejamento.
2. **Subagentes isolados** — `text_sql_agent` (banco conectado, somente leitura) e `deep_research`
   (pesquisa web), delegados via `task()`, cada um com seu próprio contexto.
3. **Backend de arquivos por sessão** — um `CompositeBackend` roteando por prefixo virtual:
   `/workspace/` → a pasta concedida (confinada, `virtual_mode`), `/skills/` → skills bundled,
   `/skills/user/` → biblioteca de skills aprovadas do usuário (materializada a partir do banco),
   e todo o resto → estado efêmero da sessão.
4. **Camada de documentos** — ingestão vectorless: manifesto + árvore de estrutura por documento,
   sem embeddings/chunking.
5. **Camada de cálculo** — DuckDB sobre arquivos da pasta; SQL livre sobre o banco via subagente.
6. **Biblioteca de skills** — modelo, repositório e state machine de aprovação
   (`draft → in_review → approved`) no Postgres; materializada em disco por agente a cada
   montagem, e montada no backend acima.
7. **Camada de renderização de artefatos** — gera Word/PPTX/Excel a partir de uma especificação
   estruturada com fonte por afirmação; a mesma especificação alimenta o preview antes da aprovação.
8. **Gate HITL** — pausa ações de geração/sobrescrita para confirmação explícita do usuário,
   registrada como evento de sessão.
9. **Camada de versionamento** — snapshot automático antes de qualquer sobrescrita numa pasta
   gravável, com desfazer.
10. **Memória** — mem0 + pgvector para memória de longo prazo, preferências refletidas e índice
    episódico de trabalho realizado.
11. **Frontend** — chat com streaming, timeline de atividade, aprovação inline, e menções `/`/`@`
    no composer.

### 5.2 Fluxo típico de uma solicitação

1. Usuário conecta pasta e/ou banco (host path ou upload) e, opcionalmente, anexa skills.
2. Usuário envia uma solicitação em linguagem natural (com ou sem menção `/skill`/`@arquivo`).
3. O agente injeta memória relevante, preferências aprendidas, índice de trabalho já feito e o
   `AGENTS.md` da pasta (quando existente).
4. O agente identifica a skill aplicável (bundled ou do usuário) via progressive disclosure, ou usa
   a que foi mencionada explicitamente.
5. O agente recupera documentos (árvore de estrutura) e/ou delega ao subagente de banco/pesquisa
   conforme necessário, e usa SQL para qualquer cálculo exato.
6. Ao reunir os dados, o agente chama a ferramenta de geração de artefato — a ação fica pendente de
   aprovação; o usuário pode expandir o preview antes de decidir.
7. Aprovado, o artefato é renderizado e disponibilizado para download; rejeitado, nada é gerado.
8. A sessão é registrada, alimentando memória e aprendizado futuro.

---

## 6. Skills — Exemplos Canônicos (bundled)

### 6.1 `analise-sql`

- **Quando usar**: a pergunta exige cálculo exato — somas, contagens, médias, rankings, tendências,
  cruzamentos — seja sobre o banco conectado ou sobre arquivos CSV/TSV da pasta.
- **Regra de ouro**: nunca somar/agregar linhas manualmente; descobrir o schema real antes de
  escrever a consulta; somente leitura; citar a tabela/consulta como fonte.

### 6.2 `relatorios`

- **Quando usar**: o usuário pede um relatório, apresentação ou planilha — não uma resposta curta no
  chat.
- **Regra de ouro**: reunir os dados com a fonte de cada um; chamar a ferramenta de geração
  (`gerar_artefato`/`gerar_planilha`) no mesmo turno em que os dados são reunidos — o entregável é o
  arquivo, nunca o corpo do relatório escrito como mensagem no chat.

Skills autoradas pelo usuário seguem a mesma estrutura (quando usar / fontes necessárias / passo a
passo / formato de saída) e passam pela aprovação antes de valerem para o agente (RF-21).

---

## 7. Métricas de Sucesso

| Métrica | Como medir |
|---|---|
| Tempo de produção de um artefato | Comparar tempo médio manual vs. tempo com o agente |
| Taxa de retrabalho | % de artefatos gerados que exigiram correção manual relevante após aprovação |
| Taxa de rastreabilidade | % de afirmações no artefato final com fonte identificada |
| Uso de preview | % de aprovações precedidas de expansão do preview (indica confiança sendo verificada, não assumida) |
| Adoção da biblioteca de skills | Número de skills autoradas e aprovadas por usuário ao longo do tempo |
| Qualidade percebida | Avaliação qualitativa dos usuários sobre utilidade do artefato gerado |

---

## 8. Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| Alucinação em relatório (afirmação sem fonte) | Rastreabilidade obrigatória; `[SEM FONTE]` explícito quando não há |
| Skill anexada mas nunca carregada, sem o usuário entender por quê | A UI deve mostrar o status da skill (`draft`/`in_review`/`approved`) de forma visível e oferecer a ação de aprovação diretamente na biblioteca — silêncio sobre o status é o que já causou confusão real numa sessão de teste |
| Skill aprovada sem revisão de qualidade (autor = aprovador) | Aceito nesta fase por não haver papel de revisor; reavaliar se o produto ganhar múltiplos usuários por conta/organização (ver §10) |
| Sobrescrita destrutiva de arquivo do usuário | Gate HITL + snapshot automático antes de qualquer sobrescrita, com desfazer |
| Degradação de qualidade por acúmulo de memória mal curada | Processo de consolidação periódica com remoção de duplicatas e arquivamento |
| Vazamento de path do host via log/trace | Path da pasta concedida nunca entra em metadados de trace (chave dedicada em `configurable`, fora do que o Langfuse vê) |

---

## 9. Estado de Implementação

Este PRD descreve o produto já em produção — não há um roadmap de fases separado do escopo
funcional (§3). Para acompanhamento de execução de tarefas de engenharia em andamento, ver
`.claude/plans/`.

---

## 10. Questões em Aberto

- A aprovação de skill deve continuar sendo o próprio autor aprovando, ou o produto vai precisar de
  um papel de revisor distinto (ex.: um gestor aprova skills da equipe) se ganhar múltiplos usuários
  por conta/organização?
- Qual o volume esperado de documentos por pasta? Isso impacta a estratégia de ingestão incremental
  e o dimensionamento da árvore de estrutura.
- Uso é de uma única organização (instância dedicada) ou há necessidade de multi-tenant (múltiplas
  organizações na mesma implantação)? Nenhuma segmentação de acesso entre organizações existe hoje.
