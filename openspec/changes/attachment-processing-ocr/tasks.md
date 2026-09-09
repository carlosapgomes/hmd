# Tasks: attachment-processing-ocr

Baseline: `80be50b` (change 09 arquivado; 852 testes verdes ×2 rodadas).

- [x] 1. Preflight: árvore limpa; `BASE_REF = c94c14c`; suíte completa verde ×1 (852 testes; ruff/format/mypy; validate --strict)
- [x] 2.1 Slice 001 — app `apps/attachments` (model `CaseAttachment` + migration 0001) + upload de anexos no intake + listagem NIR. Review: sem P1; P2s F1 (`original_filename` restaurado no D1/model/UI — causa: emenda anterior do pai) e F2 (input de anexos no form de reenvio corrigido) fechados pelo pai; F3 (conftest) aceito. 
- [ ] 2.2 Slice 002 — extração híbrida (PyMuPDF local / `VISION_MODEL` externo com auditoria) + worker cluster `attachments` + trigger pós-anonimização. Ver `slices/slice-002-hybrid-extraction.md`
- [ ] 2.3 Slice 003 — anonimização alinhada ao caso + verificação LLM de patient-match (tokens apenas; prompt seed 29º). Ver `slices/slice-003-anonymize-verify.md`
- [ ] 3.1 Slice 004 — cards de anexo na decisão médica (presenter re-identificado; mismatch = alerta). Ver `slices/slice-004-doctor-cards.md`
- [ ] 4.1 Slice 005 — ciência do NIR remove anexos (integração closure + delta MODIFIED case-closure). Ver `slices/slice-005-closure-integration.md`
- [ ] 5.1 Gate final: `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` + `openspec validate attachment-processing-ocr`; registrar resultado
- [ ] 5.2 Atualizar `PROJECT_CONTEXT.md` (estado pós-change; specs `attachments`/`case-closure` MODIFIED) e preparar arquivamento
