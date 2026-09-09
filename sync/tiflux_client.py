"""Cliente HTTP fino para a API REST do Tiflux usada por esta integração."""

from __future__ import annotations

import urllib.parse

import requests

from sync.config import Config, log
from sync.glpi_client import Anexo


class TifluxClient:
    """Wraps chamadas à API do Tiflux. Uma instância por execução do cron."""

    def __init__(self, url_base: str, token: str, cliente_id: int, id_solicitante_padrao: int):
        self._url_base = url_base
        self._cliente_id = cliente_id
        self._id_solicitante_padrao = id_solicitante_padrao
        self._mesas_validas_do_cliente: set[int] | None = None

        self._headers_json = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self._headers_form = {
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        # Para requisições GET sem corpo. IMPORTANTE: nunca mandar Content-Type: application/json
        # num GET sem body — o Rails (usado pelo Tiflux) faz o "param wrapping" do corpo (mesmo vazio)
        # dentro de uma chave com o nome singular do recurso (ex: "requestor"), e como a action de
        # busca (index) não espera esse parâmetro, a API responde 400 "unpermitted parameter".
        self._headers_get = {
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

    @property
    def cliente_id(self) -> int:
        return self._cliente_id

    @classmethod
    def conectar(cls, config: Config) -> "TifluxClient":
        return cls(config.url_tiflux, config.token_tiflux, config.cliente_tiflux_id, config.id_solicitante_padrao)

    def validar_mesa_do_cliente(self, id_mesa: int) -> bool:
        """
        Confere se a mesa pertence ao cliente configurado antes de tentar criar o ticket.
        A API do Tiflux só acusa isso com um erro genérico na criação do ticket
        ("This ticket's client do not belongs to the selected desk"); aqui a gente
        detecta antes e devolve uma mensagem que já indica a causa.
        """
        if self._mesas_validas_do_cliente is None:
            self._mesas_validas_do_cliente = self._carregar_mesas_do_cliente()

        if not self._mesas_validas_do_cliente:
            return True  # não conseguimos validar, segue e deixa a criação do ticket decidir

        return id_mesa in self._mesas_validas_do_cliente

    def _carregar_mesas_do_cliente(self) -> set[int]:
        resp = requests.get(f"{self._url_base}/clients/{self._cliente_id}/desks", headers=self._headers_get)
        if resp.status_code != 200:
            log(f"⚠️ Não foi possível validar as mesas do cliente ({resp.status_code}): {resp.text}")
            return set()  # não bloqueia, deixa a API acusar na criação
        return {m.get("id") for m in resp.json()}

    def obter_id_solicitante(self, nome_glpi: str, email_glpi: str | None) -> tuple[int, str]:
        if not email_glpi:
            return self._id_solicitante_padrao, "Ju STII (Padrão - Sem E-mail no GLPI)"

        encontrado = self._buscar_solicitante_por_email(email_glpi)
        if encontrado:
            return encontrado

        return self._cadastrar_solicitante(nome_glpi, email_glpi)

    def _buscar_solicitante_por_email(self, email_glpi: str) -> tuple[int, str] | None:
        email_limpo = email_glpi.strip().lower()
        email_encoded = urllib.parse.quote(email_limpo)
        url_busca = f"{self._url_base}/clients/{self._cliente_id}/requestors?email={email_encoded}"
        resposta = requests.get(url_busca, headers=self._headers_get)

        if resposta.status_code == 200:
            dados = resposta.json()
            if isinstance(dados, list):
                for item in dados:
                    if str(item.get("email", "")).strip().lower() == email_limpo:
                        return item.get("id"), f"{item.get('name')} (Existente no TiFlux - ID: {item.get('id')})"
        elif resposta.status_code != 404:
            log(f"⚠️ Busca de solicitante retornou status inesperado ({resposta.status_code}): {resposta.text}")

        return None

    def _cadastrar_solicitante(self, nome: str, email: str) -> tuple[int, str]:
        url_criar = f"{self._url_base}/clients/{self._cliente_id}/requestors"
        payload = {
            "name": nome if nome != "Desconhecido" else "Solicitante Sem Nome",
            "email": email,
            "can_open_ticket": True,
        }
        resp = requests.post(url_criar, json=payload, headers=self._headers_json)
        if resp.status_code in (200, 201):
            dados = resp.json()
            return dados.get("id"), f"{dados.get('name')} (Cadastrado Automaticamente via API)"

        log(f"⚠️ Falha ao cadastrar solicitante ({resp.status_code}): {resp.text}")
        return self._id_solicitante_padrao, "Ju STII (Padrão - Falha ao Auto-Cadastrar no TiFlux)"

    def criar_ticket(self, form_data: dict[str, str]) -> tuple[str | None, str | None]:
        """Retorna (ticket_number, erro_ou_None)."""
        resp = requests.post(f"{self._url_base}/tickets", data=form_data, headers=self._headers_form)
        if resp.status_code not in (200, 201):
            return None, f"Falha ao criar ticket no Tiflux ({resp.status_code}): {resp.text}"

        dados_retorno = resp.json()
        ticket_number = None
        if isinstance(dados_retorno, dict) and "ticket" in dados_retorno:
            ticket_number = dados_retorno["ticket"].get("ticket_number")

        if not ticket_number:
            return None, "Ticket criado, mas não foi possível identificar o ticket_number na resposta"

        return ticket_number, None

    def obter_mesa_do_ticket(self, ticket_number: str) -> int | None:
        """
        GET /tickets/{ticket_number} e devolve o ID da mesa (desk) ATUAL do
        ticket no Tiflux. Consultado a cada sincronização de followup (em vez de
        reaproveitar a mesa de quando o ticket foi criado) porque tickets podem
        ser movidos de mesa depois — followups sempre seguem a mesa de agora.
        """
        resp = requests.get(f"{self._url_base}/tickets/{ticket_number}", headers=self._headers_get)
        if resp.status_code != 200:
            log(f"⚠️ Não foi possível obter a mesa do ticket Tiflux #{ticket_number} ({resp.status_code}): {resp.text}")
            return None
        return (resp.json().get("desk") or {}).get("id")

    def atribuir_tecnico(self, ticket_number: str, id_tecnico: int) -> tuple[bool, int, str]:
        """Retorna (sucesso, status_http_da_ultima_tentativa, corpo_da_resposta)."""
        url_alterar_responsavel = f"{self._url_base}/tickets/{ticket_number}/change_responsible"
        payload_resp = {"responsible_id": id_tecnico}
        resp = requests.post(url_alterar_responsavel, json=payload_resp, headers=self._headers_json)
        if resp.status_code in (200, 201, 204):
            return True, resp.status_code, resp.text

        url_put = f"{self._url_base}/tickets/{ticket_number}"
        payload_put = {"ticket": {"responsible_id": id_tecnico}}
        resp = requests.put(url_put, json=payload_put, headers=self._headers_json)
        return resp.status_code in (200, 201, 204), resp.status_code, resp.text

    def enviar_anexos(self, ticket_number: str, anexos: list[Anexo]) -> tuple[int, int, list[str]]:
        """
        Envia os anexos pro ticket no Tiflux, em lotes de até 10 por requisição
        (limite da API). Retorna (qtd_enviados, qtd_falhados, motivos_das_falhas).
        """
        if not anexos:
            return 0, 0, []

        enviados = 0
        falhados = 0
        motivos = []

        for i in range(0, len(anexos), 10):
            lote = anexos[i:i + 10]
            arquivos_form = [("files[]", (nome, conteudo, mime)) for nome, conteudo, mime in lote]
            # headers_get só tem Accept + Authorization — sem Content-Type,
            # pra deixar o requests montar o multipart/form-data com o boundary certo.
            resp = requests.post(
                f"{self._url_base}/tickets/{ticket_number}/files",
                files=arquivos_form,
                headers=self._headers_get,
            )
            if resp.status_code in (200, 201):
                enviados += len(lote)
            else:
                falhados += len(lote)
                motivos.append(f"Lote {i // 10 + 1} ({resp.status_code}): {resp.text}")

        return enviados, falhados, motivos

    def publicar_resposta_cliente(self, ticket_number: str, conteudo: str, nome_requerente: str) -> requests.Response:
        return requests.post(
            f"{self._url_base}/tickets/{ticket_number}/client-answers",
            files={"name": (None, conteudo), "author_name": (None, nome_requerente)},
            headers=self._headers_get,
        )

    def publicar_resposta_agente(self, ticket_number: str, conteudo: str) -> requests.Response:
        return requests.post(
            f"{self._url_base}/tickets/{ticket_number}/answers",
            files={"name": (None, conteudo)},
            headers=self._headers_get,
        )

    def publicar_comunicacao_interna(self, ticket_number: str, conteudo: str) -> requests.Response:
        return requests.post(
            f"{self._url_base}/tickets/{ticket_number}/internal_communications",
            files={"text": (None, conteudo)},
            headers=self._headers_get,
        )

    def listar_respostas(self, ticket_number: str, tamanho_pagina: int, max_paginas: int) -> list[dict]:
        return self._listar_paginado("answers", ticket_number, tamanho_pagina, max_paginas)

    def listar_comunicacoes_internas(self, ticket_number: str, tamanho_pagina: int, max_paginas: int) -> list[dict]:
        return self._listar_paginado("internal_communications", ticket_number, tamanho_pagina, max_paginas)

    def _listar_paginado(self, endpoint: str, ticket_number: str, tamanho_pagina: int, max_paginas: int) -> list[dict]:
        """
        Pagina um endpoint de resposta/comunicação do Tiflux (offset = número da
        página, não deslocamento de linha — ver doc da API) e retorna todos os itens.
        """
        itens: list[dict] = []
        pagina = 1
        while pagina <= max_paginas:
            resp = requests.get(
                f"{self._url_base}/tickets/{ticket_number}/{endpoint}",
                params={"offset": pagina, "limit": tamanho_pagina},
                headers=self._headers_get,
            )
            if resp.status_code != 200:
                log(f"⚠️ Falha ao listar {endpoint} do ticket Tiflux #{ticket_number} (status {resp.status_code}): {resp.text}")
                break

            pagina_itens = resp.json()
            if not isinstance(pagina_itens, list) or not pagina_itens:
                break

            itens.extend(pagina_itens)
            if len(pagina_itens) < tamanho_pagina:
                break
            pagina += 1

        return itens
