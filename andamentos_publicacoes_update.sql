-- Atualização das tabelas andamentos e publicacoes para o novo esquema

-- Tabela andamentos
ALTER TABLE andamentos RENAME COLUMN data TO d;
ALTER TABLE andamentos RENAME COLUMN numero_processo TO processo;
ALTER TABLE andamentos DROP COLUMN IF EXISTS col_b;
ALTER TABLE andamentos DROP COLUMN IF EXISTS col_c;
ALTER TABLE andamentos DROP COLUMN IF EXISTS status_assunto;
ALTER TABLE andamentos DROP COLUMN IF EXISTS col_g;
ALTER TABLE andamentos DROP COLUMN IF EXISTS col_h;
ALTER TABLE andamentos DROP COLUMN IF EXISTS col_i;
ALTER TABLE andamentos ADD COLUMN IF NOT EXISTS inicio_prazo DATE;
ALTER TABLE andamentos ADD COLUMN IF NOT EXISTS fim_prazo DATE;
ALTER TABLE andamentos ADD COLUMN IF NOT EXISTS dias_restantes INTEGER;
ALTER TABLE andamentos ADD COLUMN IF NOT EXISTS setor TEXT;
ALTER TABLE andamentos ADD COLUMN IF NOT EXISTS para_ramon_e_adriana_despacharem TEXT;
ALTER TABLE andamentos ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE andamentos ADD COLUMN IF NOT EXISTS resposta_do_colaborador TEXT;
CREATE INDEX IF NOT EXISTS idx_andamentos_processo ON andamentos (processo);

-- Tabela publicacoes
ALTER TABLE publicacoes RENAME COLUMN data TO d;
ALTER TABLE publicacoes RENAME COLUMN numero_processo TO processo;
ALTER TABLE publicacoes DROP COLUMN IF EXISTS col_b;
ALTER TABLE publicacoes DROP COLUMN IF EXISTS col_c;
ALTER TABLE publicacoes DROP COLUMN IF EXISTS col_d;
ALTER TABLE publicacoes DROP COLUMN IF EXISTS col_g;
ALTER TABLE publicacoes DROP COLUMN IF EXISTS col_h;
ALTER TABLE publicacoes DROP COLUMN IF EXISTS col_i;
ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS inicio_prazo DATE;
ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS fim_prazo DATE;
ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS dias_restantes INTEGER;
ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS setor TEXT;
ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS para_ramon_e_adriana_despacharem TEXT;
ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE publicacoes ADD COLUMN IF NOT EXISTS resposta_do_colaborador TEXT;
CREATE INDEX IF NOT EXISTS idx_publicacoes_processo ON publicacoes (processo);

