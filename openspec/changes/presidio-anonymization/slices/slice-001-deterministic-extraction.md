# Slice 001: Pré-extração determinística

## Objetivo

Módulo puro `apps/anonymization/deterministic.py`: extração por regex do nº de ocorrência (reuso dos padrões do intake), nome do paciente, data de nascimento e CPF/CNS presentes no texto extraído — a base do linkage e das entidades garantidas da anonimização. Zero DB, zero modelo NLP.

## Contexto necessário (contexto zero)

- Fonte autoritativa: `temp/research/presidio-django-ptbr.md` §9 ("Pipeline com LLM e Linkage": extrair deterministicamente os identificadores essenciais ANTES do LLM) e `temp/plano-implementacao-hmd.md` §6.1.
- Padrões de nº de ocorrência: `apps/intake/pdf_utils.py` do HMD (funções `extract_agency_record_number` — **importe/reuse, não duplique**; este slice não as reescreve).
- Campos do relatório SESAB: os labels vêm do próprio documento ("Paciente:"/"Nome:"; "Nascimento:"/"Data de Nascimento:"; ver seções operacionais do gate em `apps/intake/regulation_gate.py`).
- App `apps/anonymization` não existe — este slice a cria. Modelos/campos chegam no slice 003.
- CPF/CNS: a validação por checksum vive nos recognizers (slice 002); aqui, apenas **candidatos** por regex (normalização de pontuação), com delegates de validação injetáveis para o slice 003 usar os validadores reais.

## Requisitos

- **R1** `DeterministicExtraction` (dataclass frozen): `record_number: str | None`, `patient_name: str | None`, `birth_date: date | None`, `cpf_candidates: tuple[str, ...]`, `cns_candidates: tuple[str, ...]` (dígitos normalizados, sem pontuação, deduplicados preservando ordem).
- **R2** `extract_record_number(text)`: delega ao padrão do `pdf_utils` (import; sem duplicação).
- **R3** `extract_patient_name(text)`: padrões de campo "Paciente:"/"Nome do Paciente:"/"Nome:" (case/acento tolerantes) → valor até quebra de campo seguinte; None quando ausente.
- **R4** `extract_birth_date(text)`: padrões "Nascimento:"/"Data de Nascimento:" + dd/mm/aaaa (também dd-mm-aaaa) → `date` validada (rejeita 32/13); None quando ausente.
- **R5** `extract_cpf_candidates(text)` / `extract_cns_candidates(text)`: regex com ou sem pontuação/espaços (normaliza para dígitos); 11 dígitos CPF / 15 dígitos CNS; sem validação de checksum aqui.
- **R6** `run_deterministic_extraction(text) -> DeterministicExtraction`: compõe tudo; funções puras, determinísticas, sem I/O.
- **R7** Testes: cada extrator com presença/ausência/variações de formato (case, acentos, pontuação, multiline); nº de ocorrência delegado (mock/import real — usar o real); composição completa num texto de relatório sintético.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R6 | `apps/anonymization/deterministic.py` | `test_deterministic.py::test_full_extraction_synthetic_report` |
| R2 | `apps/anonymization/deterministic.py` | `::test_record_number_delegates_to_intake` |
| R3 | idem | `::test_patient_name_patterns`, `::test_patient_name_absent` |
| R4 | idem | `::test_birth_date_valid_and_invalid`, `::test_birth_date_absent` |
| R5 | idem | `::test_cpf_canditates_with_without_punctuation`, `::test_cns_candidates` |
| R7 | `apps/anonymization/tests/test_deterministic.py` | `uv run pytest apps/anonymization/tests/test_deterministic.py` |

## RED

- Comando: `uv run pytest apps/anonymization/tests/test_deterministic.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.anonymization'`.

## GREEN / verificação local

- `uv run pytest apps/anonymization/tests/test_deterministic.py` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/anonymization/{__init__,apps,deterministic}.py
  - apps/anonymization/tests/{__init__,test_deterministic}.py
  - config/settings/base.py        # INSTALLED_APPS += apps.anonymization
out_of_scope:
  - recognizers/engine/Presidio (002); serviço/campos do Case (003); worker (004)
  - persistência de qualquer coisa
```

Escale ao parent se: os labels do relatório SESAB divergirem dos padrões (fonte ambígua); precisar de dependência nova.

## Critérios de aceitação

- [ ] R1–R7 comprovados pelos comandos da matriz
- [ ] Reuso (import) do padrão de nº de ocorrência — sem duplicação
- [ ] Funções puras determinísticas (zero I/O, zero modelo)
- [ ] Gate parcial do slice verde
