"""
diagnostico_glpi.py
Roda uma checagem direta (GET /Ticket/{id}) em uma lista de IDs, sem passar
pelo endpoint de busca — pra confirmar se o chamado existe e é visível pra
esse usuário/token da API, e mostrar a entidade dele.
"""

from glpi_tiflux import URL_BASE, APP_TOKEN, USER_TOKEN
import requests

IDS_PARA_CHECAR = [33636, 33637, 33658]


def main():
    resp = requests.get(
        f"{URL_BASE}/initSession",
        headers={"App-Token": APP_TOKEN, "Authorization": f"user_token {USER_TOKEN}"},
    )
    resp.raise_for_status()
    session_token = resp.json().get("session_token")
    headers = {"App-Token": APP_TOKEN, "Session-Token": session_token}

    # Mostra a(s) entidade(s) que essa sessão/usuário enxerga
    resp_sess = requests.get(f"{URL_BASE}/getFullSession", headers=headers)
    if resp_sess.status_code == 200:
        sess = resp_sess.json().get("session", {})
        print(f"Entidade ativa da sessão: {sess.get('glpiactive_entity_name')} "
              f"(ID {sess.get('glpiactive_entity')}) | Recursivo: {sess.get('glpiactive_entity_recursive')}")
    print()

    for id_chamado in IDS_PARA_CHECAR:
        resp_t = requests.get(f"{URL_BASE}/Ticket/{id_chamado}", headers=headers)
        print(f"--- Chamado #{id_chamado} ---")
        print(f"Status HTTP: {resp_t.status_code}")
        if resp_t.status_code in (200, 206):
            t = resp_t.json()
            print(f"  name: {t.get('name')}")
            print(f"  entities_id: {t.get('entities_id')}")
            print(f"  is_deleted: {t.get('is_deleted')}")
            print(f"  status: {t.get('status')}")
        else:
            print(f"  Resposta: {resp_t.text}")
        print()

    requests.get(f"{URL_BASE}/killSession", headers=headers)


if __name__ == "__main__":
    main()
