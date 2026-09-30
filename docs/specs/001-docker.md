# 001 — Rodar a sincronização em container Docker

## Intenção
Executar a sincronização GLPI <-> Tiflux em um container, a cada 5 minutos,
usando o Postgres que já roda no container `Postgres` (porta 5432 publicada no host).

## Resultados para o usuário
- `docker compose up -d --build` sobe a sincronização; ela roda sozinha a cada 5 min.
- `docker compose logs -f` mostra os logs em tempo real, com horário de Brasília.
- `.env` continua sendo a única fonte de credenciais e **não** entra na imagem.
- Execução manual continua possível: `docker compose run --rm sync python -m sync.forcar_sincronizacao --id-glpi N`.

## Restrições
- `.env` continua servindo para rodar fora do Docker (`DB_HOST=localhost`).
- Duas execuções nunca se sobrepõem dentro do container.
- Não mexer no container do Postgres.

## Critérios de aceite
1. Variável de ambiente sobrescreve a chave de mesmo nome do `.env` (teste automatizado).
2. A imagem builda e `pytest` passa dentro dela.
3. De dentro do container: `SELECT 1` no Postgres funciona e o GLPI responde a um GET.
4. `date` dentro do container mostra o horário de America/Sao_Paulo.

## Fora do escopo
- Adaptar a interface PHP `interface-web-api-glpi-tiflux` para chamar o container.
- Colocar o Postgres no mesmo compose.
