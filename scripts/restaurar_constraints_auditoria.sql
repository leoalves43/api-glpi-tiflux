-- Restaura em tiflux_glpi_sync a estrutura que a migração de 2026-09-30 não
-- levou (só as colunas vieram): sequência do id, defaults, NOT NULL, PKs,
-- UNIQUEs (exigidas pelos ON CONFLICT em sync/db_*.py) e índices.
-- Idêntico ao criar_tabela_auditoria.sql. Tudo ou nada.
BEGIN;

CREATE SEQUENCE IF NOT EXISTS tiflux_glpi_sync.api_glpi_tiflux_id_seq OWNED BY tiflux_glpi_sync.api_glpi_tiflux.id;
SELECT setval('tiflux_glpi_sync.api_glpi_tiflux_id_seq', COALESCE((SELECT max(id) FROM tiflux_glpi_sync.api_glpi_tiflux), 0) + 1, false);
ALTER TABLE tiflux_glpi_sync.api_glpi_tiflux
  ALTER COLUMN id SET DEFAULT nextval('tiflux_glpi_sync.api_glpi_tiflux_id_seq'),
  ALTER COLUMN id SET NOT NULL,
  ALTER COLUMN id_glpi SET NOT NULL,
  ALTER COLUMN status SET NOT NULL,
  ALTER COLUMN tentativas SET DEFAULT 1,
  ALTER COLUMN tentativas SET NOT NULL,
  ALTER COLUMN criado_em SET DEFAULT now(),
  ALTER COLUMN criado_em SET NOT NULL,
  ALTER COLUMN atualizado_em SET DEFAULT now(),
  ALTER COLUMN atualizado_em SET NOT NULL,
  ADD CONSTRAINT api_glpi_tiflux_pkey PRIMARY KEY (id),
  ADD CONSTRAINT api_glpi_tiflux_id_glpi_key UNIQUE (id_glpi);
CREATE INDEX IF NOT EXISTS idx_api_glpi_tiflux_status ON tiflux_glpi_sync.api_glpi_tiflux (status);

CREATE SEQUENCE IF NOT EXISTS tiflux_glpi_sync.api_glpi_tiflux_followups_id_seq OWNED BY tiflux_glpi_sync.api_glpi_tiflux_followups.id;
SELECT setval('tiflux_glpi_sync.api_glpi_tiflux_followups_id_seq', COALESCE((SELECT max(id) FROM tiflux_glpi_sync.api_glpi_tiflux_followups), 0) + 1, false);
ALTER TABLE tiflux_glpi_sync.api_glpi_tiflux_followups
  ALTER COLUMN id SET DEFAULT nextval('tiflux_glpi_sync.api_glpi_tiflux_followups_id_seq'),
  ALTER COLUMN id SET NOT NULL,
  ALTER COLUMN id_glpi SET NOT NULL,
  ALTER COLUMN direcao SET NOT NULL,
  ALTER COLUMN tipo SET NOT NULL,
  ALTER COLUMN id_origem SET NOT NULL,
  ALTER COLUMN status SET NOT NULL,
  ALTER COLUMN tentativas SET DEFAULT 1,
  ALTER COLUMN tentativas SET NOT NULL,
  ALTER COLUMN criado_em SET DEFAULT now(),
  ALTER COLUMN criado_em SET NOT NULL,
  ALTER COLUMN atualizado_em SET DEFAULT now(),
  ALTER COLUMN atualizado_em SET NOT NULL,
  ADD CONSTRAINT api_glpi_tiflux_followups_pkey PRIMARY KEY (id),
  ADD CONSTRAINT api_glpi_tiflux_followups_origem_key UNIQUE (direcao, id_origem);
CREATE INDEX IF NOT EXISTS idx_api_glpi_tiflux_followups_status ON tiflux_glpi_sync.api_glpi_tiflux_followups (status);
CREATE INDEX IF NOT EXISTS idx_api_glpi_tiflux_followups_id_glpi ON tiflux_glpi_sync.api_glpi_tiflux_followups (id_glpi);

COMMIT;
