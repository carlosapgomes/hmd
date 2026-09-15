# Tasks: sesab-header-extraction

## 1. Slice 001 — Metadados do cabeçalho SESAB (extração + campos do caso)

- [ ] 1.1 `HeaderMetadata` + `extract_header_metadata` em
      `apps/intake/pdf_utils.py` (idade/sexo/raça da linha de demografia;
      dias em tela = maior ocorrência; ausentes → None)
- [ ] 1.2 Campos `patient_age`/`patient_gender`/`patient_race`/
      `days_on_screen` em `Case` + migration única
- [ ] 1.3 Worker pdf persiste os metadados no mesmo atomic do
      `extracted_text` (após o gate CLEANED vigente); eventos sem valores
- [ ] 1.4 Testes RED→GREEN (extração pura, worker, cabeçalho ausente,
      CLEANED preserva) + bateria local

## 2. Slice 002 — Nome do paciente do cabeçalho + tokenização end-to-end

- [ ] 2.1 Âncora do cabeçalho padrão em `extract_patient_name` (linha de
      demografia + `Paciente:` sozinho na linha seguinte + validações)
- [ ] 2.2 `social_name` em `DeterministicExtraction` + candidato PESSOA
      próprio em `_deterministic_candidates` (sem linkage)
- [ ] 2.3 Fixture do benchmark com o layout real (desalinhado, nome social
      preenchido/vazio, páginas repetidas) + testes de tokenização de todas
      as ocorrências
- [ ] 2.4 Bateria local + aceite operacional com o corpus REAL do piloto
      (recall PESSOA 100%, linkage populado, texto sem o nome)

## 3. Encerramento

- [ ] 3.1 E2E no dev: reenvio do PDF real → caso com metadados + linkage +
      `<PESSOA_1>` no mapa + card de identificação do médico populado
- [ ] 3.2 Archive do change (sync specs main + Purposes)
