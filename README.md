# api-glpi-tiflux

Sincronização automática de chamados entre **GLPI** (REST, sessão por token) e
**Tiflux** (REST, bearer), com auditoria em Postgres. Roda via cron/agendador,
sem framework.

## O que faz

A cada execução (`sync/main.py:main()`), duas sincronizações independentes:

1. **Criação de chamados, GLPI -> Tiflux.** Sonda `GET /Ticket/{id}`
   sequencialmente a partir do maior `id_glpi` já confirmado, cria o
   equivalente no Tiflux (técnico, prioridade, anexos, campos obrigatórios).
2. **Followups, bidirecional**, para chamados já sincronizados e ainda
   abertos: comentários/respostas replicados nos dois sentidos
   (GLPI `ITILFollowup` <-> Tiflux `/answers` e `/internal_communications`).

Duas tabelas Postgres guardam o estado (uma por chamado, uma por followup) e
servem também como mecanismo de anti-eco entre as duas direções.

Mapa completo de módulos, fluxo de dados e decisões de design:
**leia `docs/ARCHITECTURE.md`** antes de mexer na lógica de sincronização.

## Requisitos

- Python 3.10+ (usa `X | None` e `tuple[str, ...]` em type hints)
- Postgres com as duas tabelas de auditoria (`criar_tabela_auditoria.sql`)
- Acesso de rede às APIs do GLPI e do Tiflux

## Instalação

```bash
pip install -r requirements.txt
```

## Configuração

Copie `exemplo.env` para `.env` na raiz do projeto e
preencha:

```
URL_GLPI=https://.../apirest.php
APP_TOKEN=
USER_TOKEN=

URL_TIFLUX=https://api.tiflux.com/api/v2
TOKEN_TIFLUX=

DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_SCHEMA=
DB_TABLE=api_glpi_tiflux
```

`.env` está no `.gitignore` — nunca commitar. Crie as tabelas de
auditoria antes da primeira execução:

```bash
psql -f criar_tabela_auditoria.sql
```

(colunas e chaves de conflito documentadas em `docs/data/audit_tables.toon`).

Demais parâmetros de negócio (janela de sondagem, grupos observadores, IDs de
técnico/campo/mesa etc.) ficam em `sync/config.py:Config` — cada campo tem um
comentário explicando o motivo do valor.

## Uso

Execução normal (a mesma que o agendador roda):

```bash
python glpi_tiflux.py
```

Forçar a sincronização de um chamado específico que ficou fora da sondagem
automática (usado pela interface web `interface-web-api-glpi-tiflux`):

```bash
python -m sync.forcar_sincronizacao --id-glpi 33769
```

## Testes

```bash
pytest
```

E/S externa (GLPI, Tiflux, Postgres) é sempre mockada com fakes nomeados em
`tests/fakes.py` / `tests/fake_clients.py` — sem chamadas de rede real nos
testes.

## Estrutura

```
glpi_tiflux.py   # entrypoint do cron (shim, não mexer no nome)
sync/            # toda a lógica — ver docs/ARCHITECTURE.md
tests/           # um teste por módulo em sync/
docs/            # ver docs/INDEX.md
```

## Documentação

Comece por `docs/INDEX.md`. Não leia `docs/` inteiro — cada doc lá lista
quando deve ser lido:

- `docs/ARCHITECTURE.md` — módulos, fluxo de dados, APIs externas, bug conhecido
- `docs/state/HANDOFF.md` — status atual, próximos passos, riscos abertos
- `docs/decisions/LOG.md` — por que cada decisão não óbvia foi tomada
- `docs/data/audit_tables.toon` — schema das tabelas de auditoria
