# tiflux-glpi-sync

Sincronização automática de chamados entre **GLPI** (REST, sessão por token) e
**Tiflux** (REST, bearer), com auditoria em Postgres. Roda em um container
Docker que repete a sincronização a cada 5 minutos, sem framework.

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

- Docker com Compose (Docker Desktop no Windows)
- Postgres acessível a partir do container, com as duas tabelas de auditoria
- Acesso de rede às APIs do GLPI e do Tiflux

## Configuração

Copie `exemplo.env` para `.env` na raiz do projeto e preencha:

```
URL_GLPI=https://.../apirest.php
APP_TOKEN=
USER_TOKEN=

URL_TIFLUX=https://api.tiflux.com/api/v2
TOKEN_TIFLUX=

DB_HOST=localhost
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_SCHEMA=
DB_TABLE=api_glpi_tiflux
```

`.env` está no `.gitignore` e no `.dockerignore`: nunca é commitado nem entra
na imagem. O compose monta o arquivo em `/app/.env` somente leitura.

**Variáveis de ambiente sobrescrevem o `.env`.** Com isso, o mesmo `.env`
serve dentro e fora do Docker. O `docker-compose.yml` troca `DB_HOST` por
`host.docker.internal`, porque dentro do container `localhost` é o próprio
container. Assim ele alcança o Postgres publicado na porta 5432 do host (por
exemplo, outro container). Se o Postgres estiver em outra máquina, ajuste o
`DB_HOST` no compose.

Demais parâmetros de negócio (janela de sondagem, grupos observadores, IDs de
técnico/campo/mesa etc.) ficam em `sync/config.py:Config`. Cada campo tem um
comentário explicando o motivo do valor.

### Banco de dados

Crie as tabelas de auditoria antes da primeira execução:

```bash
psql -f criar_tabela_auditoria.sql
```

(colunas e chaves de conflito documentadas em `docs/data/audit_tables.toon`).

Ao **migrar** um banco existente, leve as constraints junto (use `pg_dump`,
não uma cópia só de dados). A gravação usa `ON CONFLICT` e depende das
constraints únicas `id_glpi` e `(direcao, id_origem)`. Se a migração perdeu
a estrutura, rode `scripts/restaurar_constraints_auditoria.sql`. Ele usa o
schema `tiflux_glpi_sync`; ajuste se o seu for outro.

Nunca suba o container com as tabelas **vazias** em um ambiente que já
sincronizava. A sondagem recomeçaria em `id_minimo_glpi`.

## Uso

```bash
docker compose up -d --build     # builda e sobe; reinicia sozinho (restart: unless-stopped)
docker compose logs -f           # acompanha os logs (horário de America/Sao_Paulo)
docker compose stop              # para, esperando a execução em andamento terminar
docker compose down              # para e remove o container
```

O container executa `docker/loop_sincronizacao.sh`. O script roda
`glpi_tiflux.py`, espera `INTERVALO_SEGUNDOS` (padrão 300, definido no
compose) e repete. Uma execução nunca começa antes da anterior terminar. Um
`stop` não interrompe uma execução no meio: o container espera até 10 minutos
(`stop_grace_period`) para ela acabar.

Para rodar sozinho após reiniciar a máquina, ative *Start Docker Desktop when
you sign in* no Docker Desktop.

Forçar a sincronização de um chamado específico que ficou fora da sondagem
automática (usado pela interface web `interface-web-api-glpi-tiflux`):

```bash
docker compose run --rm sync python -m sync.forcar_sincronizacao --id-glpi 33769
```

### Sem Docker

Com Python 3.10+ e o `.env` apontando para o banco (`DB_HOST=localhost`):

```bash
pip install -r requirements.txt
python glpi_tiflux.py
python -m sync.forcar_sincronizacao --id-glpi 33769
```

Não rode isso em paralelo com o container contra o mesmo banco. Duas
execuções simultâneas duplicam tickets no Tiflux.

## Testes

Dentro da imagem (mesmo ambiente de produção):

```bash
docker compose run --rm --no-deps sync python -m unittest discover -s tests -t .
```

Ou localmente: `python -m unittest discover -s tests -t .` (ou `pytest`).

E/S externa (GLPI, Tiflux, Postgres) é sempre mockada com fakes nomeados em
`tests/fakes.py` / `tests/fake_clients.py`. Os testes não fazem chamadas de
rede reais.

## Estrutura

```
glpi_tiflux.py          # entrypoint (shim, não mexer no nome)
sync/                   # toda a lógica — ver docs/ARCHITECTURE.md
tests/                  # um teste por módulo em sync/
Dockerfile              # imagem Python 3.13-slim + tzdata, usuário sem root
docker-compose.yml      # serviço `sync`: .env montado, DB_HOST, TZ, intervalo
docker/                 # loop_sincronizacao.sh (agendamento + parada segura)
scripts/                # SQL de manutenção do banco
docs/                   # ver docs/INDEX.md
```

## Documentação

Comece por `docs/INDEX.md`. Não leia `docs/` inteiro: cada doc lá diz
quando deve ser lido.

- `docs/ARCHITECTURE.md`: módulos, fluxo de dados, APIs externas, bug conhecido
- `docs/state/HANDOFF.md`: status atual, próximos passos, riscos abertos
- `docs/decisions/LOG.md`: por que cada decisão não óbvia foi tomada
- `docs/data/audit_tables.toon`: schema das tabelas de auditoria
- `docs/specs/001-docker.md` e `docs/plans/001-docker.md`: migração para Docker
