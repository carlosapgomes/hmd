# Slice 004: Cards de anexo na decisão médica

## Objetivo

O médico vê os anexos do caso como cards na tela de decisão: status, método
de extração, badge `match|mismatch|unknown` e resumo/evidência
**re-identificados na renderização**; `mismatch` é alerta consultivo —
nunca descarta nem bloqueia. Casos sem anexos ficam visualmente idênticos.

## Contexto necessário

- Slices 001–003 entregues (row com `status`/`patient_match`/
  `verification_summary`/`verification_evidence`/`pseudonym_map` do anexo).
- Design D5 (`openspec/changes/attachment-processing-ocr/design.md`).
- `apps/doctor/presenters.py::build_case_detail_context` — presenter puro do
  change 07 (seções por área; adicionar seção aditiva; casos sem anexo →
  chave ausente/vazia).
- Re-identificação na renderização: `apps/anonymization/reidentify.py::
  reidentify(text, pseudonym_map)` — o núcleo puro JÁ aceita mapa arbitrário
  (P2 da review: **sem helper novo**); para anexos usar o mapa DO ANEXO
  (auto-suficiente: namespace estendido do caso, D4 — semântica sem
  ambiguidade, sem colisões); fallback ao mapa do caso apenas defensivo
  (teste opcional).
- `templates/doctor/case_detail.html` — padrão de seções/badges Bootstrap.
- Testes de view do doctor: `apps/doctor/tests/` (conftest com fixtures de
  login/papel ativo; padrão de assert de conteúdo).

## Requisitos verificáveis

- **R1** Presenter: `build_case_detail_context` inclui `attachments`
  (lista: nome, método legível Local/OCR externo, status legível, badge
  `match|mismatch|unknown|processando|falhou`, resumo e evidência
  RE-IDENTIFICADOS com o mapa do anexo — sem tokens na página; anexos
  `pending/processing/failed` sem resumo); sem anexos → seção ausente
  (contexto sem a chave ou vazia; template não renderiza).
- **R2** Template: seção "Anexos" com card por anexo (badge por
  `patient_match`/status); `mismatch` → badge de alerta (danger) + texto
  fixo "Divergência de identificação — avalie o documento"; nenhum botão/
  ação automática (sem descarte, sem bloqueio — `can_decide` do caso não
  muda).
- **R3** Tokens jamais renderizados: teste com resumo/evidência contendo
  tokens do mapa do anexo → página final sem `<PESSOA_`/tokens, com o valor
  real presente.
- **R4** Caso decidível com mismatch: teste de decisão (POST) em caso com
  anexo `mismatch` funciona normalmente (o fluxo do change 07 intocado).
- **R5** Testes: card match re-identificado; card mismatch com alerta e sem
  bloqueio (+POST decide); anexos pending/failed com status e sem resumo;
  caso sem anexos sem seção.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/doctor/presenters.py` | `test_presenter_attachments_reidentified`, `test_presenter_no_attachments_absent` |
| R2 | `templates/doctor/case_detail.html` | `test_card_mismatch_alert_no_action` |
| R3 | `apps/doctor/presenters.py` | `test_no_tokens_rendered` |
| R4 | `apps/doctor/tests/test_decision.py` (adição) | `test_decide_with_mismatch_attachment_ok` |
| R5 | `apps/doctor/tests/test_detail.py` (adições) | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/doctor/presenters.py            # seção attachments (aditiva)
  - templates/doctor/case_detail.html    # seção Anexos
  - apps/doctor/tests/{test_detail,test_decision}.py

out_of_scope:
  - apps/anonymization/reidentify.py (núcleo puro já aceita mapa — sem helper novo)

out_of_scope:
  - apps/attachments (consumido como está); apps/intake; closure (slice 005)
  - qualquer mudança no fluxo de decisão (can_decide/lock/serviço do 03)
  - exibição da imagem/PDF do anexo (cards textuais apenas)
```

## Plano de testes do slice

### RED

- Comando: `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/test_detail.py -k attachment`
- Falha esperada: `Failed: no tests ran` / asserção de seção ausente falha
  (presenter não expõe anexos).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/doctor/tests/ apps/anonymization/tests/`
  — exit 0 (regressão presenter/reidentify).
- `uv run ruff check apps/doctor apps/anonymization &&
  uv run ruff format --check apps/doctor apps/anonymization`
- `uv run mypy .`

## Critérios de aceitação

- [ ] R1–R5 comprovados; zero tokens na página (assert com tokens no resumo)
- [ ] Mismatch = alerta consultivo; decisão segue normal (POST testado)
- [ ] Casos sem anexos idênticos (sem seção); reidentify puro usado com o
      mapa do anexo (sem helper novo, sem ambiguidade de merge)
- [ ] Gate parcial do slice verde
