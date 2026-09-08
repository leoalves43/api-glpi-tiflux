CREATE TABLE siap_custom.api_glpi_tiflux (
  id SERIAL,
  id_glpi INTEGER NOT NULL,
  numero_tiflux INTEGER,
  status VARCHAR(20) NOT NULL,
  mensagem TEXT,
  tentativas INTEGER DEFAULT 1 NOT NULL,
  criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
  atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
  CONSTRAINT api_glpi_tiflux_id_glpi_key UNIQUE(id_glpi),
  CONSTRAINT api_glpi_tiflux_pkey PRIMARY KEY(id)
) 
WITH (oids = false);

COMMENT ON TABLE siap_custom.api_glpi_tiflux
IS 'Auditoria da sincronização de chamados do GLPI para o Tiflux. Cada chamado do GLPI (id_glpi) aparece no máximo uma vez.';

CREATE INDEX idx_api_glpi_tiflux_status ON siap_custom.api_glpi_tiflux
  USING btree (status COLLATE pg_catalog."default");

CREATE TABLE siap_custom.api_glpi_tiflux_followups (
  id SERIAL,
  id_glpi INTEGER NOT NULL,
  numero_tiflux INTEGER,
  direcao VARCHAR(20) NOT NULL,
  tipo VARCHAR(20) NOT NULL,
  id_origem INTEGER NOT NULL,
  id_destino INTEGER,
  status VARCHAR(20) NOT NULL,
  mensagem TEXT,
  tentativas INTEGER DEFAULT 1 NOT NULL,
  criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
  atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
  CONSTRAINT api_glpi_tiflux_followups_origem_key UNIQUE(direcao, id_origem),
  CONSTRAINT api_glpi_tiflux_followups_pkey PRIMARY KEY(id)
)
WITH (oids = false);

COMMENT ON TABLE siap_custom.api_glpi_tiflux_followups
IS 'Auditoria da sincronização bidirecional de followups/respostas entre GLPI e Tiflux. Uma linha por followup sincronizado (ou tentado), identificado por (direcao, id_origem).';

CREATE INDEX idx_api_glpi_tiflux_followups_status ON siap_custom.api_glpi_tiflux_followups
  USING btree (status COLLATE pg_catalog."default");
CREATE INDEX idx_api_glpi_tiflux_followups_id_glpi ON siap_custom.api_glpi_tiflux_followups
  USING btree (id_glpi);