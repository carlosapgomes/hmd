# Slice 001 — Metadados do cabeçalho SESAB (extração + campos do caso)

## Contexto necessário

- Todo relatório SESAB repete por página um cabeçalho padrão. Na extração
  linear (PyMuPDF) rótulos e valores desalinham: a demografia vem numa linha
  `<NOME> - Idade: 79a. - Sexo: F - Raça/Cor: Parda` ANTES do rótulo
  `Paciente:` sozinho; `Dias em tela:` é rótulo sozinho com o valor na linha
  seguinte (corpus real do piloto em dev — `Case` `AWAITING_DOCTOR` mais
  recente, `CaseDocument` com o PDF real).
- **Não há data de nascimento no relatório** (`patient_birth_date` segue
  não-populado; pattern vigente permanece para outros layouts).
- `apps/intake/pdf_utils.py`: `extract_document_text` (45), watermark,
  `extract_agency_record_number` (108). Molde ats-web:
  `apps/intake/pdf_utils.py` do ats-web `extract_regulation_days_on_screen`
  (maior `Dias em tela: N`).
- `apps/intake/tasks.py` `_extract_and_decide`: extrai texto, grava
  `extracted_text`/`agency_record_number` no atomic (~214-229) com
  refresh/gate in-atomic (slice 002 do painel-lista-encerramento — a ordem
  dos gates NÃO muda; metadados entram no MESMO write do texto).
- `apps/cases/models.py` `Case`: `patient_name`/`patient_birth_date`
  existem; minimização `_CLEANED_EMPTY_VALUES` (apps/cases/closure.py ~65)
  NÃO deve tocar os campos novos (decisão D6: sobrevivem ao CLEANED por
  paridade com `agency_record_number`).
- Eventos NÃO carregam os valores (payload PHI-free).

## Goal

O worker pdf extrai idade/sexo/raça/dias-em-tela do cabeçalho padrão e
persiste nos campos novos do caso; cabeçalho ausente não é erro.

## Deliverables

### R1 — Extração pura (`apps/intake/pdf_utils.py`)

- `HeaderMetadata` (dataclass congelável: `age: int | None`,
  `gender: str | None`, `race: str | None`, `days_on_screen: int | None`) e
  `extract_header_metadata(text) -> HeaderMetadata`: idade/sexo/raça da
  linha de demografia (`Idade:\s*(\d+)\s*a\.?`, `Sexo:\s*([FM])`,
  `Ra[çc]a/Cor:\s*([A-Za-zà-ÿ]+)`); dias em tela = MAIOR
  `Dias em tela:\s*(\d+)` (molde ats-web); ausentes → `None`. Função pura,
  zero I/O.

### R2 — Campos do caso + migration

- `Case`: `patient_age` (PositiveSmallIntegerField null), `patient_gender`
  (CharField(16) blank/default ""), `patient_race` (CharField(32)
  blank/default ""), `days_on_screen` (PositiveSmallIntegerField null).
- Migration única; `makemigrations --check` limpo.

### R3 — Persistência no worker pdf

- `_extract_and_decide`: no MESMO atomic que grava `extracted_text`/
  `agency_record_number` (após o gate CLEANED vigente), persiste os 4
  metadados de `extract_header_metadata(texto_extraído)`. Nenhum evento com
  os valores. Writer único: metadados só aqui (linkage `patient_name`
  continua só da anonimização).

### R4 — Testes (RED→GREEN)

- Novos (RED): extração pura com fixture do layout real (nome na linha de
  demografia, dias em tela em 2 páginas → maior valor, sexo/raça); campos
  ausentes → `None`s; worker persiste os 4 campos no caso; cabeçalho
  ausente → caso processado normal com campos vazios; eventos sem os
  valores; CLEANED preserva os 4 campos (regressão da minimização).
- Bateria: `TEST_DB_PORT=55435 uv run pytest -q apps/intake` + suíte cheia
  + ruff/mypy/`makemigrations --check`.

## Gates para o reviewer (2 linhas)

1. `apps/intake/tasks.py`: os metadados são gravados no MESMO atomic do
   `extracted_text`, DEPOIS do gate CLEANED in-atomic vigente (nenhum write
   novo fora do atomic/gate).
2. `apps/cases/closure.py`: `_CLEANED_EMPTY_VALUES` permanece IDÊNTICO
   (campos novos sobrevivem por decisão D6 — teste pinnando a paridade).

## Out of scope

- Nome/nome social/tokenização (slice 002 — anonymization).
- UI (cards/filas/detalhe: changes seguintes do backlog).
- Abertura/Data Adm. Unid./Dias Unid. (ficam no texto; não persistem — D3).
