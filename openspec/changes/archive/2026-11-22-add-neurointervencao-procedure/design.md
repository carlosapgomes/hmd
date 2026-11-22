# Design

## Context

O catálogo de procedimentos é code-first (`apps/cases/procedure_catalog.py`, ADR-0005): `PROCEDURE_PROFILES` (registro ordenado), `CRITERIA_SECTIONS` (thresholds S1–S8 por seção), `EXPECTED_PROCEDURE_COUNT` como contrato fechado e `catalog_problems()` como verificação de coerência. Toda a cadeia downstream deriva do catálogo: choices do intake (`apps/intake/forms.py`), roteamento da fila médica (`_TYPES_BY_DOCTOR_SUBTYPE` em `apps/doctor/access.py`; `_TYPES_BY_SUBTYPE` em `apps/doctor/views.py`), Literal de detecção do LLM1 (`apps/pipeline/schemas/base.py`), métricas do dashboard e seeds de prompt por tipo (`apps/llm/prompts_seed.py` itera `PROCEDURE_PROFILES`). O subtipo `neuro` já existe (seed da migration `accounts.0003`) e hoje enxerga somente `art_cerebral`. Um texto visível ao usuário no card de revisão (`templates/intake/case_detail.html`) cita "um dos 13 tipos do catálogo" e precisa acompanhar a spec.

Fonte clínica: `temp/parametrosHMD.md` cita "procedimentos da neurointervenção" apenas na lista geral de suporte anestésico — sem seção de critérios própria. O dono decidiu (2026-11-22): **a neurointervenção usa a seção S2** (critérios das angioplastias de carótidas/membros/coronariana — a mais restritiva existente: Plt ≥ 100.000, INR < 1,5 com nota de hemodiálise, Hb ≥ 9,0, Cr < 1,5, PAS < 180 controlada, glicemia < 180, K normal).

## Goals / Non-Goals

**Goals:**

- Adicionar o tipo `neurointerv` ao catálogo com S2/neuro/suporte anestésico, mantendo todas as invariáveis do registro (contagem, coerência seção↔tipos, fail-fast).
- Fechar a lacuna declarativa: pedidos de embolização passam a ser declaráveis pelo NIR e roteáveis ao subtipo `neuro`.
- Prompts por tipo para o novo procedimento (2 templates), coerentes com o mecanismo existente de seeds.
- Texto do card de revisão do NIR alinhado à spec delta ("14 tipos do catálogo").

**Non-Goals:**

- Nova seção de critérios (S9) ou thresholds novos — o dono fixou S2.
- Bloco específico de schema Pydantic para o tipo (a regra do change `llm-pipeline-per-type` permanece: apenas tipos com conteúdo clínico nomeado na fonte ganham blocos; a fonte não descreve campos próprios de embolização).
- Novo subtipo de médico, migrações de dados, mudanças de FSM/policy/UI além do que deriva automaticamente do catálogo.
- Revisar o rótulo/clínica dos outros 13 tipos.

## Decisions

### D1 — Identidade do tipo: código `neurointerv`, label descritivo da classe

Código canônico `neurointerv` segue o estilo do registro (curto, snake_case, sem sufixo de técnica). Label: **"Neurointervenção (embolização de aneurisma ou malformação vascular intracraniana)"** — nomeia a classe e as indicações citadas pelo dono. Alternativa preterida: `embol_intracraniana` (nomeia uma técnica, não a classe que o dono especificou; o plano inicial fala "neurointervenção").

### D2 — Posição no registro: imediatamente após `art_cerebral`

A ordem do registro é a ordem canônica de exibição/auditoria; agrupar os dois tipos `neuro` adjacentes mantém o registro legível. Índices de tipos posteriores mudam, mas nada persiste índice: `selection_key` é derivado puro (nunca gravado — verificado em auditoria), ordenações rederivam do registro em runtime e `CaseProcedure` referencia o código, não a posição. Alternativa preterida: apendar no fim (deixaria os tipos neuro separados; custo zero evitável).

### D3 — Critérios por referência a S2, não por cópia

`CRITERIA_SECTIONS["S2"].procedure_types` ganha `neurointerv` — o perfil referencia a seção existente. Thresholds não são duplicados: a policy consome a seção via mapeamento existente, e `catalog_problems()` já valida seção↔tipos bidirecionalmente. Alternativa preterida: seção nova S9 com valores iguais (criaria segunda fonte de verdade para os mesmos thresholds e violaria a decisão do dono de reusar S2).

### D4 — Prompts do novo tipo: frase de contexto + seeds padrão, sem bloco de schema

`_TYPE_CONTEXT["neurointerv"]` recebe exatamente a frase `"Tratamento endovascular de aneurisma ou malformação vascular intracraniana por embolização."` (literal fixado para eliminar divergência redacional entre workers), e o seed gera `proc.neurointerv.llm1.user`/`proc.neurointerv.llm2.user` pela iteração existente sobre `PROCEDURE_PROFILES`. Sem bloco específico no `build_llm1_schema` (Non-Goal; coerente com a correção de review do pipeline: "nada fora do parametrosHMD"). O LLM1 passa a aceitar o tipo em `procedimentos_solicitados` automaticamente via Literal derivado do catálogo.

### D5 — Contrato de contagem como único ponto de verdade numérico

`EXPECTED_PROCEDURE_COUNT = 14` continua sendo o único literal numérico; testes guarda e o comando `seed_procedure_catalog` passam a validar 14/14 sem outras mudanças. Docstrings/comentários dos arquivos tocados pelo slice 001 têm seus literais atualizados no próprio slice; o restante (apps/pipeline/schemas, config/, static/, templates restantes, README e suítes não tocadas) fica para a varredura do slice 002 — que varre `apps/ config/ templates/ static/ scripts/ README.md` com padrões de contagens derivadas ("13", "(13)", "× 13", "10 tipos", "11 intervencionais", "26 user", "28/29 templates"), não só `apps/ --include="*.py"`. O texto visível ao usuário em `templates/intake/case_detail.html` (contrato da spec `intake-nir`) é atualizado no slice 001, com asserção de teste. Spec renomeada para "Catálogo de tipos de procedimento do HMD" para não fixar contagem no nome do requisito (a contagem vive no texto, que esta change já altera).

### D6 — Deploy: sem migração; seeds obrigatórios no passo 5 do runbook

Nenhuma migration (catálogo é código; `DoctorSpecialty` já semeia `neuro`). Os 2 templates novos nascem do `seed_prompts` (get_or_create por nome/versão). **Em produção, o seed roda no passo 5 do runbook** (`docker compose … --profile migrate run --rm migrate`, que encadeia `migrate`+`createcachetable`+`seed_admin`+`seed_prompts`+`seed_procedure_catalog`) e **NÃO é no-op nesta release**: pulado, o primeiro caso real com `neurointerv` falha com template ativo ausente. O comentário enganoso do runbook ("no-op em release 'sem migrations'") é corrigido no slice 02, e o CHANGELOG desta release deve marcar "sem migrations, MAS com seeds (passo 5 obrigatório)". Localmente, `seed_prompts --settings=config.settings.dev` escreve no banco dev (exige PostgreSQL ativo); a evidência primária de idempotência vem dos testes (`test_prompts.py`, `test_seed_procedure_review.py` contra banco de teste) — o run manual é opcional. `seed_verify` é fail-closed fora de `config.settings.verify` e NÃO deve ser invocado manualmente em dev. Rollback = revert do commit (rows de prompt órfãs são inofensivas: tipos fora do catálogo nunca são montados).

## Risks / Trade-offs

- [S2 é critério herdado por decisão, não derivado de seção própria da fonte clínica] → A decisão do dono fica registrada aqui e na spec delta; se o hospital publicar parâmetros próprios de embolização, change futura cria a seção e movimenta o perfil (um campo do perfil + entry nova).
- [Médicos `neuro` restritos passam a receber casos de um tipo novo sem histórico operacional] → Comportamento desejado (é a razão da change); generalistas e admin continuam vendo tudo, e o roteamento é o mesmo já testado para `art_cerebral`.
- [Casos legados não têm o tipo] → Inexistente por construção: o tipo é novo, não há dado histórico de embolização no HMD.
- [Esquecer o passo de seeds em produção] → Runbook passo 5 obrigatório; comentário enganoso corrigido no slice 02; nota obrigatória no CHANGELOG ("com seeds"). Falha é fail-fast e visível na 1ª chamada de prompt do tipo.
- [Acoplamento inverso de S2 (backlog consciente)] → Edições futuras dos thresholds de S2 (angioplastias) alteram `neurointerv` automaticamente — comportamento desejado da decisão "S2 por referência", protegido pelo teste `EXPECTED_SECTIONS["S2"]`; mudança clínica futura exige change explícita.
- [Label de 78 caracteres em mobile 390px] → CSS existente do combobox trata quebra de linha; conferir visualmente no próximo smoke `verify-hmd` (maior label anterior: 52 chars).

Backlog P2 (sem ação nesta change): pinagem explícita de `evaluate_preop_policy(..., "neurointerv")` → `section_id == "S2"` + alerta anestésico (a derivação em `apps/pipeline/policy.py` é mecânica e garantida pelos testes de seção).

## Migration Plan

1. Deploy do código (uma revisão atômica: catálogo + seeds + testes + texto do template).
2. Produção: passo 5 do runbook (`--profile migrate run --rm migrate`) — **obrigatório mesmo "sem migrations"** (cria os 2 templates).
3. Verificar com `seed_procedure_catalog` (esperado: "14 perfis, 8 seções").
4. Rollback: revert do commit; nenhum dado a reverter (prompts órfãos são inertes).

Notas de arquivamento (fora dos slices): `## Purpose` de `openspec/specs/case-management/spec.md` cita "13 tipos" e deltas não operam sobre Purpose — ajuste manual no archive; `PROJECT_CONTEXT.md` e `CHANGELOG.md` atualizados no arquivamento conforme política do `AGENTS.md`.

## Open Questions

(nenhuma — a decisão clínica de S2 foi tomada pelo dono em 2026-11-22 e as derivadas técnicas estão resolvidas acima)
