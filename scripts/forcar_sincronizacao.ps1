# Força uma sincronização agora, sem reiniciar o container.
#
# Mata o `sleep` do docker/loop_sincronizacao.sh; o `wait` do loop retorna e a
# próxima execução começa na hora. Se uma sincronização já estiver rodando, não
# há `sleep` e nada acontece — nunca roda duas em paralelo (o loop não tem lock).
#
# Uso: .\scripts\forcar_sincronizacao.ps1
#
# Sem aspas duplas dentro do `sh -c`: o PowerShell 5.1 as corrompe ao passar
# argumentos para executáveis nativos. A imagem slim não tem pkill, daí o /proc.
$Container = 'tiflux-glpi-sync'
$AcordarLoop = 'for p in /proc/[0-9]*; do read c < $p/comm; [ x$c = xsleep ] && kill ${p#/proc/} && echo acordado; done 2>/dev/null; true'

$Resultado = docker exec $Container sh -c $AcordarLoop
if ($LASTEXITCODE -ne 0) {
    Write-Error "Falha no docker exec (código $LASTEXITCODE). O container '$Container' está rodando? Veja: docker ps"
    exit 1
}

if ($Resultado -match 'acordado') {
    Write-Host 'Sincronização iniciada. Acompanhe com: docker logs -f tiflux-glpi-sync'
    exit 0
}
Write-Host 'Já há uma sincronização em andamento; nada a fazer.'
