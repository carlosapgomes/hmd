# Slice 002 — NIR confirma novos envios pela interface

## Identity
- Change: `confirm-nir-procedure-review`.
- Tasks: `../tasks.md`, grupo 2 (2.1–2.4).
- Depende de 001 aceito. Não implantar isoladamente antes do change completo.

## Objective
Permitir ao NIR resolver a divergência de novos envios mantendo ou trocando um único procedimento, com confirmação explícita e reprocessamento real.

## Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`; `../design.md`, D1–D3/D9; delta `../specs/intake-nir/spec.md`; contrato 001 e seu relatório aceito.
- `apps/intake/{services,forms,views,urls}.py`, `templates/intake/case_detail.html`.
- `static/js/procedure_combobox.js`, `apps/intake/tests/{test_gate_divergence,test_case_detail,test_creation,test_corrected_resubmission,test_procedure_combobox}.py` (testes de lote vivem em `test_creation.py`).
- `apps/cases/procedure_review.py` e teste de integração entregue em 001.
- `templates/accounts/manual.html`; `.pi/skills/verify-hmd/SKILL.md` e `features/README.md` para qualquer execução do harness.
- `config/settings/verify.py`, `apps/accounts/backends_verify.py`, `apps/accounts/management/commands/seed_verify.py` e `apps/accounts/tests/test_verify_guards.py` (padrões de isolamento para D9).
- `apps/llm/management/commands/seed_prompts.py` (reutilizar comando idempotente existente) e `apps/pipeline/llm.py` (protocolo e erro LlmError).
- Referência somente-leitura: `/projects/dev/ats-web/templates/intake/case_detail.html`, card de confirmação.

## Requirements
| ID | Contrato | Evidência planejada |
|---|---|---|
| R1 | Marca prospectiva na primitiva única; lote e reenvio novo recebem, original/casos anteriores não. | Novo `apps/intake/tests/test_procedure_review_ui.py`, testes de criação/lote/reenvio reais e snapshots do original. |
| R2 | Casos novos não resolvem divergência por POST antigo; formato e histórico mantêm contratos; novo POST não aceita caso sem marca. | Negativas de rotas + regressões em `test_gate_divergence.py`/`test_gate_actions.py`. |
| R3 | Card declarado×detectado com select único de 13 opções, atual confirmável e opção não detectada; leitura e justificativa até 500 obrigatórias, sem upload. | Parser HTML + POSTs mantendo/trocando; testes de form e `QueryDict` com campo repetido/multiplicidade. |
| R4 | Autorização/CSRF/revisão/fonte/lock são reforçados, erros não mutam e preservam tentativa escapada sem consentimento automático; select funciona sem JS. | Client com CSRF quando aplicável, papel/ownership, stale form, 409 e captura de dispatch/DB; submit canônico sem JS. |
| R5 | Cliente determinístico exclusivo de verify, com LlmError nos cenários de falha/desconhecido, permite jornada real sem rede; setup semeia prompts antes dos casos e fixtures estáveis são idempotentes sem reset de progresso. | Novo `tests/test_llm_verify.py`, guardas/seed duas vezes antes/depois de confirmar, banco inicialmente sem prompts, `verify-hmd` desktop/mobile com POST/pipeline reais, teclado/screenshot/trace, manual e regressões. |

## Expected blast radius
- `apps/intake/{forms,services,views,urls}.py` (incluindo marca em `create_case_with_documents`).
- `templates/intake/case_detail.html`; novo `apps/intake/tests/test_procedure_review_ui.py` e testes existentes de criação/gate/lote/reenvio/combobox estritamente afetados.
- `templates/accounts/manual.html` na seção NIR.
- **Ampliação aprovada por revisão:** novo `config/llm_verify.py`, override só em `config/settings/verify.py`, opção `--procedure-review` no `apps/accounts/management/commands/seed_verify.py`, novo `tests/test_llm_verify.py` e testes de seed/guardas em `apps/accounts/tests/`.
- Mapa/jornada específica sob `.pi/skills/verify-hmd/features/` e scripts adjacentes; ler o contrato do harness antes, usar dados sintéticos e artefatos no diretório próprio. Nenhuma alteração em settings base/dev/test/prod ou factory de produção.
- Não mudar catálogo, CSS global, JS do combobox (reutilizar), upload/anexos, auth, sinais de início já entregues, migrations ou lógica clínica. Se for inevitável alterar arquivo extra, justificar; expansão material exige escalada.

## Failing-before plan
1. Teste HTTP parte da primitiva de criação, processa divergência com fake LLM e abre detalhe. RED: novo marcador/formulário ausentes e bypass simples ainda aceito.
2. POST de confirmação mantém/troca, aguarda callbacks no teste e verifica nova extração/análise; usar serviços reais de 001, sem mockar a confirmação.
3. `uv run pytest apps/intake/tests/test_procedure_review_ui.py`; registrar RED antes de implementar.
4. Casos inválidos: seleção vazia/desconhecida/repetida, justificativa vazia/espaços/NUL/501, checkbox ausente, outra pessoa/papel, nova revisão, bytes alterados no mesmo nome de PDF, fonte ilegível e worker com lock ativo.
5. Antes de criar o adaptador, RED de guardas exclusivas de verify e da jornada sem chave externa; depois provar respostas válidas, mesmo resultado em novas instâncias e LlmError em falha/entrada desconhecida com zero HTTP. Cenário manter deve repetir detecção contraditória para testar a autoridade real.
6. RED do setup com banco inicialmente sem prompts: `--procedure-review` deve ativar os prompts antes da primeira LLM1. Reexecutar setup antes e depois de uma confirmação real, provando mesmos ids, arquivos, eventos, estado/progresso e versões de prompts, sem duplicação/reset. IDs esperados: 990101 manter, 990102 trocar, 990103 sem detecção e 990104 erro LLM2 pós-confirmação; conflito com fixture incompatível deve falhar, não sobrescrever.

## Implementation constraints
- Seguir `AGENTS.md` e as convenções existentes; mudança mínima e sem refactor alheio.
- Seleção é **um único tipo**, não pacote nem multiselect. Validar cardinalidade de `request.POST.getlist('procedure_type')` (ou equivalente no form); ChoiceField isolado não prova rejeição de campos repetidos.
- Form e serviço devem concordar nos limites; defesa principal de mutação continua no serviço. Não marcar checkbox no re-render.
- GET não cria marker, confirmação, lease ou job. Marcador nasce somente no atomic de criação; não classificar caso antigo com base em `created_at`/env.
- Rota antiga: preservar formato e histórico; não aceitar body vazio como confirmação de caso novo.
- Flash usa referência humana; 404 alheio, 403 papel errado e 409 lock sem mensagens técnicas contendo PHI. Revisão obsoleta íntegra orienta reabrir; fonte/texto/mapa inconsistente no POST retorna 400 sem mutação e orienta suporte.
- Recuperação de enqueue e suas mensagens pertencem a 003; nenhum botão retry. Deploy só após todos os slices.
- D9: fake somente no adaptador `config/llm_verify.py` pela factory exclusiva de verify, sem rede/fallback real, contador global ou mutação ORM. Cenário de falha e entrada desconhecida devem levantar `apps.pipeline.llm.LlmError` com kind existente e mensagem genérica, nunca exceção genérica ou sucesso sintético. Positivos e negativos em subprocesso devem recusar dev/test/prod/settings programáticas, inclusive com nome de banco verify.
- Fixtures opcionais em `seed_verify --procedure-review` chamam `seed_prompts` antes dos casos, sem sobrescrever prompts ativos, e usam a primitiva/pipeline reais para criar a retenção inicial. Números sintéticos estáveis 990101–990104 conforme D9; reexecução na mesma run não duplica caso/documento/eventos nem reseta confirmação/decisão/progresso, e conflito de fixture é erro explícito. Não alterar seeds padrão nem semear confirmação, APPLIED ou resultados pós-confirmação; esses nascem somente do POST do navegador. Não criar helper CLI Python, nova rota de mutação ou bypass de login.

## Validation
**Focused:** `uv run pytest apps/intake/tests tests/test_llm_verify.py apps/accounts/tests/test_verify_guards.py` e regressão `apps/pipeline/tests/test_human_confirmed_procedures.py`; executar também os testes de seed adicionados.

**Project-required gates:** quality gate e check Django de `AGENTS.md`.

**Runtime/verification:** iniciar harness isolado com `config.settings.verify` apontando para o adaptador D9 e chave externa vazia; chamar a opção `seed_verify --procedure-review` sob a identidade/banco/media da run, com seed de prompts idempotente e fixtures numéricas estáveis D9, para criar retenção real. Provar que repetir setup não altera o progresso antes ou depois de confirmar. Com login real, submeter manutenção e troca para não detectado em jornadas separadas e comprovar nova LLM1/LLM2 e análise produzidas pelo servidor, não seed de resultados. Verificar card sem liberação antiga, campos obrigatórios, retorno de erro, teclado e viewport 390/1280 sem overflow; testar select sem JS. Asserir zero transporte HTTP externo, sem usar banco/media de dev ou relatório real. Bloqueio de Chromium/infra continua sendo bloqueio, não autorização para substituir a prova.

## Acceptance criteria
- [ ] R1: marca somente em novos casos, incluindo lote/reenvio, original preservado.
- [ ] R2: bypass recusado para novos e legado/formato verdes.
- [ ] R3: manter/trocar entre 13 funciona e cardinalidade/campos são validados no servidor.
- [ ] R4: negativos sem efeitos, tentativa escapada, sem consentimento implícito e fallback sem JS demonstrados.
- [ ] R5: adaptador isolado/determinístico, LlmError em falha/desconhecido, zero rede, prompts ativos antes do setup e fixtures estáveis idempotentes sem reset; jornadas reais/manual e gates verdes.

## Escalation conditions
`BLOCKED_NEEDS_DECISION` se aparecer contrato anterior incompatível, necessidade de estado/migration/dependência, expansão para multiprocedimento, mudança no gate SESAB ou no isolamento do harness, ou qualquer decisão de produto/segurança/privacidade não coberta. Não flexibilizar consentimento para contornar teste.

## Evidence report
`/tmp/sirhosp-slice-002-report.md`: status, R1–R5 → testes/artefatos, arquivos, RED/GREEN, comandos/exit status, evidência browser com paths, desvios de blast radius, riscos e decisões humanas. Não versionar artefatos temporários.

## Worker handoff
Implementar somente este slice após 001 aceito. Reconstruir contexto, produzir RED → GREEN → REFACTOR, validação focada, gate e relatório. Não atualizar `tasks.md`, commitar, push, merge, arquivar ou começar outro slice. Finalizar `READY_FOR_REVIEW` com `REPORT_PATH=/tmp/sirhosp-slice-002-report.md`, ou parar bloqueado com evidência da decisão necessária.
