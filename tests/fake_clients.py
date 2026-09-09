"""Duplos de teste para GlpiClient/TifluxClient — implementam a mesma interface
pública usada por sync.processamento_chamado e sync.sincronizacao_followups,
sem depender de HTTP real."""


class FakeGlpiClient:
    def __init__(self):
        self.tickets: dict[int, dict] = {}
        self.status_ticket_ausente = 404
        self.grupo_observador: dict[int, tuple[bool, str | None]] = {}
        self.followups: dict[int, list[dict]] = {}
        self.requerentes: dict[int, tuple[str, str | None, int | None]] = {}
        self.anexos: dict[int, tuple[list, list]] = {}
        self.followups_criados: list[dict] = []
        self.proximo_id_followup = 1000
        self.erro_ao_criar_followup: str | None = None
        self.chamados_encerrados: list[tuple[int, int]] = []
        self.resultado_encerrar_chamado: tuple[bool, str | None] = (True, None)
        self.titulos_atualizados: list[tuple[int, str]] = []
        self.resultado_atualizar_titulo: tuple[bool, str | None] = (True, None)

    def chamado_tem_grupo_observador(self, id_chamado, id_grupo_observador):
        return self.grupo_observador.get(id_chamado, (True, None))

    def obter_ticket(self, id_chamado):
        ticket = self.tickets.get(id_chamado)
        if ticket is None:
            return None, self.status_ticket_ausente
        return ticket, 200

    def obter_requerente(self, id_chamado, ticket):
        return self.requerentes.get(id_chamado, ("Desconhecido", None, None))

    def obter_anexos(self, id_chamado, tamanho_maximo_mb):
        return self.anexos.get(id_chamado, ([], []))

    def obter_followups(self, id_chamado):
        return self.followups.get(id_chamado, [])

    def criar_followup(self, id_chamado, conteudo_html, is_private=0, users_id=None):
        if self.erro_ao_criar_followup:
            return None, self.erro_ao_criar_followup
        self.proximo_id_followup += 1
        self.followups_criados.append({
            "id_chamado": id_chamado, "conteudo": conteudo_html, "is_private": is_private, "users_id": users_id,
        })
        return self.proximo_id_followup, None

    def encerrar_chamado(self, id_chamado, status):
        self.chamados_encerrados.append((id_chamado, status))
        return self.resultado_encerrar_chamado

    def atualizar_titulo(self, id_chamado, titulo):
        self.titulos_atualizados.append((id_chamado, titulo))
        return self.resultado_atualizar_titulo


class FakeTifluxClient:
    def __init__(self):
        self.cliente_id = 762707
        self.mesas_validas: set[int] | None = None
        self.id_solicitante = (3758056, "Ju STII (Padrão)")
        self.resultado_criar_ticket: tuple[str | None, str | None] = ("T-1", None)
        self.ticket_tiflux: dict | None = {}
        self.resultado_atribuir_tecnico: tuple[bool, int, str] = (True, 200, "")
        self.resultado_anexos = (0, 0, [])
        self.tickets_criados: list[dict] = []
        self.respostas: list[dict] = []
        self.comunicacoes: list[dict] = []
        self.publicacoes: list[tuple] = []
        self.resposta_publicacao = _FakeHttpResponse(201, {"id": 555})

    def validar_mesa_do_cliente(self, id_mesa):
        return True if self.mesas_validas is None else id_mesa in self.mesas_validas

    def obter_ticket(self, ticket_number):
        return self.ticket_tiflux, (200 if self.ticket_tiflux is not None else 404)

    def obter_id_solicitante(self, nome_glpi, email_glpi):
        return self.id_solicitante

    def criar_ticket(self, form_data):
        self.tickets_criados.append(form_data)
        return self.resultado_criar_ticket

    def atribuir_tecnico(self, ticket_number, id_tecnico):
        return self.resultado_atribuir_tecnico

    def enviar_anexos(self, ticket_number, anexos):
        return self.resultado_anexos

    def listar_respostas(self, ticket_number, tamanho_pagina, max_paginas):
        return self.respostas

    def listar_comunicacoes_internas(self, ticket_number, tamanho_pagina, max_paginas):
        return self.comunicacoes

    def publicar_comunicacao_interna(self, ticket_number, conteudo):
        self.publicacoes.append(("interna", ticket_number, conteudo))
        return self.resposta_publicacao

    def publicar_resposta_cliente(self, ticket_number, conteudo, nome_requerente):
        self.publicacoes.append(("cliente", ticket_number, conteudo, nome_requerente))
        return self.resposta_publicacao

    def publicar_resposta_agente(self, ticket_number, conteudo):
        self.publicacoes.append(("agente", ticket_number, conteudo))
        return self.resposta_publicacao


class _FakeHttpResponse:
    def __init__(self, status_code, json_data, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data
