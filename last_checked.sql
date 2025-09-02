-- Tabela para armazenar a última data/hora de busca de e-mails
CREATE TABLE last_checked (
    id SERIAL PRIMARY KEY,
    scope TEXT UNIQUE,
    checked_at TIMESTAMPTZ
);
