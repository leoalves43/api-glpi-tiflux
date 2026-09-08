"""
listar_config_tiflux.py
Utilitário (rodar manualmente, não faz parte do cron) para:
  1. Listar as mesas (desks) que estão de fato vinculadas ao cliente no Tiflux.
  2. Listar as prioridades cadastradas em cada uma dessas mesas.

Use a saída pra:
  - Conferir se os IDs de mesa usados em depara_categoria() (glpi_tiflux.py)
    realmente pertencem ao cliente CLIENTE_TIFLUX_ID.
  - Preencher o dicionário PRIORIDADE_POR_MESA em glpi_tiflux.py com os IDs
    corretos de prioridade de cada mesa.
"""

import requests

from glpi_tiflux import CRED, CLIENTE_TIFLUX_ID, headers_tiflux_get, URL_TIFLUX  # reaproveita config

def main():
    print(f"Cliente Tiflux: {CLIENTE_TIFLUX_ID}\n")

    resp = requests.get(f"{URL_TIFLUX}/clients/{CLIENTE_TIFLUX_ID}/desks", headers=headers_tiflux_get)
    if resp.status_code != 200:
        print(f"❌ Erro ao listar mesas do cliente: {resp.status_code} {resp.text}")
        return

    mesas = resp.json()
    if not mesas:
        print("⚠️  Esse cliente não tem NENHUMA mesa vinculada no Tiflux. "
              "É necessário associar as mesas em: Clientes > [seu cliente] > Mesas.")
        return

    for mesa in mesas:
        mesa_id = mesa.get("id")
        nome_mesa = mesa.get("name") or mesa.get("display_name")
        print("=" * 70)
        print(f"MESA: {nome_mesa}  (ID: {mesa_id})")

        resp_pri = requests.get(f"{URL_TIFLUX}/desks/{mesa_id}/priorities", headers=headers_tiflux_get)
        if resp_pri.status_code != 200:
            print(f"  ❌ Erro ao buscar prioridades: {resp_pri.status_code} {resp_pri.text}")
            continue

        prioridades = resp_pri.json()
        if not prioridades:
            print("  ⚠️  Nenhuma prioridade cadastrada nessa mesa.")
            continue

        for p in prioridades:
            print(f"  - ID {p.get('id')}: {p.get('name')}")

    print("=" * 70)
    print("\nCopie os IDs de mesa acima e confirme que batem com depara_categoria() em glpi_tiflux.py.")
    print("Copie os IDs de prioridade e preencha o dicionário PRIORIDADE_POR_MESA em glpi_tiflux.py.")


if __name__ == "__main__":
    main()
