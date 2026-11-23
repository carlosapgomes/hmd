# Tasks

Plano produzido com `openspec-vertical-change-writer`, revisado após F-01–F-12 e três decisões do dono. Estado: S1 implementado e aceito com notas P2; S2–S5 pendentes. Executar um slice por vez, em ordem; cada task inclui testes, documentação e gates próprios. Não habilitar produção antes do aceite S1–S5. Antigo S3 foi dividido em S3 (núcleo seguro) e S4 (UI); antiga conferência S4 passa a S5.

## 1. S1 — Identidade e prior-case

- [x] 1.1 Entregar CNS do bloco textual SESAB fechado de D1 (revisão aprovada após review3) com reset imediato, classificador, códigos de origem e prior-case novo em 15 dias; verificar narrativa-demografia/formatos parciais, lookup legado com flag off, passthrough web/todos workers, identidade/idade/tipos, anonimização, migrations e quality gate. Contract: [slice-001-identidade-prior-case.md](slices/slice-001-identidade-prior-case.md).

## 2. S2 — Substituição médica

- [ ] 2.1 Entregar vínculo de fila exclusivo compatível com contexto histórico, substituição só com novo pronto, cadeia/espera, métricas corrigidas e retry limitado no cluster llm; verificar locks no atomic final, concorrência PG, leases/retomada/esgotamento, histórico, ambos modos de disposição e quality gate. Contract: [slice-002-substituicao-medica.md](slices/slice-002-substituicao-medica.md).

## 3. S3 — Resolução e documentos no agendamento

- [ ] 3.1 Entregar scheduling_repeat SOMENTE pós-LLM1/reconciliação, preservando original, com rotas serve/viewer recebidos (inclusive CLEANED retido), retenção e purga de grupo; verificar HTTP/IDs/flags, ausência de LLM2/triagem clínica indevidas, corridas purga×reabertura/resolução, marcadores, erros físicos e quality gate. Contract: [slice-003-reapresentacoes-agendamento.md](slices/slice-003-reapresentacoes-agendamento.md).

## 4. S4 — UI do agendador

- [ ] 4.1 Entregar contador/partial, seção de documentos na página de trabalho real e histórico CLEANED, espera herdada médica→agendamento e manual; verificar queries sem N+1, gates originais preservados, jornada verify-hmd 1280/390 PDF/imagem/anexo PDF, agendamento não interrompido e quality gate. Contract: [slice-004-ui-agendamento.md](slices/slice-004-ui-agendamento.md).

## 5. S5 — Conferência NIR

- [ ] 5.1 Entregar comparação read-only no GET e exatamente duas respostas auditadas no POST, integradas aos três fluxos; verificar independência por revisão, confirmação tardia honesta, prior_context coexistindo com fila, NIR equipe/segurança/concorrência, silêncio sem bloqueio, manual, jornadas finais, OpenSpec strict e quality gate. Contract: [slice-005-conferencia-nir.md](slices/slice-005-conferencia-nir.md).

## Workflow follow-up

- Submeter plano revisado a nova revisão independente antes de autorizar apply; atualização não autoriza implementação.
- Rollout controlado com flag explícita e smoke de workers reais; sem resolução retroativa em massa.
- Ao desligar feature, preservar leitura/métricas/proteção dos vínculos aplicados e lookup legado; documentar reenqueue técnico de pendências ao reabilitar, sem decisões destrutivas.
- Sincronizar specs e arquivar só após aceite; atualizar PROJECT_CONTEXT.md nesse momento, conforme AGENTS.md.
