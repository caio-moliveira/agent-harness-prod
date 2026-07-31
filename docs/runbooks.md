# Runbooks

One section per alert in `observability/prometheus/alerts.yml` (the alert's `runbook` annotation
links here by anchor). Each answers the same three questions: **o que significa**, **onde olhar**,
**o que fazer agora**. Keep alert and runbook in sync — an alert without a runbook is a pager that
nobody knows how to answer.

Consoles: Prometheus `http://localhost:9090` (alertas em `/alerts`) · Grafana
`http://localhost:3000` (dashboards **Agent Health & SLOs** e **LLM Observability**).

---

## APIDown

**Significa** Prometheus não consegue coletar `/metrics` da API há 2 min: processo caiu, não subiu,
ou está inacessível pela rede.

**Onde olhar**
- Logs do container/serviço: `make docker-compose-logs ENV=development` (ou `docker logs <api>`)
- A falha mais comum no boot é a validação de configuração de LLM (`llm_config_validated` ausente,
  `LLMConfigError` presente): `MODEL` sem a chave do provedor, ou pacote de integração faltando.
- Postgres de pé? A API sobe sem ele (degrada para stateless), mas o checkpointer some.

**O que fazer**
1. Se for `LLMConfigError`: corrija `MODEL`/chave no `.env.<ambiente>` e reinicie.
2. Se o processo estiver em crash loop, capture o traceback e trate como incidente de código.
3. Se a API estiver viva mas não coletada, verifique o alvo em `observability/prometheus/prometheus.yml`
   (`host.docker.internal:8000`) e a rede do compose.

---

## HighHTTP5xxRate

**Significa** Mais de 5% das requisições HTTP retornaram 5xx por 10 min — falha sistêmica, não um
usuário azarado.

**Onde olhar**
- Grafana → LLM Observability e o painel de requisições; identifique o `endpoint` afetado.
- Logs estruturados: procure `data_stream_failed`, `validation_error`, exceções não tratadas.

**O que fazer**
1. Se concentrado em `/data-agent/*`: veja também `HighAgentTurnErrorRate` — a causa provável é o
   provedor de LLM ou o banco.
2. Se espalhado: cheque banco (`DatabaseConnectionsSaturated`) e memória/CPU do host (cAdvisor).
3. Rollback do último deploy é a mitigação padrão se o início coincide com uma publicação.

---

## RecursionBackstopFired

**Significa** Um turno terminou pelo backstop físico de recursão. **Este alerta deveria ser
impossível**: `compute_recursion_limit()` (`src/app/agents/data_agent/turn_limits.py`) deriva o
limite do grafo compilado justamente para o cap gracioso de chamadas disparar primeiro.

**Onde olhar**
- Log `turn_recursion_backstop_hit` (traz `recursion_limit` e `model_calls` do turno).
- Mudou a pilha de middlewares recentemente? Cada hook `before_model`/`after_model` é um nó do grafo
  e custa um super-step por rodada.

**O que fazer**
1. Rode `uv run pytest tests/unit/test_turn_limits.py` — o teste de derivação deve falhar se o
   custo por rodada subiu além da folga.
2. Se a pilha cresceu legitimamente, a derivação já se ajusta sozinha; investigue um loop que **não
   consome chamadas de modelo** (jump loop de middleware) — esse é o cenário que o cap não cobre.
3. Nunca "resolva" aumentando o `recursion_limit` na mão: a fórmula derivada é a correção.

---

## HighAgentTurnErrorRate

**Significa** Mais de 2% dos turnos terminam em `error` — falha real (as fronteiras graciosas
reportam `call_limit`/`timeout`, não `error`).

**Onde olhar**
- Log `data_stream_failed` (traz `session_id`, `steps`, `answer_chars`) — o traceback está junto.
- Langfuse: o trace do turno mostra em qual chamada/ferramenta quebrou.

**O que fazer**
1. Erro do provedor (401/429/5xx) → veja `LLMProviderErrorsSpiking`.
2. Erro de banco → veja `DatabaseConnectionsSaturated`.
3. Erro de ferramenta específico e recorrente → trate como bug e adicione um caso golden que o
   reproduza (`evals/golden_set.json`).

---

## HighAgentTurnTimeoutRate

**Significa** Mais de 10% dos turnos batem no teto de tempo (`TURN_TIMEOUT_SECONDS`). O usuário
recebe a mensagem recuperável com "continuar", mas em excesso isso é fricção, não robustez.

**Onde olhar**
- Grafana → p50/p95/p99 de duração; se o p50 subiu, é lentidão geral do provedor.
- `MODEL` atual: um modelo local grande (Ollama) é ordens de magnitude mais lento que um de nuvem.

**O que fazer**
1. Provedor lento por natureza → aumente `TURN_TIMEOUT_SECONDS` conscientemente (o default 600s
   pressupõe provedor de nuvem).
2. Degradação súbita de um provedor de nuvem → cheque a status page dele.
3. Contexto inflado (turnos longos relendo arquivos) → verifique o read-ledger nos logs do turno.

---

## HighAgentTurnCallLimitRate

**Significa** Mais de 20% dos turnos param no cap de chamadas de modelo. Não é falha — é o produto
pedindo "continuar" com frequência demais.

**Onde olhar**
- Log `data_turn_summary` (`model_calls`, `deliverable_called`, `incomplete`).
- A mistura de terminações no dashboard: `call_limit` crescendo em relação a `completed`.

**O que fazer**
1. Se os turnos legítimos precisam de mais passos → aumente `MODEL_CALL_LIMIT` (o recursion limit
   se recalibra sozinho, é derivado).
2. Se o modelo está **repetindo** ferramentas sem progredir → é qualidade de modelo/prompt: rode
   `make eval-golden-live` e compare com o baseline antes de mexer no limite.

---

## AgentTurnLatencySLOBreach

**Significa** O p95 dos turnos concluídos passou de 120s por 30 min.

**Onde olhar**
- Grafana → p50/p95/p99; compare com `llm_inference_duration_seconds` (latência por chamada) para
  separar "provedor lento" de "muitas rodadas por turno".
- `tool_executions_total` — ferramentas lentas (ingestão de PDF grande, SQL pesado) inflam o turno.

**O que fazer**
1. Latência por chamada alta → provedor; considere um modelo mais rápido ou `UTILITY_MODEL` para
   sub-fluxos.
2. Muitas rodadas por turno → prompt/planejamento; caso golden novo para fixar a expectativa.
3. Ferramenta específica lenta → otimize ou mova para subagente com resultado destilado.

---

## TokenBudgetsExhaustingFrequently

**Significa** Muitos turnos estão sendo recusados por orçamento diário (`TOKEN_BUDGET_DAILY`). Não é
falha — é política apertada demais ou uso anômalo.

**Onde olhar**
- `GET /me/usage` do usuário afetado (ou a tabela `tokenusage`: uma linha por usuário/dia com
  `turns` e totais) — muitos turnos pequenos e um único turno gigante pedem respostas diferentes.
- Log `token_budget_exhausted` traz `user_id`, `used` e `limit`.

**O que fazer**
1. Uso legítimo batendo no teto → suba `TOKEN_BUDGET_DAILY`, ou dê override na conta
   (`user.token_budget_daily`; `0` = ilimitado para aquele usuário).
2. Uma conta destoando das demais → investigue abuso antes de subir o limite global.
3. Consumo alto por turno (poucos `turns`, muitos tokens) → é contexto inflado, não volume:
   veja `AgentTurnLatencySLOBreach` e o read-ledger do turno.

---

## LLMProviderErrorsSpiking

**Significa** O provedor está devolvendo erro em volume (`llm_errors`).

**Onde olhar**
- Mensagem do erro nos logs: 401 (chave), 429 (rate limit), 5xx (provedor), timeout (rede).

**O que fazer**
1. 401 → chave expirada/rotacionada: atualize o segredo e reinicie.
2. 429 → reduza concorrência (rate limit por usuário em `RATE_LIMIT_ENDPOINTS`) ou suba o tier.
3. 5xx persistente → considere trocar `MODEL` temporariamente (a fábrica é multi-provedor: um
   `openai:`/`ollama:` de contingência sobe sem mudança de código).

---

## DatabaseConnectionsSaturated

**Significa** O pool de conexões está perto do limite (`POSTGRES_POOL_SIZE` + `MAX_OVERFLOW`).
Turnos em streaming seguram conexões por minutos, então o pool satura muito antes da CPU.

**Onde olhar**
- Painel "Conexões Postgres ativas"; correlacione com o número de turnos simultâneos.
- No banco: `SELECT count(*), state FROM pg_stat_activity GROUP BY state;`

**O que fazer**
1. Aumente `POSTGRES_POOL_SIZE`/`POSTGRES_MAX_OVERFLOW` se o hardware do banco aguenta.
2. Verifique conexões ociosas em transação (`idle in transaction`) — indicam sessão não fechada no
   código.
3. Em pico sustentado, o teto real de usuários simultâneos é este número: registre-o como limite
   conhecido de capacidade.
