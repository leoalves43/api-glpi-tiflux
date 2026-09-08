import requests
import urllib.parse

credenciais = {}
with open("credenciais.txt", "r") as arquivo:
    for linha in arquivo:
        if "=" in linha:
            chave, valor = linha.split("=", 1)
            credenciais[chave.strip()] = valor.strip()

URL_TIFLUX = credenciais.get("URL_TIFLUX")
TOKEN_TIFLUX = credenciais.get("TOKEN_TIFLUX")
EMAIL_TESTE = "cleusa.dias@caraguatatuba.sp.gov.br"
email_encoded = urllib.parse.quote(EMAIL_TESTE)

headers = {
    "accept": "application/json",
    "Authorization": f"Bearer {TOKEN_TIFLUX}"
}

print(f"🔍 TESTANDO ROTAS GLOBAIS DA API V2 PARA: {EMAIL_TESTE}\n")

# TESTE 1: GET /users?email=...
url1 = f"{URL_TIFLUX}/users?email={email_encoded}"
r1 = requests.get(url1, headers=headers)
print("=" * 70)
print(f"TESTE 1: GET {url1}")
print(f"Status: {r1.status_code}")
print(f"Resposta: {r1.text}")

# TESTE 2: GET /requestors?query=...
url2 = f"{URL_TIFLUX}/requestors?query={email_encoded}"
r2 = requests.get(url2, headers=headers)
print("\n" + "=" * 70)
print(f"TESTE 2: GET {url2}")
print(f"Status: {r2.status_code}")
print(f"Resposta: {r2.text}")
print("=" * 70)