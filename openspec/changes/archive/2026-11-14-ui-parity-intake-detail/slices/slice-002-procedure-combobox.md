# Slice 002 — Combobox de tipo de procedimento

```yaml
expected_files:
  - apps/intake/forms.py (procedure_type vira select canônico)
  - static/js/procedure_combobox.js (novo, molde ats-web adaptado)
  - static/css/app.css (só bloco do combobox, se ainda ausente)
  - templates/intake/home.html + corrected_resubmission.html (uso do widget)
  - apps/intake/tests/test_*.py (form + fluxos, tipados)
  - .pi/skills/verify-hmd/{features,scripts}/ (jornada combobox-procedimento)
allowed_incidental_files: []
out_of_scope:
  - catálogo de 13 perfis e validação (intactos)
  - filtro de subtipo da fila médica (usa o valor submetido, sem mudança)
  - upload visual (slice 001), painel, filas
```

## Objetivo

`procedure_type` pesquisável no padrão ats-web, com o select
canônico funcionando sem JS e a mesma validação.

## Read first

- `AGENTS.md`, `PROJECT_CONTEXT.md`
- `openspec/changes/ui-parity-intake-detail/proposal.md`, `design.md` (D2, D5–D6)
- `apps/intake/forms.py`, `apps/cases/procedure_catalog.py` (labels + códigos canônicos; sem aliases — sinônimos ficam para change futuro)
- Molde (somente leitura): `/projects/dev/ats-web/static/js/procedure_combobox.js` e o bloco `.procedure-combobox` do seu `app.css`

## Contexto necessário

- `IntakeUploadForm.procedure_type` (ChoiceField de radios) e
  `CorrectedResubmissionForm` (herda o campo).
- Técnica do molde: select recolhido + input/listbox/chevron CSS;
  hint persistente; erro textual com marcador.

## Requisitos verificáveis

- **R1** — Digitação filtra por label/código ignorando acentos;
  teclado (↑↓/Enter) seleciona; texto visível e escolha canônica
  submetida nunca divergem (editar/limpar após selecionar atualiza
  o canônico).
- **R2** — Envio e reenvio submetem `procedure_type` válido do
  catálogo; inválido rejeitado como hoje; vazio, texto sem match e
  texto digitado sem confirmar NÃO submetem valor (com e sem JS);
  erro com associação e foco acessíveis.
- **R3** — Busca restrita a labels + códigos dos 13 perfis; nenhum
  tipo novo ou removido; sem sinônimos (deferido com nota de
  produto).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `procedure_combobox.js`, templates | jornada (com e sem JS: seleção, edição/limpeza pós-seleção) + teste de form |
| R2 | `forms.py` | testes de validação dos dois fluxos (vazio, sem match, não confirmado) + foco/erro acessível na jornada |
| R3 | catálogo (leitura) + JS | teste de options = 13 perfis + buscas positivas/negativas em labels/códigos |

## Plano de testes

### RED

- Comando: `uv run pytest apps/intake/tests/ -q` (testes novos antes)
- Falha esperada: select/combobox ausentes; radios ainda presentes.

### GREEN / verificação local

- `uv run pytest apps/intake -q`, `mypy`, `ruff check/format` — exit 0
- jornada + capturas vs ats-web

## Critérios de aceitação

- [ ] R1–R3 verdes; radios eliminados dos dois fluxos; visível e submetido nunca divergem
- [ ] Erro de validação com o mesmo texto e status de antes

## Relatório de evidência

`/tmp/hmd-slice-i002-report.md` (mesmo formato do slice 001).

## Handoff

Implementar apenas este slice. Escalar se o catálogo exigir alias
novo (decisão de produto) ou se a validação precisar mudar. Não
iniciar o slice 003.
