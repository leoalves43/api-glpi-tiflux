import requests
import urllib.parse

# 1. TOKENS E CREDENCIAIS
credenciais = {}
with open("credenciais.txt", "r") as arquivo:
    for linha in arquivo:
        if "=" in linha:
            chave, valor = linha.split("=", 1)
            credenciais[chave.strip()] = valor.strip()

URL_BASE = credenciais.get("URL_GLPI")
APP_TOKEN = credenciais.get("APP_TOKEN")
USER_TOKEN = credenciais.get("USER_TOKEN")

URL_TIFLUX = credenciais.get("URL_TIFLUX")
TOKEN_TIFLUX = credenciais.get("TOKEN_TIFLUX")

CLIENTE_TIFLUX_ID = 762707
ID_SOLICITANTE_PADRAO = 3758056  # Ju STII
PRIORITY_ID_PADRAO = 120549      # Prioridade padrão para todas as mesas

ID_TECNICO_LEO = 117180
ID_TECNICO_SANIA = 1019979

headers_tiflux_json = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN_TIFLUX}"
}

# FUNÇÃO PARA CADASTRAR SOLICITANTE (REQUESTOR) NO TIFLUX CASO NÃO EXISTA
def cadastrar_solicitante_tiflux(nome, email):
    url_criar = f"{URL_TIFLUX}/clients/{CLIENTE_TIFLUX_ID}/requestors"
    payload = {
        "name": nome if nome != "Desconhecido" else "Solicitante Sem Nome",
        "email": email,
        "can_open_ticket": True
    }

    resp = requests.post(url_criar, json=payload, headers=headers_tiflux_json)
    if resp.status_code in [200, 201]:
        dados = resp.json()
        # A criação de requestor retorna o objeto direto na raiz (sem envelope "user")
        return dados.get("id"), f"{dados.get('name')} (Cadastrado Automaticamente via API)"

    print(f"⚠️ Falha ao cadastrar solicitante ({resp.status_code}): {resp.text}")
    return ID_SOLICITANTE_PADRAO, "Ju STII (Padrão - Falha ao Auto-Cadastrar no TiFlux)"

# BUSCAR SOLICITANTE (REQUESTOR) POR E-MAIL, RESTRITO AO CLIENTE
def obter_id_solicitante_tiflux(nome_glpi, email_glpi):
    if not email_glpi:
        return ID_SOLICITANTE_PADRAO, "Ju STII (Padrão - Sem E-mail no GLPI)"

    email_limpo = email_glpi.strip().lower()
    email_encoded = urllib.parse.quote(email_limpo)

    # Rota correta: /requestors (não /users). O filtro "email" é por
    # correspondência parcial ("contém"), então ainda validamos o valor exato abaixo.
    url_busca = f"{URL_TIFLUX}/clients/{CLIENTE_TIFLUX_ID}/requestors?email={email_encoded}"
    resposta = requests.get(url_busca, headers=headers_tiflux_json)

    if resposta.status_code == 200:
        dados = resposta.json()

        if isinstance(dados, list) and len(dados) > 0:
            for item in dados:
                if str(item.get("email", "")).strip().lower() == email_limpo:
                    return item.get("id"), f"{item.get('name')} (Existente no TiFlux - ID: {item.get('id')})"
            # Nenhum bateu exatamente: não assume o primeiro da lista (pode ser
            # outro solicitante cujo e-mail apenas contém o termo buscado)
    elif resposta.status_code != 404:
        print(f"⚠️ Busca de solicitante retornou status inesperado ({resposta.status_code}): {resposta.text}")

    # Fallback apenas se não encontrou correspondência exata
    return cadastrar_solicitante_tiflux(nome_glpi, email_glpi)

# 2. AUTENTICAÇÃO NO GLPI
url_login = f"{URL_BASE}/initSession"
headers_glpi_login = {
    "App-Token": APP_TOKEN,
    "Authorization": f"user_token {USER_TOKEN}"
}

resposta = requests.get(url_login, headers=headers_glpi_login)

if resposta.status_code == 200:
    session_token = resposta.json().get("session_token")
    headers_glpi = {
        "App-Token": APP_TOKEN,
        "Session-Token": session_token
    }

    # 3. ID do chamado para teste
    ID_CHAMADO_TESTE = 27514
    
    print(f"Buscando informações do chamado #{ID_CHAMADO_TESTE} no GLPI...\n")
    resp_ticket = requests.get(f"{URL_BASE}/Ticket/{ID_CHAMADO_TESTE}", headers=headers_glpi)
    
    if resp_ticket.status_code in [200, 206]:
        ticket = resp_ticket.json()

        id_glpi = ticket.get("id")
        titulo_glpi = ticket.get("name")
        descricao_glpi = ticket.get("content")
        prioridade_glpi = ticket.get("priority")
        categoria_glpi = ticket.get("itilcategories_id")
        
        # BUSCA DE REQUERENTE NO GLPI
        nome_solicitante_glpi = "Desconhecido"
        email_solicitante_glpi = None
        id_requerente = None
        
        # 1. Filtra no Ticket_User pelo Type == 1 (Requerente)
        resp_vinculos = requests.get(f"{URL_BASE}/Ticket/{ID_CHAMADO_TESTE}/Ticket_User", headers=headers_glpi)
        if resp_vinculos.status_code in [200, 206]:
            for v in resp_vinculos.json():
                if v.get("type") == 1:
                    id_requerente = v.get("users_id")
                    break

        if not id_requerente:
            id_requerente = ticket.get("users_id_recipient") or ticket.get("users_id_lastupdater")

        # 2. Pega dados e e-mails do usuário
        if id_requerente:
            resp_usuario = requests.get(f"{URL_BASE}/User/{id_requerente}", headers=headers_glpi)
            if resp_usuario.status_code in [200, 206]:
                dados_usuario = resp_usuario.json()
                p_nome = dados_usuario.get("firstname", "")
                s_nome = dados_usuario.get("realname", "")
                login = dados_usuario.get("name", "")
                
                nome_solicitante_glpi = f"{p_nome} {s_nome}".strip() if (p_nome or s_nome) else f"Login: {login}"
                email_solicitante_glpi = dados_usuario.get("email")

            # 3. Consulta rota /UserEmail se o e-mail principal for nulo
            if not email_solicitante_glpi:
                resp_email = requests.get(f"{URL_BASE}/User/{id_requerente}/UserEmail", headers=headers_glpi)
                if resp_email.status_code in [200, 206]:
                    lista_emails = resp_email.json()
                    if isinstance(lista_emails, list) and len(lista_emails) > 0:
                        email_solicitante_glpi = lista_emails[0].get("email")

        # Mapeamento de Categoria GLPI -> Mesa TiFlux
        def depara_categoria(cat_id):
            if cat_id in range(267, 272):
                return 37963  # ADMINISTRATIVO/RH
            if cat_id in range(272, 277):
                return 37964  # ARRECADAÇÃO
            if cat_id in range(277, 282):
                return 37965  # FINANÇAS
            if cat_id in range(282, 287):
                return 37966  # SUPRIMENTOS
            
            avulsos = {12: 38853, 53: 34260}
            return avulsos.get(cat_id, 34260)

        mesa_tiflux = depara_categoria(categoria_glpi)

        # Regra de Atribuição de Técnico
        def definir_tecnico(id_mesa):
            if id_mesa == 37964:  # ARRECADAÇÃO
                return ID_TECNICO_LEO, "Léo Alves"
            return ID_TECNICO_SANIA, "Sânia Almeida"

        id_tecnico_tiflux, nome_tecnico_tiflux = definir_tecnico(mesa_tiflux)

        # Resolução do Solicitante Final no TiFlux
        id_solicitante_tiflux, info_solicitante_tiflux = obter_id_solicitante_tiflux(
            nome_solicitante_glpi, 
            email_solicitante_glpi
        )

        # De/Para de Prioridades
        mapa_prioridades = {1: "Baixa", 2: "Média", 3: "Normal", 4: "Alta", 5: "Urgente"}
        prioridade_glpi_texto = mapa_prioridades.get(prioridade_glpi, "Normal")
        
        titulo_tiflux = f"{titulo_glpi} ({id_glpi})"
        
        # Formatação do corpo da descrição
        cabecalho_personalizado = f"Este chamado tem a prioridade: {prioridade_glpi_texto}"
        info_solicitante_texto = f"Solicitante: {nome_solicitante_glpi} <{email_solicitante_glpi if email_solicitante_glpi else 'Sem e-mail'}>"
        
        descricao_tiflux = f"{cabecalho_personalizado}<br><br>{info_solicitante_texto}<br><br>Descrição:<br>{descricao_glpi}"
        
        # EXIBIÇÃO DO PACOTE TRADUZIDO
        print("=" * 80)
        print(" 📦 PACOTE PRONTO PARA ENVIAR AO TIFLUX")
        print("=" * 80)
        print(f"• Cliente ID:         {CLIENTE_TIFLUX_ID}")
        print(f"• Mesa ID:            {mesa_tiflux}")
        print(f"• Técnico Resp:       {id_tecnico_tiflux} ({nome_tecnico_tiflux})")
        print(f"• Solicitante GLPI:   {nome_solicitante_glpi} <{email_solicitante_glpi}>")
        print(f"• Solicitante TiFlux: {id_solicitante_tiflux} ({info_solicitante_tiflux})")
        print(f"• Priority ID TiFlux: {PRIORITY_ID_PADRAO}")
        print(f"• Título Final:       {titulo_tiflux}")
        print("=" * 80)

        # 4. ENVIO REAL PARA A API DO TIFLUX (Etapa 1: Criar Ticket)
        headers_post_tiflux = {
            "accept": "application/json",
            "Authorization": f"Bearer {TOKEN_TIFLUX}"
        }

        form_data = {
            "title": titulo_tiflux,
            "description": descricao_tiflux,
            "client_id": str(CLIENTE_TIFLUX_ID),
            "desk_id": str(mesa_tiflux),
            "requestor_id": str(id_solicitante_tiflux),
            "priority_id": str(PRIORITY_ID_PADRAO)
        }

        print("\n🚀 Disparando criação do ticket no TiFlux...")
        url_tickets = f"{URL_TIFLUX}/tickets"
        
        resp_criacao = requests.post(url_tickets, data=form_data, headers=headers_post_tiflux)

        print(f"Status Code da Criação: {resp_criacao.status_code}")
        
        if resp_criacao.status_code in [200, 201]:
            dados_retorno = resp_criacao.json()
            
            ticket_number_tiflux = None
            if isinstance(dados_retorno, dict) and "ticket" in dados_retorno:
                ticket_number_tiflux = dados_retorno["ticket"].get("ticket_number")

            print(f"✅ Ticket criado com sucesso! Código do chamado no TiFlux: #{ticket_number_tiflux}")
            
            # 5. ATRIBUINDO O TÉCNICO (Etapa 2: Rota de Responsável)
            if ticket_number_tiflux:
                print(f"👤 Atribuindo técnico {nome_tecnico_tiflux} (ID: {id_tecnico_tiflux}) ao chamado #{ticket_number_tiflux}...")
                
                url_alterar_responsavel = f"{URL_TIFLUX}/tickets/{ticket_number_tiflux}/change_responsible"
                payload_resp = {"responsible_id": id_tecnico_tiflux}
                
                resp_update = requests.post(url_alterar_responsavel, json=payload_resp, headers=headers_tiflux_json)
                
                if resp_update.status_code not in [200, 201, 204]:
                    url_put = f"{URL_TIFLUX}/tickets/{ticket_number_tiflux}"
                    payload_put = {"ticket": {"responsible_id": id_tecnico_tiflux}}
                    resp_update = requests.put(url_put, json=payload_put, headers=headers_tiflux_json)

                print(f"Status Code da Atribuição: {resp_update.status_code}")
                print("Resposta da Atribuição:")
                print(resp_update.text)
            else:
                print("⚠️ Não foi possível identificar o ticket_number na resposta do TiFlux.")
            
        else:
            print("❌ Erro ao criar ticket na API TiFlux:")
            print(resp_criacao.text)
        
    else:
        print(f"❌ Não foi possível encontrar o chamado #{ID_CHAMADO_TESTE}. Código: {resp_ticket.status_code}")
else:
    print("❌ Erro na autenticação com o GLPI.")