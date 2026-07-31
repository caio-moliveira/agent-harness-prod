# Operação

Backup, retenção, capacidade e sondas de saúde. Complementa [`runbooks.md`](runbooks.md) (o
que fazer quando um alerta dispara) com o que fazer **antes** de qualquer alerta disparar.

> Docs vizinhos: [`runbooks.md`](runbooks.md) (resposta a alerta) ·
> [`security.md`](security.md) (guardrails, segredos, LGPD).

---

## Sondas de saúde: liveness ≠ readiness

Três endpoints, com papéis distintos — usar o errado no orquestrador causa dano ativo:

| Endpoint | Pergunta | Uso |
|---|---|---|
| `GET /api/v1/health/live` | O processo está vivo? | **liveness probe** — nunca toca dependências |
| `GET /api/v1/health/ready` | Dá para mandar tráfego? (banco incluso) | **readiness probe** — 503 tira do balanceador |
| `GET /api/v1/health` | Visão completa (versão, ambiente, componentes) | dashboards e diagnóstico humano |

**Por que separar**: se a liveness probe verificasse o banco, uma indisponibilidade momentânea do
Postgres faria o orquestrador **reiniciar** processos saudáveis — transformando uma falha
recuperável num crash loop. A readiness tira do balanceador e deixa o processo vivo para se
recuperar sozinho.

> Correção relevante (#75): antes, o health check chamava um método `async` **sem `await`** — a
> coroutine nunca executava, o banco nunca era consultado e o endpoint respondia `healthy` mesmo com
> o Postgres fora. Uma sonda que não pode falhar é pior que sonda nenhuma.

Exemplo (Kubernetes):

```yaml
livenessProbe:
  httpGet: { path: /api/v1/health/live, port: 8000 }
  periodSeconds: 30
readinessProbe:
  httpGet: { path: /api/v1/health/ready, port: 8000 }
  periodSeconds: 10
  failureThreshold: 3
```

---

## Backup e restore

```bash
make backup                                # dump comprimido em ./backups (ou $BACKUP_DIR)
make restore-drill dump=backups/x.dump     # ensaio em banco descartável (<POSTGRES_DB>_restore_check)
./scripts/restore.sh backups/x.dump        # restauração REAL (sobre o banco configurado) — sem atalho de make
```

Os dois primeiros são atalhos para `scripts/backup.sh` e `scripts/restore.sh --into`. A restauração
real não tem alvo de `make` de propósito: sobrescrever o banco vivo deve custar um comando escrito à
mão.

**Formato `custom` (-Fc)** de propósito: permite restaurar seletivamente (uma tabela, um schema) e
já vem comprimido. O backup falha explicitamente se o dump sair menor que 1 KB — melhor um erro
alto que um arquivo inútil marcado como sucesso.

**O que NÃO está no dump**: os artefatos gerados (`ARTIFACT_STORAGE_ROOT`). São arquivos em volume
— inclua esse caminho na rotina de backup de volumes, ou os downloads aprovados somem numa
restauração.

**Ensaio de restauração** — o único jeito de saber que o backup presta. Trimestral, em banco
descartável (`--into`), conferindo contagens:

```sql
select 'user' t, count(*) from "user"
union all select 'session', count(*) from session
union all select 'chatmessage', count(*) from chatmessage;
```

| Data | Dump | Resultado |
|---|---|---|
| 2026-07-31 | `mydb-20260731T192544Z.dump` (1,4 MB) | ✅ restaurado em banco novo: 4 usuários, 109 sessões, 344 mensagens, 3 linhas de uso |

Registre cada ensaio nessa tabela. Um backup sem ensaio recente é esperança, não backup.

---

## Retenção e exclusão (LGPD)

Dois comandos com propósitos diferentes — não confunda:

```bash
make purge                                                     # = retention purge (janelas de retenção)
uv run python -m src.cli.retention erase --user 42             # direito de exclusão
uv run python -m src.cli.retention erase --user 42 --keep-account   # apaga histórico, mantém login
```

**Retenção** (`purge`) é higiene por idade, configurada em `.env`:

| Variável | Default | O que remove |
|---|---|---|
| `RETENTION_MESSAGES_DAYS` | `0` (nunca) | conversas inteiras (sessão + mensagens + steps + eventos + artefatos) |
| `RETENTION_EVENTS_DAYS` | `0` (nunca) | log de auditoria avulso |
| `RETENTION_USAGE_DAYS` | `400` | contadores diários de tokens |
| `RETENTION_ARTIFACTS_DAYS` | `30` | diretórios de artefato órfãos no volume |

Os defaults de conversa são **desligados** de propósito: apagar o histórico de um usuário por causa
de um default que ninguém escolheu é pior que uma tabela grande. Ligue conscientemente.

Conversas são removidas **inteiras**, nunca aparadas: uma sessão sem mensagens apareceria na barra
lateral como uma conversa vazia e inexplicável.

**Exclusão** (`erase`) atende a um pedido do titular: remove sessões (com mensagens, steps, eventos,
ações pendentes, artefatos e a thread de checkpoint), memórias, uso e — salvo `--keep-account` — a
própria conta. Coberto por testes de integração (`tests/integration/test_retention.py`) justamente
porque "conseguimos apagar seus dados" precisa ser fato verificado. A política de dados pessoais que
justifica isso está em [`security.md`](security.md#dados-pessoais-lgpd).

---

## Capacidade

```bash
make load-test USERS=20 TURNS=3        # = python -m tests.load.streaming_load --users 20 --turns 3
```

Sobe a API real com o mock LLM (zero tokens) e mede p50/p95/p99 por turno. Medição em
**2026-07-31**, um worker uvicorn no container de desenvolvimento:

| Usuários simultâneos | Turnos concluídos | Falhas | Throughput | p50 | p95 |
|---|---|---|---|---|---|
| 10 | 20 | 0 | 2,05 turnos/s | 4,8 s | 7,6 s |
| 40 | 80 | 0 | 2,06 turnos/s | 19,0 s | 30,0 s |

**Leitura honesta desses números**: o throughput **não muda** de 10 para 40 usuários, e a latência
cresce proporcionalmente — o sistema está saturado e os pedidos excedentes **enfileiram**. O ponto
importante é que ele degrada por fila, com **zero falhas**, em vez de estourar erros.

O gargalo aqui é o **worker único** (o grafo do agente é trabalho de CPU no processo), não o pool
do Postgres: 40 turnos simultâneos ultrapassam o pool default (20+10) sem nenhuma falha de conexão.
Para escalar: mais workers/réplicas primeiro; só depois mexa em `POSTGRES_POOL_SIZE`. E refaça a
medição no hardware de produção — este número vale para este container, não para o seu deploy.

---

## Armazenamento de artefatos

`ARTIFACT_STORAGE_ROOT` (default `./data/artifacts`) guarda os entregáveis aprovados quando a pasta
concedida é somente-leitura. **Nunca aponte para `/tmp` em produção**: em container isso é apagado
no restart e o download de um artefato aprovado passa a dar 404 sem explicação. Monte em volume
(veja `docker-compose.yml`) e inclua no backup de volumes.
