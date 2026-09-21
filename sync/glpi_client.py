"""Cliente HTTP fino para a API REST do GLPI usada por esta integração."""

from __future__ import annotations

import concurrent.futures

import requests
from requests.adapters import HTTPAdapter

from sync.config import Config, log

Anexo = tuple[str, bytes, str]

# Timeout padrão (segundos) de toda chamada HTTP a este GLPI — sem isso, uma
# única requisição que trave (rede ou servidor sem resposta) bloqueia a
# execução inteira do cron indefinidamente em vez de falhar e seguir/retentar
# na próxima execução.
TIMEOUT_PADRAO_SEGUNDOS = 30

# Quantas sondagens de buscar_chamados_desde() disparar em paralelo por lote.
# Também usado como pool_maxsize da sessão HTTP, pra não serializar as
# conexões concorrentes atrás do pool padrão (10) do requests.
TAMANHO_LOTE_SONDAGEM_PADRAO = 10


def _mensagem_de_recusa(resp: requests.Response) -> str | None:
    """
    PUT /Ticket/{id} devolve `[{"<id>": true, "message": "..."}]` — `message`
    não-vazia é um aviso/recusa de regra de negócio (ex.: campo obrigatório
    faltando) mesmo com HTTP 200. Retorna essa mensagem, ou None se vazia/
    ausente/corpo em formato inesperado.
    """
    try:
        dados = resp.json()
    except ValueError:
        return None
    if isinstance(dados, list) and dados and isinstance(dados[0], dict):
        return dados[0].get("message") or None
    return None


class GlpiClient:
    """Wraps a sessão autenticada do GLPI. Uma instância por execução do cron."""

    def __init__(
        self, url_base: str, app_token: str, headers: dict[str, str], session: requests.Session | None = None,
        timeout: int = TIMEOUT_PADRAO_SEGUNDOS, tamanho_lote_sondagem: int = TAMANHO_LOTE_SONDAGEM_PADRAO,
    ):
        self._url_base = url_base
        self._app_token = app_token
        self._headers = headers
        self._timeout = timeout
        self._tamanho_lote_sondagem = tamanho_lote_sondagem
        # Sessão HTTP reutilizada por todas as chamadas desta instância — reusa
        # a conexão TCP/TLS com o GLPI (keep-alive) em vez de renegociar uma
        # nova a cada request, que é o que `buscar_chamados_desde()` faz aos
        # montes (uma sondagem por ID). Ver docs/decisions/LOG.md.
        self._session = session if session is not None else requests.Session()
        # Pool do tamanho do lote de sondagem — sem isso, requests concorrentes
        # acima do pool_maxsize padrão (10) do requests serializam esperando
        # conexão livre, anulando o ganho de rodar em paralelo.
        adapter = HTTPAdapter(pool_maxsize=max(tamanho_lote_sondagem, 10))
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    @classmethod
    def autenticar(cls, config: Config) -> "GlpiClient":
        session = requests.Session()
        resposta = session.get(
            f"{config.url_glpi}/initSession",
            headers={"App-Token": config.app_token, "Authorization": f"user_token {config.user_token}"},
            timeout=config.timeout_http_segundos,
        )
        resposta.raise_for_status()
        session_token = resposta.json().get("session_token")
        headers = {"App-Token": config.app_token, "Session-Token": session_token}
        return cls(
            config.url_glpi, config.app_token, headers, session=session,
            timeout=config.timeout_http_segundos, tamanho_lote_sondagem=config.tamanho_lote_sondagem,
        )

    def encerrar_sessao(self) -> None:
        try:
            self._session.get(f"{self._url_base}/killSession", headers=self._headers, timeout=self._timeout)
        except requests.RequestException:
            pass
        finally:
            self._session.close()

    def _get(self, caminho: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self._timeout)
        return self._session.get(f"{self._url_base}{caminho}", headers=self._headers, **kwargs)

    def buscar_chamados_desde(self, id_inicial: int, limite_por_execucao: int, max_furos_seguidos: int) -> list[int]:
        """
        Sonda cada ID a partir de id_inicial via GET /Ticket/{id} (o mesmo
        endpoint, comprovadamente confiável, que já usamos pra buscar os dados
        de cada chamado). Evitamos o endpoint /search/Ticket porque nessa
        instalação ele se mostrou inconsistente (campo/boundary/entidade
        geraram resultados que não batiam com chamados confirmados via GET
        direto).

        As sondagens saem em lotes de até `_tamanho_lote_sondagem` IDs
        disparados em paralelo (ThreadPoolExecutor) — dentro de cada lote os
        resultados são dobrados na ordem dos IDs, então o resultado final é
        idêntico ao de sondar um por um, só que mais rápido: com
        max_furos_seguidos=50 (default), a cauda de "já estou em dia" que
        antes era 50 requests sequenciais agora é ~5 lotes paralelos. O
        tamanho de cada lote também é limitado pelo que falta pra estourar
        max_furos_seguidos/limite_por_execucao, pra nunca disparar mais
        sondagens do que a versão sequencial precisaria no pior caso.

        Para depois de max_furos_seguidos IDs seguidos sem chamado (assume que
        chegou no fim dos criados até agora), ou ao atingir limite_por_execucao
        chamados encontrados.
        """
        encontrados: list[int] = []
        id_atual = id_inicial
        furos_seguidos = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=self._tamanho_lote_sondagem) as executor:
            while furos_seguidos < max_furos_seguidos and len(encontrados) < limite_por_execucao:
                tamanho_lote = min(
                    self._tamanho_lote_sondagem,
                    max_furos_seguidos - furos_seguidos,
                    limite_por_execucao - len(encontrados),
                )
                ids_lote = list(range(id_atual, id_atual + tamanho_lote))
                for id_chamado, existe in zip(ids_lote, executor.map(self._chamado_existe, ids_lote)):
                    if existe:
                        encontrados.append(id_chamado)
                        furos_seguidos = 0
                    else:
                        furos_seguidos += 1
                    id_atual = id_chamado + 1

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
        resp = self._session.post(f"{self._url_base}/ITILFollowup", json=payload, headers=self._headers, timeout=self._timeout)
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
        return self._atualizar_chamado(id_chamado, {"status": status}, "encerrar")

    def voltar_status_para_novo(self, id_chamado: int) -> tuple[bool, str | None]:
        """
        PUT /Ticket/{id} forçando status=1 (Novo). Esta instalação do GLPI
        muda o status pra "Processando (atribuído)" automaticamente sempre
        que um Ticket_User é criado (atribuir_tecnico) ou um followup é
        adicionado (criar_followup) — comportamento padrão do GLPI ao
        registrar um ator/interação, não uma escolha desta integração. Chame
        depois de qualquer uma dessas duas operações pra manter o chamado em
        Novo até um técnico humano decidir mudar. Retorna (sucesso, erro_ou_None).
        """
        return self._atualizar_chamado(id_chamado, {"status": 1}, "voltar status para Novo do")

    def atualizar_titulo(self, id_chamado: int, titulo: str) -> tuple[bool, str | None]:
        """
        PUT /Ticket/{id} pra trocar o título do chamado (ex.: prefixar com o
        número do ticket no Tiflux depois de criado, pra facilitar achar um
        chamado no GLPI a partir do número que aparece no Tiflux).
        Retorna (sucesso, erro_ou_None).
        """
        return self._atualizar_chamado(id_chamado, {"name": titulo}, "atualizar título do")

    def _atualizar_chamado(self, id_chamado: int, campos: dict, acao: str) -> tuple[bool, str | None]:
        """
        PUT /Ticket/{id} SEMPRE devolve HTTP 200/201 nessa instalação do GLPI,
        mesmo quando uma regra de negócio recusa o campo (ex.: status pra
        Solucionado sem técnico atribuído ou sem solução registrada) — a
        recusa só aparece dentro do corpo, em `message`, com o código HTTP de
        sucesso mesmo assim. Confirmado ao vivo: sem checar `message`, uma
        recusa dessas seria lida como sucesso e nunca mais retentada. Por
        isso `message` não-vazia conta como falha aqui, mesmo com HTTP 200.
        """
        payload = {"input": campos}
        resp = self._session.put(f"{self._url_base}/Ticket/{id_chamado}", json=payload, headers=self._headers, timeout=self._timeout)
        if resp.status_code not in (200, 201):
            return False, f"Falha ao {acao} chamado #{id_chamado} no GLPI ({resp.status_code}): {resp.text}"

        mensagem_glpi = _mensagem_de_recusa(resp)
        if mensagem_glpi:
            return False, f"GLPI recusou {acao} chamado #{id_chamado}: {mensagem_glpi}"
        return True, None

    def atribuir_tecnico(self, id_chamado: int, id_usuario: int) -> tuple[bool, str | None]:
        """
        POST /Ticket_User (type=2, Atribuído) pra dar ao chamado um técnico
        responsável no GLPI. Pré-requisito dessa instalação do GLPI pra
        aceitar status Solucionado/Fechado depois — ver encerrar_chamado().
        Retorna (sucesso, erro_ou_None).
        """
        payload = {"input": {"tickets_id": id_chamado, "users_id": id_usuario, "type": 2}}
        resp = self._session.post(f"{self._url_base}/Ticket_User", json=payload, headers=self._headers, timeout=self._timeout)
        if resp.status_code not in (200, 201):
            return False, f"Falha ao atribuir técnico no GLPI ao chamado #{id_chamado} ({resp.status_code}): {resp.text}"
        return True, None

    def tecnico_atribuido(self, id_chamado: int) -> int | None:
        """GET /Ticket/{id}/Ticket_User — retorna o users_id do vínculo tipo 2 (Atribuído), ou None se não tiver."""
        resp = self._get(f"/Ticket/{id_chamado}/Ticket_User")
        if resp.status_code not in (200, 206):
            return None
        for vinculo in resp.json():
            if vinculo.get("type") == 2:
                return vinculo.get("users_id")
        return None

    def registrar_solucao(self, id_chamado: int, conteudo: str) -> tuple[bool, str | None]:
        """
        POST /ITILSolution. Pré-requisito dessa instalação do GLPI pra aceitar
        status Solucionado/Fechado depois — ver encerrar_chamado().
        Retorna (sucesso, erro_ou_None).
        """
        payload = {"input": {"itemtype": "Ticket", "items_id": id_chamado, "content": conteudo}}
        resp = self._session.post(f"{self._url_base}/ITILSolution", json=payload, headers=self._headers, timeout=self._timeout)
        if resp.status_code not in (200, 201):
            return False, f"Falha ao registrar solução no GLPI pro chamado #{id_chamado} ({resp.status_code}): {resp.text}"
        return True, None

    _STATUS_SOLUCAO_RECUSADA = 4  # confirmado ao vivo em GLPI #34187, 2026-09-17

    def solucao_registrada(self, id_chamado: int) -> bool:
        """
        GET /Ticket/{id}/ITILSolution — True se existe alguma solução que não
        tenha sido recusada pelo requerente. Uma solução recusada não conta:
        o ciclo de encerramento em cascata (ver _encerrar_em_cascata) reabre
        o chamado e pode fechá-lo de novo mais tarde, e nesse reencerramento
        precisa de uma solução NOVA (o conteúdo mais recente do Tiflux) —
        contar a recusada como "já registrada" bloqueava isso pra sempre,
        confirmado ao vivo: reabrir e fechar #34187 uma segunda vez nunca
        registrava solução nenhuma no GLPI, só mudava o status.
        """
        resp = self._get(f"/Ticket/{id_chamado}/ITILSolution")
        if resp.status_code not in (200, 206):
            return False
        dados = resp.json()
        if not isinstance(dados, list):
            return False
        return any(s.get("status") != self._STATUS_SOLUCAO_RECUSADA for s in dados)

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

    def chamado_tem_grupo_observador(self, id_chamado: int, ids_grupo_observador: tuple[int, ...]) -> tuple[bool, str | None]:
        """
        Confere se algum ID de ids_grupo_observador está vinculado ao chamado
        como OBSERVADOR (type=3 em Group_Ticket, conforme GLPI: 1=Requerente,
        2=Atribuído, 3=Observador).
        Retorna (bool, motivo_se_nao_encontrado_ou_erro).
        """
        resp = self._get(f"/Ticket/{id_chamado}/Group_Ticket")
        if resp.status_code not in (200, 206):
            return False, f"Falha ao consultar grupos do chamado no GLPI (status {resp.status_code})"

        for vinculo in resp.json():
            if vinculo.get("type") == 3 and vinculo.get("groups_id") in ids_grupo_observador:
                return True, None

        return False, f"Chamado não tem nenhum dos grupos observadores {ids_grupo_observador}"

    def obter_anexos(self, id_chamado: int, tamanho_maximo_mb: int) -> tuple[list[Anexo], list[str]]:
        """
        Busca os documentos vinculados diretamente ao CHAMADO no GLPI (anexos e
        imagens inseridas na descrição) — não inclui documentos anexados via
        followup, ver obter_anexos_do_followup().
        Retorna (lista_de_anexos, avisos) onde cada anexo é (nome, conteudo_bytes, mime)
        e avisos é uma lista de strings com o que não pôde ser baixado/enviado.
        """
        return self._obter_anexos_de(
            f"/Ticket/{id_chamado}/Document_Item", tamanho_maximo_mb, "Falha ao listar anexos do chamado no GLPI",
        )

    def obter_anexos_do_followup(self, id_followup: int, tamanho_maximo_mb: int) -> tuple[list[Anexo], list[str]]:
        """
        Busca os documentos anexados especificamente a um followup (ITILFollowup),
        não ao chamado. Um documento anexado via followup NUNCA aparece em
        /Ticket/{id}/Document_Item (confirmado ao vivo: chamado GLPI #34234 /
        Tiflux #362601 — o anexo da planilha corrigida, mandado num followup do
        requerente, só existe em /ITILFollowup/{id}/Document_Item, com
        tickets_id=0 no próprio Document). Sem isso, o anexo de um followup
        feito depois da criação do ticket no Tiflux é descartado silenciosamente
        (obter_anexos() só roda uma vez, na criação do ticket).
        """
        return self._obter_anexos_de(
            f"/ITILFollowup/{id_followup}/Document_Item", tamanho_maximo_mb, "Falha ao listar anexos do followup no GLPI",
        )

    def _obter_anexos_de(self, caminho: str, tamanho_maximo_mb: int, mensagem_erro: str) -> tuple[list[Anexo], list[str]]:
        resp = self._get(caminho)
        if resp.status_code not in (200, 206):
            return [], [f"{mensagem_erro} (status {resp.status_code})"]

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
