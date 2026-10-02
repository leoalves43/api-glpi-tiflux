# tiflux-glpi-sync

Sincronização automática de chamados entre **GLPI** e **Tiflux**, com
auditoria em Postgres. A cada execução:

1. **Criação de chamados, GLPI -> Tiflux.** Sonda os chamados novos do GLPI a
   partir do maior `id_glpi` já sincronizado e cria o equivalente no Tiflux
   (técnico, prioridade, anexos, campos obrigatórios).
2. **Followups, nos dois sentidos**, para chamados já sincronizados e ainda
   abertos.

O estado fica em duas tabelas Postgres (uma por chamado, uma por followup),
que também evitam o eco entre as duas direções. Antes de mexer na lógica, leia
`docs/ARCHITECTURE.md`.

## Configuração

Copie `exemplo.env` para `.env` na raiz do projeto e preencha:

```
URL_GLPI=https://.../apirest.php
APP_TOKEN=
USER_TOKEN=

URL_TIFLUX=https://api.tiflux.com/api/v2
TOKEN_TIFLUX=

DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_SCHEMA=
DB_TABLE=api_glpi_tiflux
```

O `.env` nunca é commitado nem entra na imagem Docker. Variáveis de ambiente
sobrescrevem o `.env`, então o mesmo arquivo serve com e sem Docker.

Parâmetros de negócio (janela de sondagem, grupos observadores, IDs de
técnico/campo/mesa) ficam em `sync/config.py:Config`.

## Banco de dados

Crie as tabelas de auditoria antes da primeira execução:

```bash
psql -f criar_tabela_auditoria.sql
```

Ao migrar um banco existente, use `pg_dump` (estrutura + dados). A gravação
depende das constraints únicas `id_glpi` e `(direcao, id_origem)`; se elas se
perderam, rode `scripts/restaurar_constraints_auditoria.sql` (ajuste o schema
se não for `tiflux_glpi_sync`).

Nunca rode a sincronização com as tabelas **vazias** em um ambiente que já
sincronizava: a sondagem recomeçaria do início e duplicaria chamados.

## Rodando com Docker

Requisitos: Docker com Compose (Docker Desktop no Windows), Postgres acessível
a partir do container e acesso de rede ao GLPI e ao Tiflux.

```bash
docker compose up -d --build     # builda e sobe; reinicia sozinho
docker compose logs -f           # acompanha os logs (horário de America/Sao_Paulo)
docker compose stop              # para, esperando a execução em andamento terminar
docker compose down              # para e remove o container
```

O container roda a sincronização, espera `INTERVALO_SEGUNDOS` (padrão 300,
no `docker-compose.yml`) e repete. Uma execução nunca começa antes da anterior
terminar, e um `stop` espera até 10 minutos para a execução em andamento
acabar.

O container usa o `DB_HOST` do `.env` (Postgres em servidor remoto). Não use
`localhost` ali: dentro do container, `localhost` é o próprio container. Para
um Postgres na própria máquina, use `DB_HOST=host.docker.internal`.

Para o container voltar sozinho após reiniciar a máquina, ative *Start Docker
Desktop when you sign in* no Docker Desktop.

Antecipar a próxima execução sem reiniciar o container (Windows/PowerShell;
não faz nada se já houver uma execução em andamento):

```powershell
.\scripts\forcar_sincronizacao.ps1
```

Forçar a sincronização de um chamado específico que ficou fora da sondagem:

```bash
docker compose run --rm sync python -m sync.forcar_sincronizacao --id-glpi 33769
```

Testes, no mesmo ambiente de produção:

```bash
docker compose run --rm --no-deps sync python -m unittest discover -s tests -t .
```

## Rodando sem Docker

Requisitos: Python 3.10+, Postgres e acesso de rede ao GLPI e ao Tiflux. No
`.env`, use o host real do banco.

```bash
pip install -r requirements.txt
python glpi_tiflux.py                                    # uma execução
python -m sync.forcar_sincronizacao --id-glpi 33769      # um chamado específico
python -m unittest discover -s tests -t .                # testes
```

`glpi_tiflux.py` roda uma vez e termina. Para repetir, agende-o (Agendador de
Tarefas do Windows, cron) sem permitir execuções sobrepostas.

**Não rode sem Docker enquanto o container estiver ligado contra o mesmo
banco.** Duas sincronizações simultâneas duplicam chamados e followups no
Tiflux.
