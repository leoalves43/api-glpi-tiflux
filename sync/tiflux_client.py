"""Cliente HTTP fino para a API REST do Tiflux usada por esta integração."""

from __future__ import annotations

import re
import urllib.parse

import requests

from sync.config import Config, log
from sync.glpi_client import TIMEOUT_PADRAO_SEGUNDOS, Anexo


class TifluxClient:
    """Wraps chamadas à API do Tiflux. Uma instância por execução do cron."""

    def __init__(
        self, url_base: str, token: str, cliente_id: int, id_solicitante_padrao: int,
        session: requests.Session | None = None, timeout: int = TIMEOUT_PADRAO_SEGUNDOS,
    ):
        self._url_base = url_base
        self._cliente_id = cliente_id
        self._id_solicitante_padrao = id_solicitante_padrao
        self._mesas_validas_do_cliente: set[int] | None = None
        self._timeout = timeout
        # Sessão HTTP reutilizada por todas as chamadas desta instância — reusa
        # a conexão TCP/TLS com o Tiflux (keep-alive) em vez de renegociar uma
        # nova a cada request. Ver docs/decisions/LOG.md.
        self._session = session if session is not None else requests.Session()

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
        return cls(
            config.url_tiflux, config.token_tiflux, config.cliente_tiflux_id, config.id_solicitante_padrao,
            timeout=config.timeout_http_segundos,
        )

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
        resp = self._session.get(
            f"{self._url_base}/clients/{self._cliente_id}/desks", headers=self._headers_get, timeout=self._timeout,
        )
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
        resposta = self._session.get(url_busca, headers=self._headers_get, timeout=self._timeout)

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
        resp = self._session.post(url_criar, json=payload, headers=self._headers_json, timeout=self._timeout)
        if resp.status_code in (200, 201):
            dados = resp.json()
            return dados.get("id"), f"{dados.get('name')} (Cadastrado Automaticamente via API)"

        log(f"⚠️ Falha ao cadastrar solicitante ({resp.status_code}): {resp.text}")
        return self._id_solicitante_padrao, "Ju STII (Padrão - Falha ao Auto-Cadastrar no TiFlux)"

    def criar_ticket(self, form_data: dict[str, str]) -> tuple[str | None, str | None]:
        """
        Retorna (ticket_number, erro_ou_None).
        207 é sucesso, não erro: é o status que o Tiflux usa quando a requisição
        inclui "entities" (campos personalizados) — o ticket é criado de qualquer
        forma, e "entities_errors" (se não vier null) descreve só os campos que
        falharam, sem invalidar a criação do ticket em si.
        """
        resp = self._session.post(
            f"{self._url_base}/tickets", data=form_data, headers=self._headers_form, timeout=self._timeout,
        )
        if resp.status_code not in (200, 201, 207):
            return None, f"Falha ao criar ticket no Tiflux ({resp.status_code}): {resp.text}"

        dados_retorno = resp.json()
        ticket_number = None
        if isinstance(dados_retorno, dict) and "ticket" in dados_retorno:
            ticket_number = dados_retorno["ticket"].get("ticket_number")

        if not ticket_number:
            return None, "Ticket criado, mas não foi possível identificar o ticket_number na resposta"

        if isinstance(dados_retorno, dict) and dados_retorno.get("entities_errors"):
            log(f"⚠️ Ticket #{ticket_number} criado, mas campos personalizados falharam: {dados_retorno['entities_errors']}")

        return ticket_number, None

    def obter_ticket(self, ticket_number: str) -> tuple[dict | None, int]:
        """
        GET /tickets/{ticket_number}. Usado a cada sincronização de followup
        (em vez de reaproveitar dados de quando o ticket foi criado) porque
        mesa e status podem mudar depois — followups e encerramento em
        cascata sempre seguem o estado ATUAL do ticket no Tiflux.
        Retorna (ticket_ou_None, status_http).
        """
        resp = self._session.get(
            f"{self._url_base}/tickets/{ticket_number}", headers=self._headers_get, timeout=self._timeout,
        )
        if resp.status_code != 200:
            return None, resp.status_code
        return resp.json(), resp.status_code

    def buscar_ticket_por_chamado_glpi(self, id_chamado: int) -> tuple[str | None, str | None]:
        """
        Procura um ticket já existente no Tiflux pro chamado GLPI `id_chamado`,
        pelo título "<titulo> (<id_chamado>)" — mesma convenção usada tanto pela
        criação automática (ver _montar_form_data em processamento_chamado.py)
        quanto pelas aberturas manuais feitas durante quedas do token GLPI.
        Evita duplicar o ticket quando o chamado não tem linha na auditoria mas
        já foi aberto manualmente no Tiflux.
        Retorna (ticket_number, erro): ticket_number vem preenchido só quando
        exatamente um ticket bate; erro descreve por que nada foi retornado
        (nenhum achado é normal e não conta como erro: (None, None)), incluindo
        o caso de múltiplos candidatos — nunca escolhe um automaticamente.
        """
        resp = self._session.get(
            f"{self._url_base}/tickets",
            params={"search": str(id_chamado), "filter_by": "all", "client_ids": str(self._cliente_id), "limit": 200},
            headers=self._headers_get,
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            return None, f"Falha ao buscar ticket existente pro chamado GLPI #{id_chamado} ({resp.status_code}): {resp.text}"

        candidatos = self._filtrar_tickets_pelo_id_glpi(resp.json(), id_chamado)
        if not candidatos:
            return None, None
        if len(candidatos) > 1:
            numeros = ", ".join(str(n) for n in candidatos)
            return None, (
                f"{len(candidatos)} tickets no Tiflux têm o chamado GLPI #{id_chamado} no título "
                f"({numeros}) — não dá pra escolher automaticamente, reconcilie manualmente"
            )
        return str(candidatos[0]), None

    @staticmethod
    def _filtrar_tickets_pelo_id_glpi(tickets, id_chamado: int) -> list:
        """
        O parâmetro `search` da API do Tiflux é fuzzy (bate em título, cliente,
        mesa, número do ticket, responsável e início da descrição — ver
        openapi-spec-tiflux.json), então filtra de novo aqui pelo id do GLPI
        aparecendo como número isolado no título (não como parte de outro
        número maior).
        """
        if not isinstance(tickets, list):
            return []
        padrao = re.compile(rf"(?<!\d){id_chamado}(?!\d)")
        return [t.get("ticket_number") for t in tickets if padrao.search(t.get("title") or "")]

    def atribuir_tecnico(self, ticket_number: str, id_tecnico: int) -> tuple[bool, int, str]:
        """Retorna (sucesso, status_http_da_ultima_tentativa, corpo_da_resposta)."""
        url_alterar_responsavel = f"{self._url_base}/tickets/{ticket_number}/change_responsible"
        payload_resp = {"responsible_id": id_tecnico}
        resp = self._session.post(url_alterar_responsavel, json=payload_resp, headers=self._headers_json, timeout=self._timeout)
        if resp.status_code in (200, 201, 204):
            return True, resp.status_code, resp.text

        url_put = f"{self._url_base}/tickets/{ticket_number}"
        payload_put = {"ticket": {"responsible_id": id_tecnico}}
        resp = self._session.put(url_put, json=payload_put, headers=self._headers_json, timeout=self._timeout)
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
            resp = self._session.post(
                f"{self._url_base}/tickets/{ticket_number}/files",
                files=arquivos_form,
                headers=self._headers_get,
                timeout=self._timeout,
            )
            if resp.status_code in (200, 201):
                enviados += len(lote)
            else:
                falhados += len(lote)
                motivos.append(f"Lote {i // 10 + 1} ({resp.status_code}): {resp.text}")

        return enviados, falhados, motivos

    def reabrir_ticket(self, ticket_number: str) -> tuple[bool, str | None]:
        """
        PUT /tickets/{ticket_number}/reopen. Usado quando o chamado volta a
        ficar aberto no GLPI (ex.: requerente recusa a solução) enquanto o
        ticket correspondente no Tiflux segue fechado por um encerramento em
        cascata anterior — ver _reabrir_tiflux_apos_recusa_glpi em
        sincronizacao_followups.py. 422 "already open" conta como sucesso
        (idempotente: pode acontecer se uma tentativa anterior já reabriu,
        mas a confirmação falhou por timeout). Retorna (sucesso, erro_ou_None).
        """
        resp = self._session.put(
            f"{self._url_base}/tickets/{ticket_number}/reopen", json={}, headers=self._headers_json, timeout=self._timeout,
        )
        if resp.status_code == 200:
            return True, None
        if resp.status_code == 422 and "already open" in resp.text.lower():
            return True, None
        return False, f"Falha ao reabrir ticket no Tiflux ({resp.status_code}): {resp.text}"

    def publicar_resposta_cliente(self, ticket_number: str, conteudo: str, nome_requerente: str) -> requests.Response:
        return self._session.post(
            f"{self._url_base}/tickets/{ticket_number}/client-answers",
            files={"name": (None, conteudo), "author_name": (None, nome_requerente)},
            headers=self._headers_get,
            timeout=self._timeout,
        )

    def publicar_resposta_agente(self, ticket_number: str, conteudo: str) -> requests.Response:
        return self._session.post(
            f"{self._url_base}/tickets/{ticket_number}/answers",
            files={"name": (None, conteudo)},
            headers=self._headers_get,
            timeout=self._timeout,
        )

    def publicar_comunicacao_interna(self, ticket_number: str, conteudo: str) -> requests.Response:
        return self._session.post(
            f"{self._url_base}/tickets/{ticket_number}/internal_communications",
            files={"text": (None, conteudo)},
            headers=self._headers_get,
            timeout=self._timeout,
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
            resp = self._session.get(
                f"{self._url_base}/tickets/{ticket_number}/{endpoint}",
                params={"offset": pagina, "limit": tamanho_pagina},
                headers=self._headers_get,
                timeout=self._timeout,
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
