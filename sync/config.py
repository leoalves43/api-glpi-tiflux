"""Credenciais e constantes de configuração da sincronização GLPI <-> Tiflux."""

import sys
from dataclasses import dataclass
from datetime import datetime


def log(msg: str) -> None:
    """
    Mensagens de log usam emojis; alguns ambientes (console do Windows em
    cp1252, saída redirecionada sem encoding UTF-8) não conseguem codificá-los.
    Nunca deixa isso derrubar a sincronização — cai pra uma versão sem
    caracteres não-representáveis nesse caso.
    """
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha = f"[{agora}] {msg}"
    try:
        print(linha)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(linha.encode(encoding, errors="replace").decode(encoding))


def carregar_credenciais(caminho: str = "credenciais.txt") -> dict[str, str]:
    credenciais: dict[str, str] = {}
    with open(caminho, "r") as arquivo:
        for linha in arquivo:
            if "=" in linha:
                chave, valor = linha.split("=", 1)
                credenciais[chave.strip()] = valor.strip()
    return credenciais


@dataclass(frozen=True)
class Config:
    """Credenciais e parâmetros de negócio, resolvidos uma vez em main()."""

    url_glpi: str
    app_token: str
    user_token: str

    url_tiflux: str
    token_tiflux: str

    db_host: str
    db_port: str
    db_name: str
    db_user: str
    db_password: str
    tabela_auditoria: str
    tabela_followups: str

    # Regra de negócio: só processar chamados a partir deste número
    id_minimo_glpi: int = 33637

    # Quantos chamados buscar por execução do cron (aumente se ficar tickets p/ trás)
    tamanho_pagina_busca: int = 200

    # Quantos chamados já sincronizados varrer por execução em busca de followups novos
    tamanho_pagina_followups: int = 50

    # Paginação ao listar respostas/comunicações internas de um ticket no Tiflux
    tamanho_pagina_respostas_tiflux: int = 100
    max_paginas_respostas_tiflux: int = 20  # limite de segurança

    cliente_tiflux_id: int = 762707
    id_solicitante_padrao: int = 3758056  # Ju STII

    # Só sincroniza chamados que tenham algum desses grupos como OBSERVADOR no GLPI
    ids_grupo_observador: tuple[int, ...] = (21, 22)  # EMBRAS - Backlog, EMBRAS - Atendimentos

    id_tecnico_leo: int = 117180

    # IDs de usuário no GLPI (não no Tiflux) usados pra atribuir autoria de
    # followups Tiflux -> GLPI conforme a mesa do chamado
    id_glpi_leo: int = 4988
    id_glpi_sania: int = 4816

    # Máximo de anexo aceito pelo Tiflux
    tamanho_maximo_anexo_mb: int = 25

    # Campo personalizado obrigatório "Módulo utilizado" no Tiflux — sempre
    # criado com a opção "Padrão", já que essa integração não tem como saber
    # o módulo real a partir do GLPI.
    id_campo_modulo_utilizado_tiflux: int = 35107
    id_opcao_modulo_utilizado_padrao_tiflux: int = 819846

    # Máximo de IDs "furados" (404) seguidos antes de considerar que chegamos no
    # fim dos chamados criados até agora e parar de sondar nessa execução.
    max_furos_seguidos: int = 50

    @staticmethod
    def carregar(caminho_credenciais: str = "credenciais.txt") -> "Config":
        cred = carregar_credenciais(caminho_credenciais)
        db_schema = cred.get("DB_SCHEMA", "public")
        db_table = cred.get("DB_TABLE", "api_glpi_tiflux")
        db_table_followups = cred.get("DB_TABLE_FOLLOWUPS", "api_glpi_tiflux_followups")
        return Config(
            url_glpi=cred.get("URL_GLPI"),
            app_token=cred.get("APP_TOKEN"),
            user_token=cred.get("USER_TOKEN"),
            url_tiflux=cred.get("URL_TIFLUX"),
            token_tiflux=cred.get("TOKEN_TIFLUX"),
            db_host=cred.get("DB_HOST"),
            db_port=cred.get("DB_PORT", "5432"),
            db_name=cred.get("DB_NAME"),
            db_user=cred.get("DB_USER"),
            db_password=cred.get("DB_PASSWORD"),
            tabela_auditoria=f"{db_schema}.{db_table}",
            tabela_followups=f"{db_schema}.{db_table_followups}",
        )
