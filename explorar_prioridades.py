import requests

credenciais = {}
with open("credenciais.txt", "r") as arquivo:
    for linha in arquivo:
        if "=" in linha:
            chave, valor = linha.split("=", 1)
            credenciais[chave.strip()] = valor.strip()

URL_TIFLUX = credenciais.get("URL_TIFLUX")
TOKEN_TIFLUX = credenciais.get("TOKEN_TIFLUX")

headers = {
    "accept": "application/json",
    "Authorization": f"Bearer {TOKEN_TIFLUX}"
}

print("Buscando prioridades e detalhes da Mesa de Arrecadação (37964)...\n")

# Consultando a rota de prioridades/desks do TiFlux
url = f"{URL_TIFLUX}/desks/37963"
resposta = requests.get(url, headers=headers)

if resposta.status_code != 200:
    print(f"Rota por mesa retornou {resposta.status_code}. Tentando rota global de prioridades...")
    url = f"{URL_TIFLUX}/priorities"
    resposta = requests.get(url, headers=headers)

print(f"Status Code: {resposta.status_code}")
print("Resposta da API TiFlux:")
print(resposta.text)