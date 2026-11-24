# Slice 002 — Substituição médica com espera preservada

## 1. Identity
Change: `reapresentacao-casos-filas`. Task: [2.1](../tasks.md#2-s2--substituicao-medica). Depende de S1 aceito.

**Revisão contratual de saneamento:** S2 tem código parcial NÃO aceito. A auditoria integral identificou AUD-01–07 bloqueadores e AUD-08–14 notas/lacunas. Esta revisão não apaga o trabalho, não converte unidades em slices aceitos, não reinicia contadores nem autoriza apply/commit. S1 permanece aceito; S3–S5 não iniciados. Histórico preservado em `/tmp/hmd-slice-002-gd_sn652/`; seus hashes antigos não representam este contrato revisado. Futuro controller deve registrar aprovação/linhagem da revisão e a mesma base/delta original, sem sobrescrever histórico ou inferir novo orçamento.

## 2. Objective
Reapresentação forte e inequívoca substitui o original médico somente após processamento integral, preservando espera, trabalho reservado e recuperação operacional limitada, auditável e visível.

## 3. Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`, `../design.md` D1–D4, D6, D8, D10 e Migration Plan.
- `../specs/case-reappearance/spec.md`, `../specs/doctor-decision/spec.md`, `../specs/case-closure/spec.md`, `../specs/dashboard/spec.md` e S1 aceito.
- `apps/cases/{models,locks,events,event_trail,closure,reappearance,reappearance_tasks,procedure_review}.py` e migration aditiva existente.
- `apps/pipeline/{orchestrator,tasks}.py`, `apps/pipeline/tests/test_reappearance_orchestrator.py` e testes de integridade/revisão do orquestrador.
- `apps/cases/tests/test_reappearance*.py`, testes FSM/trilha/locks/closure/purga/worker; `apps/doctor/views.py`, `_queue_content.html` e testes de fila/decisão/espera.
- `apps/intake/views.py`, `templates/intake/{case_detail,closed_case_detail}.html`, detalhe gerencial e trilha compartilhada; `apps/dashboard/metrics.py` e testes de métricas.
- `.pi/skills/verify-hmd/SKILL.md` INTEIRO e seu mapa S2 antes de qualquer harness/runtime.
- Auditoria em `temp/auditoria-reapresentacao-s2/relatorio-auditoria.md`, quando disponível, como contexto/evidência, NÃO contrato substituto. A matriz abaixo explicita as obrigações mesmo sem o arquivo temporário.
Referências `../` relativas a slices; código à raiz. Inspecionar os caminhos reais antes de criar arquivos: não duplicar seams existentes.

## 4. Requirements e matriz binária de aceite
R1–R5 originais são preservados e decompostos abaixo. Toda linha exige evidência de código + teste comportamental e/ou inspeção runtime apropriada. **Existência de teste, GREEN global e resumo do worker não substituem a prova da linha.** Testes atuais podem ser reutilizados quando suas asserções realmente demonstram o contrato. Não marcar linhas por alegação ou rebaixar obrigação expressa porque o relatório a chamou de P2.

| ID | Obrigação verificável | Evidência mínima / resultado esperado | Origem |
|---|---|---|---|
| R1.1 | Schema/marco aditivos, fallback legado, FK PROTECT e sem auto-link/backfill fictício | Paridade model/migration, dados legados sem vínculo e marco null usando created_at | R1 original |
| R1.2 | Unique par/revisão/kind e uma fila aplicada por incoming, contexto coexistente; incoming≠prior | Constraints reais PG com positivos/negativos; prior_context não consome exclusividade de fila | R1 original |
| R1.3 | FSM própria, 17 estados, aplicação atômica e dois eventos correlacionados legíveis | Fonte S2 AWAITING_DOCTOR→CLEANED válida, demais recusadas; sem admin/decisão/resposta final fictícios; payload permitido/sem PII com asserção NÃO vácua; anti-drift trilha | R1; AUD-11 |
| R1.4 | Cadeia A→B→C imutável, elo B e representante C com ciclo/destino guardados | HTTP NIR criador e não-criador: hrefs exatos, seguir links autorizados, C ativo e CLEANED, negativo fora do guard; re-render 400 não perde vínculo | R1; AUD-09/10a |
| R2.1 | Só novo integralmente pronto/autoridade vigente substitui original ainda médico | Orquestrador REAL, doubles só na fronteira LLM; pronto aplica; fail/retido/divergência/tipo alterado/autoridade obsoleta não removem prior | R2 original |
| R2.2 | Único plausível, não apenas único forte, em TODAS as filas D3 | Forte+fraco, forte+conflito CNS/idade, forte médico+plausível agendamento: sem seleção/fecho; fraco sozinho não aplica | AUD-02 |
| R2.3 | Anterioridade estrita por (created_at,case_id), mesma regra na seleção/revalidação | Empate exato com UUIDs invertidos, sem casos mutuamente anteriores; fraco não vira forte por UUID | D3; AUD-08 |
| R3.1 | Menor marco herdado A→B→C, criação intacta | Tempo congelado, três elos por serviço e fila HTTP/HTML com marco original; sem fabricar dias oficiais | R3 original |
| R3.2 | Card/partial/FIFO usam marco, days_on_screen NOVO é principal | Ordem/asserts exatos em fila completa/polling, nulls e caso sem vínculo; aba decidida e seus guards intactos | R3; AUD-10b |
| R4.1 | Atomic FINAL real: mutex antes de qualquer row lock, grupo por case_id, completar/fechar/herdar/liberar própria lease juntos | Trace SQL inicia na entrada do atomic final, sem seen.clear no mutex; inspeção integração/call sites, rollback sem efeito parcial; chave estável/versionada em todos os entrypoints | AUD-01/07/14 |
| R4.2 | Revalidar chave do nome e conjunto plausível APÓS locks, abort/restart em NOVA transação na deriva | Corridas PG separadas de nome/participante alterado entre coleta/lock; nenhum mutex extra/expansão fora da ordem ou efeito parcial. Limite de 3 tentativas transacionais TOTAIS: erro explícito após rollback; no pipeline incoming ainda em processamento vai a FAILED pelos guards existentes, prior intacto e só própria lease liberada, sem nova LLM/recuperação automática; resolução/retry prontos preservam estado e sinalizam erro | AUD-01 |
| R4.3 | Matriz real de concorrência, não só resolve×resolve | PG conexões separadas/barreiras: orquestrador×retry; dois novos pelo caminho FINAL; decisão×finalização; lease claim/release/expiração×finalização/retry; vencedores/erro limpo/idempotência/sem deadlock. Repetir de forma estável | R4; AUD-07 |
| R4.4 | Vínculo pending existente é reutilizado | Resolve duas vezes com lease viva sem IntegrityError/agenda duplicada; resolve após release aplica MESMO vínculo; reentrada relevante no final real não marca incoming FAILED | AUD-04 |
| R4.5 | Retry preso ao par/revisão original; drift inelegível sem reset | Regressão fix1 continua válida; fonte/identidade/declaração mudada não retargeta par/digest, não zera attempt_count/first_attempt_at e não reanalisa LLM | AUD-05; fix1 |
| R4.6 | Agenda durável Schedule.ONCE cluster llm, nome/dedupe por vínculo+revisão+tentativa | Revisões distintas não colidem; agenda de outra revisão/tentativa não é dedupe válido; estado/agenda coerentes no commit, dispatch só após commit | R4; AUD-05 |
| R4.7 | Backoff60s exponencial cap3600, limites24tentativas OU24h; motivo terminal impede retomada | Testes dos dois limites, inclusive >=24h com <24 tentativas; esgotar→liberar lease→job duplicado/atrasado não aplica/reagenda/duplica auditoria. Mesma guarda para state_ineligible | AUD-06; AUD-10c |
| R4.8 | Jobs duplicados/adiantados/revisão ou tentativa antiga são no-op; lease viva não é tomada | Guardas antes e sob lock, orçamento inalterado em stale job; release/expiração permite apenas retry elegível; release_case_lock NÃO dispara resolução | R4 original |
| R4.9 | Falha inicial e de reagendamento tratada transacionalmente, sem Schedule/sucesso falso | Injeção RuntimeError e erro PG real; savepoint permite registrar pending/falha; lease/decisão intactas; falha de auditoria/recuperação também sinalizada sem alegar gravação | AUD-03; fix2 |
| R4.10 | Pendência atual visível e cronologia verdadeira | Eventos legíveis/deduplicados de falha/esgotamento/reenqueue SOMENTE no incoming; aviso nos detalhes NIR e gerencial desse incoming, nunca duplicado no prior. Guards existentes, GET sem escrita, alerta atual regulariza sem apagar histórico, sem terceira resposta/painel; substituição efetiva mantém dois eventos, um por caso | AUD-03; decisão aprovada |
| R4.11 | Comando técnico explícito de reenqueue, simulação e execução idempotentes | Seleção por vínculo, simulação zero escritas; --apply agenda só efeito elegível com auditoria; agenda válida existente no-op; flagoff/applied/stale/terminal/limites recusados sem reset, sem LLM ou lease takeover; exit0 simulação válida/agenda real/no-op com agenda válida, exit1 recusa --apply/vínculo inexistente/falha persistência, exit2 uso/UUID inválido; stdout distingue os resultados | AUD-03; decisão aprovada |
| R5.1 | Original substituído segue retenção/minimização com marcador coerente e arquivos pós-commit | Documentos/anexos REAIS de fixture: retenção conserva rows/arquivos/marcador null, minimização remove e marca; sem delete físico sob lock; worker atrasado não repovoa; purga preserva elos | R5; AUD-10d |
| R5.2 | Técnicos fora de Em andamento e de TODAS as contagens por procedimento nos dois modos da flag | Encerramento/evento real; total/approved/denied/pending, mantido com decisões reais, resumo Total=envios/Encerrados=CLEANED e sem desfecho/admin fictício; tempo médico não ganha decisão inventada | R5; AUD-10d |
| R5.3 | Rollback da flag preserva leitura/métricas e impede novos efeitos/retries | Flagoff sem candidatos/vínculos/aplicação/retry/orçamento consumido; histórico aplicado permanece legível; recuperação manual --apply recusa enquanto off | Migration Plan |

### Unidades sequenciais de saneamento — NÃO slices/aceites independentes
- **A — Seleção conservadora:** R2.2/R2.3; regressões forte+fraco/conflito/outra fila e empate temporal antes da correção. AUD-02/08.
- **B — Aplicação concorrente segura:** R4.1–R4.3; correções e corridas/trace juntos. Revalidar A sob locks; incluir locks do vínculo quando pertinente. AUD-01/07/14.
- **C — Retry idempotente e limitado:** R4.4–R4.9; TDD de pending reentrante, deriva, colisão de revisões, terminais e falhas; preservar regressões fix1/fix2. AUD-04–06.
- **D — Recuperação observável:** R4.10/R4.11, evento→leitura→comando→agenda→retry, com RED/GREEN, documentação e guards próprios. AUD-03.
- **E — Histórico e espera:** dona explícita de R1.3/R1.4/R3.1/R3.2; implementar ajustes/testes residuais de payload/trilha, links/guards/ciclo e re-render 400, cadeia/tempo congelado/FIFO/polling/decididos, com RED/GREEN/documentação próprios. AUD-09/10a/10b/11.
- **F — Disposição e métricas:** dona explícita de R5.1/R5.2/R5.3; implementar ajustes/testes residuais com arquivos reais nos dois modos, worker/purga/elos, contagens/decisões reais/tempo médico e flag off, com RED/GREEN/documentação próprios. AUD-10d e regressões de rollout.
- **Integração/aceite:** depois de A→B→C→D→E→F, provar TODAS as linhas R1–R5 no estado final. Somente integração, consolidação de evidências e aceite; não acumular correções ou testes de unidade sem dono. Cada unidade entrega seus próprios testes/documentação antes da integração. Não apresentar unidades como autorização de deploy/commit parcial.

## 5. Expected blast radius
Preservar trabalho existente em models/migration/reappearance/reappearance_tasks/closure/events/event_trail, orquestrador/serviços de retry estritamente necessários, views/templates doctor/intake/detalhe gerencial/trilha, métricas, testes correspondentes, manual e harness oficial. Novo comando autorizado em `apps/cases/management/commands/reenqueue_pending_reappearances.py` com testes e documentação operacional pertinente; inspecionar estrutura existente antes de criar. Justificar expansões.

Não implementar scheduling_repeat/documentos recebidos, purga de grupo S3, UI de conferência fraca ou nova triagem clínica. Não usar encerramento administrativo ou forçar reservas. Nenhum novo estado FSM, campo/tabela/dependência fora de D2. S1 não reabre por notas preexistentes. Mudança clínica/segurança/persistência não prevista exige decisão humana.

P2 de higiene não é autorização automática de escopo: avaliar AUD-12 (duplicata morta) e AUD-13 (driver supersedido) com justificativa e aprovação apropriada. Driver direto não é evidência aceita nem deve ser indicado como entrypoint. Obrigações expressas da matriz, inclusive asserção vácua e cobertura residual, NÃO podem ser dispensadas por rótulo P2.

## 6. Failing-before plan
Para CADA correção atual, teste comportamental primeiro, executar/preservar RED do defeito presente, mínimo para GREEN, refactor e rerun afetado. Não fabricar RED desfazendo código nem confundir erro de setup com falha-alvo.
- A: forte+fraco/conflito/outra fila seleciona indevidamente; empate aceita anterioridade inversa.
- B: deriva nome/grupo não aborta; trace não detecta row lock prévio; interleavings PG reais violam invariantes. Injetar deriva nas 3 tentativas totais e provar desfecho FAILED limpo do incoming em processamento, prior intacto, guardas/própria lease e contagem LLM sem repetição; caminhos prontos/retry preservam estado com erro explícito. Barreiras determinísticas/conexões separadas, asserts de resultado, timeout que detecta deadlock sem sleep como sincronização principal.
- C: segunda resolução pending levanta IntegrityError; revisão reseta orçamento/dedupe colide; job após exhausted/inelegível aplica; erro real de agenda propaga ou simula sucesso. Cada seam tem comando/falha esperada distintos.
- D: falha não aparece em evento/HTML do incoming ou duplica no prior; comando inexistente/incorreto não recupera idempotentemente ou usa saída incorreta. Separar prova de ausência da capacidade e testes funcionais após criação; não usar só ImportError como demonstração integral. Provar códigos 0/1/2 e ausência de escrita na simulação.
- E: payload vácuo, links/400/guards incompletos e espera/ordenação/abas sem prova; adicionar regressões reais antes das correções e completar cadeia de três/tempo congelado.
- F: disposição física/marcadores/worker e métricas nos dois modos sem prova; fixtures com arquivos/decisões reais e asserts que falhem no defeito presente ou em regressão. Testes/documentação pertencem a E/F, não à integração final.

A exceção humana do RED original S2 é SOMENTE histórica e não se estende a novas correções. Testes já existentes podem provar comportamento preservado sem forçar RED artificial.

## 7. Implementation constraints
Follow the repository engineering policy in `AGENTS.md` and preserve existing conventions. Prefer the smallest correct change; avoid speculative abstraction and unrelated refactoring.
Não baixar gates de completude/autoridade. Não adquirir mutex depois de row lock nem expandir grupo travado. Mesma regra de seleção/revalidação em todos os entrypoints, sem copiar protocolos divergentes. Nenhuma rede/storage delete sob lock. S2 só aplica forte único médico: candidato em agendamento apenas participa da ambiguidade, nunca é encerrado em S2. Recovery/command não são botões NIR nem novas análises LLM. Não desativar guards ou relaxar assertions para obter GREEN.

## 8. Validation
**Por unidade:** testes focused da matriz + afetados, RED/GREEN/REFACTOR e documentação própria. **Gate obrigatório:** AGENTS.md; comandos/exits reais, sem máscara por tail/grep. Teste sensível à env pode executar isolado, contabilizando TODOS os testes distintos, sem waiver silencioso. Migration parity e histórico efetivamente aplicado no banco isolado devem ter evidências separadas (warning de conexão em makemigrations não prova aplicação).

**PG:** validar matriz R4.3 com conexões distintas/barreiras e múltiplas execuções estáveis; teste de mutex captura SQL desde ANTES da aquisição. Somente resolve×resolve não prova o atomic final real.

**Runtime:** verify-hmd oficial, build FINAL, sintético, 1280/390: original pendente→novo processado pelo domínio/orquestrador real→original histórico vinculado→um card novo com espera herdada; história/aviso de suporte nos detalhes NIR e gerencial, guards/alerta regularizado. Seed é setup; navegador prova ação/estado resultante, não upload/processamento não executado. Cadeia de três obrigatória em HTTP e fila HTML; declarar limites de single-hop no browser. Entry via control-hmd drive (nunca driver direto), Chromium pinado/login real/assets/posse/cleanup e artefatos preservados. Comando de recuperação exige evidência de simulação/executar/repetir/recusas e agenda real do cluster llm. Não usar DB dev/prod.

## 9. Acceptance criteria
- Cada linha R1.1–R5.3 da matriz foi classificada e demonstrada por fonte/assertions/log/runtime aplicável no MESMO estado final.
- Zero P0/P1 abertos; nenhum requisito obrigatório sem prova ou dispensado por rótulo P2. Notas opcionais têm disposição explícita.
- Revisão independente integral cobre a matriz INTEIRA e inventário atribuído, não só último fix; inspeciona artefatos brutos e registra o que não pôde auditar. Não parar no primeiro achado. Auditoria incompleta não suporta aceite.
- Head/index/trabalho preexistente preservados, delta atribuível e pre/post revisão sem mutação, nenhum writer ativo; gate/PG/runtime finais verdes e autênticos.
- S2 integral só aceito após todas essas condições. Alteração de tasks/commit não equivale a aceite; ninguém marca linhas/unidades completas para legitimar implementação parcial.

## 10. Escalation conditions
`BLOCKED_NEEDS_DECISION` para contradição, decisão nova de produto/arquitetura, expansão material, compatibilidade/persistência/FSM não prevista, segurança/privacidade/integridade/clínica, impossibilidade de prova ou orçamento de revisão esgotado. Não tomar lease viva, ressuscitar terminal ou dispensar corrida/critério para encerrar unidade. Revisão de plano não autoriza execução.

## 11. Evidence report
Controller fornece caminho externo ÚNICO por fase com binding real de output; não sobrescrever `/tmp/sirhosp-slice-002-report.md` preexistente nem reports/logs anteriores. Report compacto completo READY_FOR_REVIEW/BLOCKED_NEEDS_DECISION com appendix separado se necessário; matriz linha→fonte/teste/RED/GREEN/exits/estado/runtime, alterações/justificativas/limites/decisões. Claims devem distinguir EXECUTADO, AUDITADO e NÃO VERIFICADO; relatório truncado não é prontidão.

## 12. Worker handoff
Implement only this approved slice **when separately authorized**, using this revised contract and referenced artifacts, not conversation or historical hashes as current authority. Work only on the authorized remediation unit, preserve original base/delta/evidence, deliver genuine failing-before and current passing evidence, focused/project/runtime gates. Do not update tasks, stage, commit, delegate, archive or start another slice. New decision: BLOCKED_NEEDS_DECISION; otherwise READY_FOR_REVIEW with actual REPORT_PATH. Nenhuma chamada de apply é autorizada por esta revisão documental.
