"""Regras de negócio de tradução GLPI -> Tiflux: mesa, técnico e prioridade."""

from sync.config import Config

# De/para fixo de prioridade por mesa. Prioridade no Tiflux é cadastrada POR MESA,
# então o mesmo ID não serve pra todas.
PRIORIDADE_POR_MESA = {
    37963: 120547,  # ADMINISTRATIVO/RH  -> Solicitar um Atendimento
    37964: 120549,  # ARRECADAÇÃO        -> Solicitar um Atendimento
    37965: 120551,  # FINANÇAS           -> Solicitar um Atendimento
    37966: 121197,  # SUPRIMENTOS        -> Solicitar um Atendimento
}

# Textos de SLA do contrato, agrupados em 3 faixas sobre a escala de 6 níveis do GLPI.
_SLA_PRIORIDADE_BAIXA = (
    "Tempo para conclusão não é requerido e o trabalho normal pode continuar.\n"
    "Ação em 72 horas da abertura do chamado e resolução em prazo de comum acordo."
)
_SLA_PRIORIDADE_MEDIA = (
    "Funcionalidade com problema, mas sem comprometer a operação do sistema;\n"
    "Não há compromisso imediato e inadiável do usuário;\n"
    "Alguns munícipes precisam ter a solução dos seus interesses adiada.\n\n"
    "Ação em até 8 horas da abertura do chamado com resolução em 72 horas.\n"
    "Deverá apresentar solução de contorno."
)
_SLA_PRIORIDADE_CRITICA = (
    "Sistema parado;\n"
    "Sistema apresenta erro que compromete a observância de prazo inadiável;\n"
    "Número significativo de munícipes afetado pela paralisação.\n\n"
    "Ação em até 2 horas da abertura do chamado com resolução em até 24 horas.\n"
    "Deverá apresentar solução de emergência."
)

# priority do GLPI -> (nome exibido, texto de SLA)
NIVEIS_PRIORIDADE_GLPI: dict[int, tuple[str, str]] = {
    1: ("Muito baixa", _SLA_PRIORIDADE_BAIXA),
    2: ("Baixa", _SLA_PRIORIDADE_BAIXA),
    3: ("Média", _SLA_PRIORIDADE_MEDIA),
    4: ("Alta", _SLA_PRIORIDADE_MEDIA),
    5: ("Muito alta", _SLA_PRIORIDADE_CRITICA),
    6: ("Crítica", _SLA_PRIORIDADE_CRITICA),
}
# 3 (Média) é o padrão do GLPI — usado quando priority vem vazio ou fora da escala.
_NIVEL_PRIORIDADE_PADRAO = NIVEIS_PRIORIDADE_GLPI[3]


def depara_categoria(cat_id: int | None) -> int | None:
    """
    Retorna o ID da mesa no Tiflux correspondente à categoria do GLPI, ou None
    se a categoria não tiver correspondência conhecida — nesse caso o chamado
    NÃO é sincronizado automaticamente, fica registrado como erro pra revisão manual.
    """
    if cat_id == 233 or cat_id in range(267, 272):
        return 37963  # ADMINISTRATIVO/RH
    if cat_id in range(272, 277):
        return 37964  # ARRECADAÇÃO
    if cat_id in range(277, 282):
        return 37965  # FINANÇAS
    if cat_id in range(282, 287):
        return 37966  # SUPRIMENTOS
    return None


def definir_tecnico(id_mesa: int, config: Config) -> tuple[int | None, str | None]:
    """
    Só a mesa ARRECADAÇÃO tem atribuição automática de técnico (Léo Alves).
    Chamados das demais mesas ficam sem técnico atribuído no Tiflux.
    """
    if id_mesa == 37964:  # ARRECADAÇÃO
        return config.id_tecnico_leo, "Léo Alves"
    return None, None


def definir_autor_glpi(config: Config) -> int:
    """
    Quem representa a integração no GLPI: sempre Léo (Suporte Embras, id
    4988), independente da mesa — usuária da Sania foi inativada no GLPI.
    Usada em dois lugares:
    - Autoria (no GLPI) de um followup sincronizado do Tiflux
      (sincronizacao_followups.py).
    - Técnico atribuído no GLPI (Ticket_User tipo 2) na hora de criar o
      chamado no Tiflux (processamento_chamado.py) — pré-requisito dessa
      instalação do GLPI pra aceitar status Solucionado/Fechado depois.
    """
    return config.id_glpi_leo


def definir_prioridade(id_mesa: int) -> int | None:
    """Retorna o ID de prioridade fixo configurado para a mesa, ou None se não configurado."""
    return PRIORIDADE_POR_MESA.get(id_mesa)


def cabecalho_prioridade_glpi(prioridade_glpi: int | None) -> str:
    """
    Cabeçalho da descrição no Tiflux: nome da prioridade do GLPI + SLA da faixa.
    Ex.: cabecalho_prioridade_glpi(6) -> "Este chamado tem a prioridade: Crítica\\n\\nSistema parado;..."
    """
    nome, sla = NIVEIS_PRIORIDADE_GLPI.get(prioridade_glpi, _NIVEL_PRIORIDADE_PADRAO)
    return f"Este chamado tem a prioridade: {nome}\n\n{sla}"
