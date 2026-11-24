# Reapresentação no agendamento S4 (slice 004)

UI do agendador para reapresentações recebidas e espera herdada: fila com
contador e último recebimento, detalhe com seção `#reapresentacoes-recebidas`
(lazy PDF desktop, viewer mobile com Voltar, imagem), histórico `CLEANED`
retido vs purgado e confirmação real do agendamento sem triagem clínica.

## Pré-condição (setup real, nunca prova)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
# sob a identidade da run (db/media do manifest):
VERIFY_DB_NAME=<manifest.database> VERIFY_DB_PORT=<manifest.dbPort> \
  HMD_VERIFY_MEDIA_ROOT=<run>/media \
  uv run python manage.py seed_verify --reapresentacao-s4 --settings=config.settings.verify
```

Setup cria pelos serviços reais:
- `880401`: substituição médica (espera herdada 6 dias > `created_at`) →
  aprovação médica → `SCHEDULER_REQUESTED` + 2 incomings distintos
  `scheduling_repeat` aplicados (PDF de relatório, anexo PDF e anexo PNG
  válidos).
- `880402`: aprovado → `scheduling_repeat` aplicado → confirmado → ciência
  do NIR em retenção (`CLEANED` retido).
- `880403`: aprovado → `scheduling_repeat` aplicado → confirmado → ciência
  do NIR → purgado via `purge_closed_case_data` (`CLEANED` purgado).

## Jornadas (via entrypoint oficial)

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
.pi/skills/verify-hmd/scripts/control-hmd drive --run <tmp/verification/hmd-verify-...> --journey s4
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <tmp/verification/hmd-verify-...>
```

- **Desktop 1280×800 (`s4-desktop-*`):**
  - Login real como `verify-scheduler` no form `/login/`.
  - Fila `/scheduler/`: card `880401` com espera herdada (`6 dias`), badge
    `Caso reapresentado 2 vezes enquanto aguarda agendamento`, `Último recebimento:`
    e link `#reapresentacoes-recebidas`.
  - Navegação pelo link até `case_detail` `#reapresentacoes-recebidas`:
    formulários POST confirmar/negar + `lock_token`, aviso de não-aprovação,
    card de PDF original ausente, **0 fetches** para URLs de PDF recebido
    antes de expandir os collapses.
  - Expansão do sub-collapse do relatório PDF recebido → fetch lazy (200).
  - Expansão do sub-collapse do anexo PDF recebido → fetch lazy (200).
  - Imagem anexa `<img>` carregada (200) com `naturalWidth > 0`.
  - `scheduler:case_pdf` original para `880401` retorna 404.
  - Histórico `880402` (`CLEANED` retido): `#reapresentacoes-recebidas` visível,
    PDF recebido carrega lazy (200) ao expandir, PDF original ausente e 404.
  - Histórico `880403` (`CLEANED` purgado): aviso de indisponibilidade e 0
    links/embeds para `/reappearances/`.
- **Mobile 390×844 (`s4-mobile-*`):**
  - Login real como `verify-scheduler`, `case_detail` de `880401`: 0 fetches
    desktop eager de embed.
  - Viewer mobile do relatório PDF recebido → HTTP 200 + serve PDF 200 →
    `Voltar` retorna ao `case_detail`.
  - Viewer mobile do anexo PDF recebido → serve PDF 200 → `Voltar` retorna ao
    `case_detail`.
  - Histórico `880402` no mobile → viewer mobile → `Voltar` retorna ao
    `historical_case_detail`.
- **Confirmação real (`s4-confirm-*`):**
  - Submissão do formulário real de confirmação em `880401` (unidade 1,
    data/hora futura, local) → `Agendamento confirmado.` sem pergunta clínica
    ou suspensão.

Evidência em `<run>/artifacts/` (`report-s4.json`, `s4-*.png/txt`,
`observed-state-s4.json`). Exit `0` PASS · `1` falha observada · `2`
infra/driver.
