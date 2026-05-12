-- ==============================================
-- TFG: Asistente de triaje inteligente
-- Esquema completo de base de datos
-- ==============================================

CREATE TABLE IF NOT EXISTS ado_work_items (
    id              BIGINT PRIMARY KEY,
    work_item_type  TEXT,
    title           TEXT,
    state           TEXT,
    created_date    TIMESTAMP,
    changed_date    TIMESTAMP,
    area_path       TEXT,
    iteration_path  TEXT,
    assigned_to     TEXT,
    tags            TEXT,
    description     TEXT,
    repro_steps     TEXT,
    acceptance_criteria TEXT
);

CREATE TABLE IF NOT EXISTS ado_work_item_embeddings (
    work_item_id    BIGINT PRIMARY KEY REFERENCES ado_work_items(id),
    embedding       DOUBLE PRECISION[],
    model           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ado_work_item_relations (
    source_id       BIGINT REFERENCES ado_work_items(id),
    target_id       BIGINT,
    relation_type   TEXT,
    similarity      DOUBLE PRECISION,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_relations_source ON ado_work_item_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_type   ON ado_work_item_relations(relation_type);
