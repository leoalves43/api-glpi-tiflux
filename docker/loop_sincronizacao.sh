#!/bin/sh
# Substitui o Agendador de Tarefas do Windows: roda uma sincronização e só
# então dorme — execuções nunca se sobrepõem (não há lock entre elas).
#
# SIGTERM (docker compose stop/down, restart do Docker Desktop) NÃO interrompe
# uma execução em andamento: matar no meio pode postar um followup sem gravar
# a auditoria, e ele seria repostado na execução seguinte. O sinal só marca a
# parada; o loop sai quando a execução atual termina.
INTERVALO="${INTERVALO_SEGUNDOS:-300}"
PARAR=0
trap 'PARAR=1' TERM INT

aguardar_processo() {
    # `wait` retorna cedo quando um sinal chega; espera de novo até o PID sumir.
    while kill -0 "$1" 2>/dev/null; do
        wait "$1"
    done
}

while [ "$PARAR" -eq 0 ]; do
    # Python fora do grupo de sinais do shell: o TERM do Docker vai só pro PID 1.
    python glpi_tiflux.py &
    aguardar_processo $!
    [ "$PARAR" -eq 1 ] && break

    sleep "$INTERVALO" &
    PID_SLEEP=$!
    wait "$PID_SLEEP"
    kill "$PID_SLEEP" 2>/dev/null
done

echo "Parada solicitada — encerrando após a última execução."
