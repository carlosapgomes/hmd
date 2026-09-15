# Tasks: painel-ats-parity

## 1. Slice 001 — Unidade de origem do cabeçalho (extração + campo)

- [x] 1.1 `HeaderMetadata.origin_unit` + parser multilinha (`Unid. Origem:`
      mesma-linha OU linha seguinte plausível) + `Case.origin_unit` +
      migration
- [x] 1.2 Worker pdf persiste no mesmo atomic; reenvio zera
      (`_RESUBMIT_CLEARED_FIELDS`); CLEANED preserva
- [x] 1.3 Testes RED→GREEN (multilinha/mesma-linha/adversarial rótulo-seguido-de-rótulo/reenvio/CLEANED) + bateria

## 2. Slice 002 — Lista do painel com paridade ats-web

- [x] 2.1 Filtros `date_from`/`date_to` + `procedure_type` (dropdown 13) +
      `q` por nome; default hoje/todos os estados; métricas independentes
- [x] 2.2 Cards com nome+idade+unidade+exames+fase+data/hora+[Detalhes];
      rota+view `dashboard:case_detail` (identificação completa +
      procedimentos + encerramento admin); encerramento sai do card
- [x] 2.3 Testes RED→GREEN (default discriminante, AND de filtros, busca
      por nome, cards, detail/guards/404, métricas pinadas) + bateria

## 3. Slice 003 — Trilha legível no detail + remoção NIR/médico

- [ ] 3.1 `apps/dashboard/event_labels.py` (`EVENT_LABELS` cobrindo TODO o
      enum + `EVENT_BADGE_CSS`; teste anti-drift)
- [ ] 3.2 Trilha collapsible fechada no detail do painel (labels, dot,
      data/hora, ator)
- [ ] 3.3 Trilha removida dos detalhes NIR/médico + badge «Falha no
      processamento» em FAILED
- [ ] 3.4 Testes RED→GREEN (cobertura, labels no render, collapsible
      fechado, ausência de trilha em NIR/médico, badge) + bateria

## 4. Encerramento

- [ ] 4.1 E2E no dev: painel com cards completos, filtros (data/tipo/nome),
      detail com trilha legível, NIR/médico sem trilha + badge de erro
- [ ] 4.2 Archive do change (sync specs main + Purposes; PROJECT_CONTEXT:
      política PHI UI interna vs perímetro externo; backlog concluído)
