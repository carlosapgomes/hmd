# Slice 002: pdf_utils + regulation gate (funções puras)

## Objetivo

A caixa de ferramentas determinística da extração, pura e testável sem rede/worker: `pdf_utils` (texto via PyMuPDF, remoção de marca d'água, extração do nº de ocorrência) e `regulation_gate` adaptado ao domínio (avalia o texto contra o padrão do relatório SESAB com thresholds por env).

## Contexto necessário (contexto zero)

- Referência (somente-leitura): `/projects/dev/ats-web/apps/intake/pdf_utils.py` (`strip_watermark_and_extract_record` — marca d'água = sequência de 5–6 dígitos do registro que se repete; padrões "Código: XXXXX" e "RELATÓRIO DE OCORRÊNCIAS … XXXXX") e `/projects/dev/ats-web/apps/intake/regulation_gate.py` (header "RELATÓRIO DE OCORRÊNCIAS" + sinais institucionais BA + seções operacionais; env `INTAKE_REGULATION_MIN_TEXT_CHARS=500`, `INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS=3`). Nota do recon: **HMD adapta os sinais ao domínio de hemodinâmica** (design D4).
- Design: `../design.md` D3 (pdf_utils), D4 (gate). Spec: Requirements "Extração assíncrona no cluster pdf" (cenário watermark) e "Gate de regulação retém documento fora do padrão".
- Slice 001 entregou `apps/intake` (app existe, sem pdf_utils/gate). **Fixtures de PDF são geradas pelo próprio PyMuPDF nos testes** (sem binários no repo; sem rede).

## Requisitos

- **R1** `extract_document_text(path) -> str`: abre o PDF com PyMuPDF e concatena o texto das páginas; PDF ilegível/corrompido levanta exceção do PyMuPDF (a task do slice 003 a converte em `fail_processing`); PDF válido sem camada de texto → string vazia (gate retém depois).
- **R2** `strip_watermark(text) -> str`: remove a sequência repetida de 5–6 dígitos (registro do paciente) que aparece como marca d'água; texto sem marca permanece idêntico.
- **R3** `extract_agency_record_number(text) -> str | None`: padrões "Código: XXXXX" e "RELATÓRIO DE OCORRÊNCIAS … XXXXX" (case/formato tolerantes); ausente → None.
- **R4** `evaluate_regulation_report(text) -> GateResult(ok: bool, reason: str)` congelável (dataclass frozen): verifica cabeçalho "RELATÓRIO DE OCORRÊNCIAS" + sinais institucionais da Bahia + nº mínimo de seções operacionais + `len(text) >= INTAKE_REGULATION_MIN_TEXT_CHARS`; cada falha tem `reason` canônico ("missing_header", "below_min_chars", "insufficient_sections", "missing_institutional_signals").
- **R5** Settings por env: `INTAKE_REGULATION_MIN_TEXT_CHARS` (default 500), `INTAKE_REGULATION_MIN_OPERATIONAL_SECTIONS` (default 3) — em base.py + `.env.example`.
- **R6** Testes com PDFs gerados em teste: PDF com texto → extração retorna o texto; PDF multi-página → concatenação na ordem; PDF vazio/sem texto → ""; marca d'água repetida removida e texto normal intacto; nº extraído dos dois padrões e None quando ausente; gate ok para relatório padrão (fixture com header+seções+dígitos) e cada reason de falha testado isoladamente.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/pdf_utils.py` | `test_pdf_utils.py::test_extracts_text`, `::test_multipage_concat`, `::test_no_text_layer_empty` |
| R2 | `apps/intake/pdf_utils.py` | `::test_watermark_stripped`, `::test_text_without_watermark_intact` |
| R3 | `apps/intake/pdf_utils.py` | `::test_record_number_patterns`, `::test_record_number_absent` |
| R4 | `apps/intake/regulation_gate.py` | `test_regulation_gate.py::test_standard_report_ok`, `::test_each_failure_reason` |
| R5 | `config/settings/base.py`, `.env.example` | `rg -n "INTAKE_REGULATION" config/settings/base.py .env.example` |
| R6 | `apps/intake/tests/{test_pdf_utils,test_regulation_gate}.py` | `uv run pytest apps/intake/tests/test_pdf_utils.py apps/intake/tests/test_regulation_gate.py` |

## RED

- Comando: `uv run pytest apps/intake/tests/test_pdf_utils.py`
- Falha esperada: `ModuleNotFoundError: No module named 'apps.intake.pdf_utils'`.

## GREEN / verificação local

- `uv run pytest apps/intake/tests/test_pdf_utils.py apps/intake/tests/test_regulation_gate.py` — exit 0
- `uv run pytest apps/intake/tests/` — exit 0
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0
- `rg -n "PyMuPDF\|pymupdf" pyproject.toml` — pin presente

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/pdf_utils.py
  - apps/intake/regulation_gate.py
  - apps/intake/tests/{test_pdf_utils,test_regulation_gate}.py
  - pyproject.toml            # + pymupdf (pin travado no uv.lock)
  - uv.lock
  - config/settings/base.py   # thresholds
  - .env.example
allowed_incidental_files: []
out_of_scope:
  - task/worker/integração FSM (slice 003 liga tudo)
  - OCR (change 10)
  - alters em models
```

Escale ao parent se: a API do PyMuPDF na versão resolvida divergir materialmente; algum padrão do ats-web não portar limpo.

## Critérios de aceitação

- [ ] R1–R6 comprovados pelos comandos da matriz
- [ ] Fixtures geradas em teste (zero binários no repo, zero rede)
- [ ] GateResult com reasons canônicos (consumidos por eventos/testes do slice 003)
- [ ] Gate parcial do slice verde
