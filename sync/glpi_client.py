"""Cliente HTTP fino para a API REST do GLPI usada por esta integração."""

from __future__ import annotations

import requests

from sync.config import Config, log

Anexo = tuple[str, bytes, str]


class GlpiClient:
    """Wraps a sessão autenticada do GLPI. Uma instância por execução do cron."""

    def __init__(self, url_base: str, app_token: str, headers: dict[str, str]):
        self._url_base = url_base
        self._app_token = app_token
        self._headers = headers

    @classmethod
    def autenticar(cls, config: Config) -> "GlpiClient":
        resposta = requests.get(
            f"{config.url_glpi}/initSession",
            headers={"App-Token": config.app_token, "Authorization": f"user_token {config.user_token}"},
        )
        resposta.raise_for_status()
        session_token = resposta.json().get("session_token")
        headers = {"App-Token": config.app_token, "Session-Token": session_token}
        return cls(config.url_glpi, config.app_token, headers)

    def encerrar_sessao(self) -> None:
        try:
            requests.get(f"{self._url_base}/killSession", headers=self._headers)
        except requests.RequestException:
            pass

    def _get(self, caminho: str, **kwargs) -> requests.Response:
        return requests.get(f"{self._url_base}{caminho}", headers=self._headers, **kwargs)

    def buscar_chamados_desde(self, id_inicial: int, limite_por_execucao: int, max_furos_seguidos: int) -> list[int]:
        """
        Sonda sequencialmente cada ID a partir de id_inicial via GET /Ticket/{id}
        (o mesmo endpoint, comprovadamente confiável, que já usamos pra buscar os
        dados de cada chamado). Evitamos o endpoint /search/Ticket porque nessa
        instalação ele se mostrou inconsistente (campo/boundary/entidade geraram
        resultados que não batiam com chamados confirmados via GET direto).

        Para depois de max_furos_seguidos IDs seguidos sem chamado (assume que
        chegou no fim dos criados até agora), ou ao atingir limite_por_execucao
        chamados encontrados.
        """
        encontrados: list[int] = []
        id_atual = id_inicial
        furos_seguidos = 0

        while furos_seguidos < max_furos_seguidos and len(encontrados) < limite_por_execucao:
            if self._chamado_existe(id_atual):
                encontrados.append(id_atual)
                furos_seguidos = 0
            else:
                furos_seguidos += 1
            id_atual += 1

        if furos_seguidos >= max_furos_seguidos:
            log(f"🔎 Sondagem parou após {max_furos_seguidos} IDs seguidos sem chamado "
                f"(parou em #{id_atual - 1}). Se isso for prematuro, aumente MAX_FUROS_SEGUIDOS.")

        log(f"🔎 Sondagem de #{id_inicial} até #{id_atual - 1}: {len(encontrados)} chamado(s) encontrado(s)")
        return encontrados

    def _chamado_existe(self, id_chamado: int) -> bool:
        resp = self._get(f"/Ticket/{id_chamado}")
        if resp.status_code in (200, 206):
            return True
        if resp.status_code != 404:
            log(f"⚠️ Status inesperado ({resp.status_code}) ao sondar chamado #{id_chamado}: {resp.text}")
        return False

    def obter_ticket(self, id_chamado: int) -> tuple[dict | None, int]:
        """Retorna (ticket_ou_None, status_http) — None quando o chamado não existe/erro."""
        resp = self._get(f"/Ticket/{id_chamado}")
        if resp.status_code not in (200, 206):
            return None, resp.status_code
        return resp.json(), resp.status_code

    def obter_followups(self, id_chamado: int) -> list[dict]:
        """
        GET /Ticket/{id}/ITILFollowup — lista os followups (públicos e privados)
        do chamado. Retorna lista de dicts crus do GLPI, ou [] em caso de erro
        (loga aviso, não levanta).
        """
        resp = self._get(f"/Ticket/{id_chamado}/ITILFollowup")
        if resp.status_code not in (200, 206):
            log(f"⚠️ Falha ao buscar followups do chamado #{id_chamado} no GLPI (status {resp.status_code}): {resp.text}")
            return []
        dados = resp.json()
        return dados if isinstance(dados, list) else []

    def criar_followup(
        self, id_chamado: int, conteudo_html: str, is_private: int = 0, users_id: int | None = None,
    ) -> tuple[int | None, str | None]:
        """
        POST /ITILFollowup, usando o "input wrapper" padrão do GLPI pra criação de
        itens. Sem users_id, o GLPI atribui a autoria ao usuário autenticado da API
        (sempre o mesmo), então passamos users_id pra refletir o autor de verdade.
        Retorna (id_criado, erro_ou_None).
        """
        payload = {
            "input": {
                "itemtype": "Ticket",
                "items_id": id_chamado,
                "content": conteudo_html,
                "is_private": is_private,
            }
        }
        if users_id is not None:
            payload["input"]["users_id"] = users_id
        resp = requests.post(f"{self._url_base}/ITILFollowup", json=payload, headers=self._headers)
        if resp.status_code not in (200, 201):
            return None, f"Falha ao criar followup no GLPI ({resp.status_code}): {resp.text}"

        dados = resp.json()
        id_criado = dados.get("id") if isinstance(dados, dict) else None
        if not id_criado:
            return None, "Followup criado no GLPI, mas não foi possível identificar o id na resposta"

        return id_criado, None

    def encerrar_chamado(self, id_chamado: int, status: int) -> tuple[bool, str | None]:
        """
        PUT /Ticket/{id} pra mudar o status do chamado (ex.: encerramento em
        cascata quando o ticket correspondente foi fechado no Tiflux).
        Retorna (sucesso, erro_ou_None).
        """
        payload = {"input": {"status": status}}
        resp = requests.put(f"{self._url_base}/Ticket/{id_chamado}", json=payload, headers=self._headers)
        if resp.status_code not in (200, 201):
            return False, f"Falha ao encerrar chamado #{id_chamado} no GLPI ({resp.status_code}): {resp.text}"
        return True, None

    def obter_requerente(self, id_chamado: int, ticket: dict) -> tuple[str, str | None, int | None]:
        """Resolve nome, e-mail e id do requerente (Ticket_User type=1) de um chamado."""
        id_requerente = self._id_requerente(id_chamado, ticket)
        if not id_requerente:
            return "Desconhecido", None, None

        nome, email = self._dados_usuario(id_requerente)
        return nome, email, id_requerente

    def _id_requerente(self, id_chamado: int, ticket: dict) -> int | None:
        resp_vinculos = self._get(f"/Ticket/{id_chamado}/Ticket_User")
        if resp_vinculos.status_code in (200, 206):
            for v in resp_vinculos.json():
                if v.get("type") == 1:
                    return v.get("users_id")
        return ticket.get("users_id_recipient") or ticket.get("users_id_lastupdater")

    def _dados_usuario(self, id_usuario: int) -> tuple[str, str | None]:
        nome = "Desconhecido"
        email = None

        resp_usuario = self._get(f"/User/{id_usuario}")
        if resp_usuario.status_code in (200, 206):
            dados = resp_usuario.json()
            p_nome = dados.get("firstname", "")
            s_nome = dados.get("realname", "")
            login = dados.get("name", "")
            nome = f"{p_nome} {s_nome}".strip() if (p_nome or s_nome) else f"Login: {login}"
            email = dados.get("email")

        if not email:
            email = self._email_alternativo(id_usuario)

        return nome, email

    def _email_alternativo(self, id_usuario: int) -> str | None:
        resp_email = self._get(f"/User/{id_usuario}/UserEmail")
        if resp_email.status_code not in (200, 206):
            return None
        lista_emails = resp_email.json()
        if isinstance(lista_emails, list) and lista_emails:
            return lista_emails[0].get("email")
        return None

    def chamado_tem_grupo_observador(self, id_chamado: int, id_grupo_observador: int) -> tuple[bool, str | None]:
        """
        Confere se id_grupo_observador está vinculado ao chamado como OBSERVADOR
        (type=3 em Group_Ticket, conforme GLPI: 1=Requerente, 2=Atribuído, 3=Observador).
        Retorna (bool, motivo_se_nao_encontrado_ou_erro).
        """
        resp = self._get(f"/Ticket/{id_chamado}/Group_Ticket")
        if resp.status_code not in (200, 206):
            return False, f"Falha ao consultar grupos do chamado no GLPI (status {resp.status_code})"

        for vinculo in resp.json():
            if vinculo.get("type") == 3 and vinculo.get("groups_id") == id_grupo_observador:
                return True, None

        return False, f"Chamado não tem o grupo observador ID {id_grupo_observador}"

    def obter_anexos(self, id_chamado: int, tamanho_maximo_mb: int) -> tuple[list[Anexo], list[str]]:
        """
        Busca os documentos vinculados ao chamado no GLPI (anexos e imagens
        inseridas na descrição) e baixa o conteúdo binário de cada um.
        Retorna (lista_de_anexos, avisos) onde cada anexo é (nome, conteudo_bytes, mime)
        e avisos é uma lista de strings com o que não pôde ser baixado/enviado.
        """
        resp = self._get(f"/Ticket/{id_chamado}/Document_Item")
        if resp.status_code not in (200, 206):
            return [], [f"Falha ao listar anexos do chamado no GLPI (status {resp.status_code})"]

        vinculos = resp.json()
        if not isinstance(vinculos, list) or not vinculos:
            return [], []

        anexos: list[Anexo] = []
        avisos: list[str] = []
        for vinculo in vinculos:
            doc_id = vinculo.get("documents_id")
            if not doc_id:
                continue
            self._baixar_documento(doc_id, tamanho_maximo_mb, anexos, avisos)

        return anexos, avisos

    def _baixar_documento(self, doc_id: int, tamanho_maximo_mb: int, anexos: list[Anexo], avisos: list[str]) -> None:
        resp_doc = self._get(f"/Document/{doc_id}")
        if resp_doc.status_code not in (200, 206):
            avisos.append(f"Documento {doc_id}: falha ao obter metadados (status {resp_doc.status_code})")
            return

        meta = resp_doc.json()
        nome_arquivo = meta.get("filename") or meta.get("name") or f"arquivo_{doc_id}"
        mime = meta.get("mime") or "application/octet-stream"

        resp_bin = self._get(f"/Document/{doc_id}", params={"alt": "media"})
        if resp_bin.status_code not in (200, 206):
            avisos.append(f"'{nome_arquivo}': falha ao baixar conteúdo (status {resp_bin.status_code})")
            return

        tamanho_mb = len(resp_bin.content) / (1024 * 1024)
        if tamanho_mb > tamanho_maximo_mb:
            avisos.append(f"'{nome_arquivo}' tem {tamanho_mb:.1f}MB, acima do limite de "
                           f"{tamanho_maximo_mb}MB do Tiflux — não enviado")
            return

        anexos.append((nome_arquivo, resp_bin.content, mime))
