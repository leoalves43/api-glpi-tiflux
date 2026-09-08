"""
glpi_tiflux.py
Ponto de entrada usado pelo cron. A lógica vive no pacote `sync/` —
ver docs/ARCHITECTURE.md para o mapa dos módulos.
"""

from sync.main import main

if __name__ == "__main__":
    main()
