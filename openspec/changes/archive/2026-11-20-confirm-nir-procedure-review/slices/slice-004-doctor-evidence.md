# Slice 004 — Médico vê a contradição e decide sobre a análise confirmada

## Identity
- Change: `confirm-nir-procedure-review`.
- Tasks: `../tasks.md`, grupo 4 (4.1–4.5).
- Depende de 003 aceito. Integração final do change, sem archive automático.

## Objective
Mostrar ao médico a confirmação humana e a contradição automática junto ao resumo/análise do confirmado, reforçando completude e preservando liberdade clínica e alcance por subtipo.

## Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`; `../design.md`, D6/D8/D9; deltas `../specs/doctor-decision/spec.md` e `../specs/case-management/spec.md` (auditoria).
- Contratos e resultados aceitos de 001–003.
- `apps/cases/procedure_review.py`; `apps/doctor/{presenters,access,views,forms}.py`.
- `templates/doctor/{_case_cards,case_detail,decide}.html`.
- `apps/dashboard/{views,presenters,event_labels}.py`, `templates/dashboard/case_detail.html`.
- `apps/doctor/tests/{test_detail,test_decision,test_queue}.py`, `apps/dashboard/tests/{test_views,test_event_labels}.py`, `templates/accounts/manual.html`.
- `.pi/skills/verify-hmd/SKILL.md` e mapas existentes de detalhe/decisão antes de execução visual.
- Adaptador D9 `config/llm_verify.py`, override `config/settings/verify.py`, opção `seed_verify --procedure-review` e `tests/test_llm_verify.py` entregues no 002.
- Referência somente-leitura: `/projects/dev/ats-web/apps/cases/procedure_review.py::build_doctor_confirmation_notice` e `apps/doctor/tests/test_procedure_review_notice.py`.

## Requirements
| ID | Contrato | Evidência planejada |
|---|---|---|
| R1 | Aviso do `APPLIED` e confirmação referenciada em detalhe/decisão mostra confirmado, mantido/trocado, anterior, detecção, autor/data/justificativa e ressalva clínica; nunca inventa aviso em automático/legado. | Novo `apps/doctor/tests/test_procedure_review_notice.py`, helper unitário e SSR real das duas páginas. |
| R2 | Texto escapado e auditoria legível correlacionada; detecção original vem da revisão referenciada e nova de APPLIED.automatic_procedures, não da união/conflitos, incluindo conjunto vazio. | HTML médico/painel com A declarado, B detectado originalmente, C confirmado e D detectado na nova passada; casos markup/sem ator/má referência e snapshots de rows/artefato. |
| R3 | Nova declaração efetiva governa alcance por subtipo e decisão singleton; recomendação/aviso não bloqueiam decisão clínica com reserva válida. | Testes queue/detalhe/POST: `cat_cardiaco -> nefrostomia`, médico subtipo antigo/novo, generalista/admin e aprovação contra recomendação. |
| R4 | Trabalho GET e POST direto de caso novo sem completude são recusados; em retenção/falha não há caso pronto na fila. | Negativos no teste de decisão com resumo ausente/espaços, policy/sugestão de tipo anterior, fases de processamento/FAILED, nenhuma mutação médica. |
| R5 | Jornadas integradas manter/trocar/sem detecção/falha e manual médico demonstram o contrato inteiro, com adaptador D9 e POST real gerando confirmação/APPLIED/análise. | Novo `apps/pipeline/tests/test_procedure_review_journey.py`, `verify-hmd` reutilizando factory/fixtures do 002, prova zero rede e integração/gate final. |

## Expected blast radius
- `apps/cases/procedure_review.py` (projeção determinística do aviso e linhas de auditoria).
- `apps/doctor/{presenters,views}.py`, `templates/doctor/_case_cards.html`; forms/access apenas se testes demonstrarem ajuste estritamente necessário, sem mudar matriz.
- `apps/dashboard/{views,event_labels}.py`, `templates/dashboard/case_detail.html` para linhas dos eventos novos; presenter se for o seam real do assembly, sem redesenhar painel.
- Novos testes da matriz, ajustes mínimos nos testes médicos/painel existentes; `templates/accounts/manual.html` na seção médica.
- Jornada/mapa específico `verify-hmd` e reutilização do adaptador/fixtures D9 de 002. `config/llm_verify.py`, `apps/accounts/management/commands/seed_verify.py` e `tests/test_llm_verify.py` podem receber apenas os cenários sintéticos adicionais necessários (detecção vazia/falha); manter guardas existentes e não mudar settings de produção.
- Não redesenhar filas, expandir notificações ou card sumário do painel, alterar política/scheduling, retirar autorização do PDF, expor trilha integral no NIR/médico, modificar migrations/estados/dependências. Justificar arquivo adicional; expansão material exige escalada.

## Failing-before plan
1. Prova SSR com caso realmente confirmado/processado nos slices anteriores: RED por aviso ausente, e caso marcado inconsistente cuja abertura/POST de trabalho hoje não aplica a defesa nova.
2. Unitários podem semear eventos para edge cases; isso não substitui E2E com confirmação, `APPLIED` e pipeline produzidos pelo fluxo.
3. `uv run pytest apps/doctor/tests/test_procedure_review_notice.py apps/pipeline/tests/test_procedure_review_journey.py`; registrar RED antes da implementação e GREEN depois.
4. A prova de troca deve mudar o subtipo e conservar detecção contraditória; caso A apenas renomeado não demonstra reroteamento.
5. Fazer original B e nova detecção D diferirem; o aviso deve manter B na origem e mostrar somente D como nova detecção, nunca A/B/C/D unidos. Cobrir também `automatic_procedures=[]`.

## Implementation constraints
- Seguir `AGENTS.md`, menor mudança sem refactor alheio.
- Aviso fora do texto gerado pelo LLM; fonte é aplicação + confirmação do mesmo caso + revisão original, não o último CONFIRMED solto ou metadata mutável.
- Mostrar detecção original reconstruída de CONFIRMED.review_event_id (classes match/missing_declaration) e detecção nova de APPLIED.automatic_procedures, que contém exatamente os detectados novos na ordem canônica, não tipos em conflito. Nenhum detectado é ausência explícita, não concordância. Não inferir ator ausente nem transformar confirmação em recomendação médica.
- Usar labels canônicos e data local. Justificativa livre escapada, nunca `mark_safe`; reidentificação só na renderização clínica.
- Guard de completude compartilhado de domínio, sem duplicar interpretação de artefatos na view; reforço no POST com estado/lease e sem decisão parcial.
- PDF original pode continuar consultável pelo autorizado em estados de erro/andamento. Isso não equivale a encaminhar como pronto para decisão.
- Dados de demonstração sintéticos, adaptador D9 exclusivo de verify e zero chamada externa LLM real; não gravar artefatos na árvore versionada. Fixture só prepara fonte e retenção inicial pelos serviços reais; confirmação/APPLIED/resumo/análise posteriores devem nascer do POST do navegador, nunca de seed.

## Validation
**Focused:** `uv run pytest apps/doctor/tests apps/dashboard/tests/test_event_labels.py apps/dashboard/tests/test_views.py apps/pipeline/tests/test_procedure_review_journey.py` e testes de confirmação/recovery dos slices anteriores.

**Project-required gates:** gate completo e check Django definidos em `AGENTS.md`; `openspec validate confirm-nir-procedure-review --strict`.

**Runtime/verification:** `verify-hmd` com factory D9 de 002, chave externa vazia e setup opcional `seed_verify --procedure-review` (prompts idempotentes e ocorrências 990101–990104 de D9) para obter retenção real, sem resultados finais semeados. Reexecutar setup não pode resetar jornadas já confirmadas/decididas. Executar login real NIR → POST do formulário → pipeline real com transporte LLM fake → médico autorizado, em jornadas separadas manter, trocar para não detectado e detecção vazia; comprovar confirmação/APPLIED produzidos pelo POST, aviso e análise visíveis, zero transporte externo e decisão permitida/não bloqueada pelo consultivo. Repetir em 390/1280 e verificar escaping/sem overflow; cenário de erro não aparece pronto na fila. Evidência do scheduler real de 003 deve estar anexada ou marcada como bloqueio operacional pré-release, sem fingir que mock a substitui.

**Integração final:** criação única/lote/reenvio novos, contrato histórico, formato, fonte/revisão obsoletas, dupla submissão, encerramento em voo, completo/incompleto, dispatch falhando/recuperado e guard de subtipo. Usar serviços reais e fakes apenas nas fronteiras externas. Não marcar o change implementado com base só em status de arquivo do OpenSpec.

## Acceptance criteria
- [ ] R1: aviso completo nas duas páginas e ausência de aviso fictício provados.
- [ ] R2: revisão original e detecção nova exata (diferente ou vazia) exibidas sem união/conflitos indevidos, com auditoria/escaping e sem fabricar consenso.
- [ ] R3: reroteamento de subtipo e decisão singleton livres de veto clínico comprovados.
- [ ] R4: GET/POST inconsistente sem decisão nem encaminhamento puro comprovados.
- [ ] R5: jornadas E2E/visuais reais com adaptador D9, nenhum resultado final semeado, zero rede, manual e gate completo verdes; limitações operacionais explícitas.

## Escalation conditions
Parar `BLOCKED_NEEDS_DECISION` se qualquer regra clínica/alcance precisar mudar, se a auditabilidade exigir persistência nova, se for necessário ampliar anexos/notificações/painel além da trilha prevista, ou se houver conflito spec/design, nova decisão de segurança/privacidade ou expansão material de escopo. Não enfraquecer guard para fazer fixtures incompletas passarem.

## Evidence report
`/tmp/sirhosp-slice-004-report.md`: status, R1–R5 → evidência, arquivos, RED/GREEN, comandos/exit status, browser/integração e smoke scheduler ou bloqueio, desvios e riscos. Relatório temporário fora do Git; não repetir diff em snippets.

## Worker handoff
Implementar somente este slice após 003 aceito. Reconstruir contexto, produzir RED → GREEN → REFACTOR, validação focada e integração/gate finais com relatório. Não atualizar `tasks.md`, commitar, push, merge, arquivar, atualizar `PROJECT_CONTEXT.md` como se já arquivado ou iniciar outro slice. Terminar `READY_FOR_REVIEW` com `REPORT_PATH=/tmp/sirhosp-slice-004-report.md`, ou bloquear com evidência da decisão necessária.
