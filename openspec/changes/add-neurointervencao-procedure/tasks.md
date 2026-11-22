# Tasks

Execução por slices verticais — cada task abaixo aponta para o contrato completo
em `slices/`. Um slice só inicia após o anterior aceito (review independente).
Plano revisado após review independente de 2026-11-22 (NEEDS-REWORK incorporado:
fusão das pinagens no slice 001 com gate completo obrigatório, inventário estendido,
texto do template de revisão, runbook e varredura ampliada).

## 1. Catálogo 14 + seeds + fixações + pinagens (RED→GREEN, gate completo)

- [x] S1 — Tipo `neurointerv` no catálogo (perfil S2/neuro/anestésico, contagem 14, ordem pós-`art_cerebral`), frase literal de contexto nos seeds, todas as fixações mecânicas (casos/llm/accounts/pipeline/intake-UI), texto "14 tipos do catálogo" no card de revisão, pinagens de selects/roteamento/detecção e gate completo verde (suíte inteira)
  Contract: `slices/slice-001-catalogo-seeds-superficies.md`

## 2. Varredura residual ampliada + runbook + gate final

- [ ] S2 — Literais obsoletos de contagem removidos de apps/config/templates/static/README/docs (grep antes/depois com padrões derivados), runbook sem indução a pular o passo de seeds em releases "sem migrations, MAS com seeds", quality gate completo e verificação operacional dos seeds idempotentes (14 perfis/8 seções; 31 templates)
  Contract: `slices/slice-002-varredura-runbook-gate.md`
