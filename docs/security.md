# Segurança e privacidade

Como o produto trata segredos, dados pessoais e entradas hostis. Complementa o `AGENTS.md` (que
descreve o *como* do código) com o *porquê* das decisões de segurança e o que um operador precisa
fazer em produção.

O contexto que molda tudo abaixo: o agente recebe acesso de leitura a **uma pasta de trabalho do
usuário** — planilhas de vendas, contratos, cadastros. É dado real de terceiros, muitas vezes com
CPF/CNPJ. A postura é: dado sensível não deve *entrar* na conversa, e o que já está nos arquivos
não deve *vazar* para logs, traces ou memória de longo prazo.

---

## Política de guardrails

Entrada e saída recebem tratamentos deliberadamente **assimétricos**, porque só um dos lados pode
de fato prevenir dano.

### Entrada — bloqueia, sempre, em todos os caminhos

`src/app/core/guardrails/input_screening.py` roda **antes** de o agente fazer qualquer coisa, tanto
no caminho de streaming quanto no não-streaming:

| Camada | O que barra |
|---|---|
| Content filter | palavras banidas e padrões de *prompt injection* ("ignore previous instructions", "reveal your system prompt"…) |
| PII de alto risco | chaves de API, cartão de crédito, SSN, **CPF e CNPJ** |

São checagens determinísticas (regex + dígito verificador), custam microssegundos e o turno é
recusado com uma mensagem em pt-BR que **nunca repete o dado sensível**. A recusa é persistida no
histórico — o usuário vê o que aconteceu, não um silêncio.

E-mails e telefones **não** bloqueiam: são conteúdo normal dos documentos do usuário. Eles são
redigidos adiante, não motivo para recusar o pedido.

### Saída — redação sempre; avaliação semântica é auditoria no streaming

- **Redação de PII**: o `PIIMiddleware` do deep agent redige e-mail, **CPF e CNPJ** no que trafega
  pelo modelo. Isso importa porque um resultado de ferramenta (uma planilha lida) carregaria os
  documentos para o contexto, os traces do Langfuse e o histórico persistido.
- **Avaliação semântica (`evaluate_safety`)**: no caminho **não-streaming** ela **bloqueia** —
  ali dá para substituir a mensagem antes de entregar. No caminho **streaming** ela é
  **auditoria**, desligada por padrão (`OUTPUT_SAFETY_AUDIT_ENABLED=true` para ligar).

> **Por que não bloquear no streaming?** Porque não dá — honestamente. Os tokens já foram exibidos
> ao usuário quando o veredito chega; um "bloqueio" pós-hoc só criaria a *ilusão* de proteção.
> Bloquear de verdade exigiria bufferizar a resposta inteira antes de exibir, o que elimina o
> streaming — o diferencial de UX do produto. A escolha é explícita: quando ligada, a auditoria
> registra métrica (`guardrail_checks_total{check_type="output_audit"}`) e log estruturado para
> revisão posterior, sem fingir que impediu algo.

---

## Segredos em produção

**Nunca** use os `.env` do repositório em produção — eles existem para desenvolvimento local.

| Segredo | Papel | Se vazar |
|---|---|---|
| `JWT_SECRET_KEY` | assina os tokens de usuário e de sessão | qualquer um forja sessões: **rotacione imediatamente** (invalida todos os tokens ativos) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY` | provedor do modelo | custo de terceiros na sua conta: revogue no console do provedor |
| `ENCRYPTION_KEY` | deriva a chave Fernet que cifra credenciais persistidas (senha do banco do usuário) | senhas de banco dos usuários ficam legíveis: rotacione e **force nova conexão** |
| `POSTGRES_PASSWORD` | banco da aplicação | acesso a todas as conversas, memórias e artefatos |
| `LANGFUSE_*` | observabilidade | traces (que contêm trechos de conversa) expostos |

**Recomendações**
1. Injete via *secret manager* do orquestrador (Kubernetes Secrets + KMS, AWS Secrets Manager,
   Azure Key Vault) — não via arquivo no host.
2. `JWT_SECRET_KEY` deve ser aleatório e longo (`openssl rand -hex 32`). Rotação programada
   desloga todo mundo; agende em janela de baixo uso.
3. `ENCRYPTION_KEY` **vazio é um default seguro**: sem ele o produto simplesmente não persiste
   senhas de banco (o usuário reinforma por sessão). Só defina se você precisa da conveniência —
   e, aí, trate-o como o segredo mais crítico depois do JWT.
4. O CI roda **gitleaks** com histórico completo em todo PR (bloqueante). Um segredo commitado e
   depois "removido" continua nos objetos do git: **rotacione, não confie no rebase**.

---

## Dados pessoais (LGPD)

- **Minimização na entrada**: CPF/CNPJ colados no chat são recusados (acima). O agente trabalha
  com os arquivos da pasta — não precisa que o usuário reescreva dados pessoais na conversa.
- **Redação no trânsito**: documentos que aparecem em resultados de ferramentas são redigidos
  antes de chegar ao modelo, aos traces e ao histórico.
- **Detecção por dígito verificador**: CPF e CNPJ são confirmados pelo *checksum*, não só pelo
  formato. Isso é precisão, não capricho: sem isso, todo id numérico de 11 dígitos nas planilhas
  do usuário seria redigido e a análise sairia corrompida.
- **Escopo da pasta**: `SANDBOX_ALLOWED_ROOTS` vazio desabilita concessão de pasta (default
  seguro). Nunca inclua diretórios com segredos; raiz de disco anula o sandbox.
- **Isolamento por usuário**: memórias, sessões, artefatos e downloads são escopados por
  `(user_id, agent_id)` e verificados na rota. Coberto por testes de autorização.
- **Direito de exclusão**: apagar um usuário exige remover sessões, mensagens, memórias (mem0/
  pgvector) e artefatos gerados. Rotina automatizada é *follow-up* registrado na issue #75.

---

## Dependências

`.github/workflows/security.yaml` roda em todo PR e semanalmente:

- **pip-audit** (Python) e **npm audit** (frontend) — hoje *reportando*, não bloqueando: uma
  advisory nova contra uma dependência transitiva não deve travar um PR sem relação. Revise o
  resumo do job e atualize deliberadamente; migre para bloqueante quando o passivo estiver zerado.
- **gitleaks** — **bloqueante**. Credencial commitada não é item de backlog, é incidente.
