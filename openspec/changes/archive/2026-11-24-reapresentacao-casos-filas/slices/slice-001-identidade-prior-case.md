# Slice 001 — Identidade e contexto de decisão anterior

## 1. Identity
Change: `reapresentacao-casos-filas`. Task: [1.1](../tasks.md#1-s1--identidade-e-prior-case). Sem dependências de implementação.

## 2. Objective
Um novo relatório sem nascimento reconhece decisão anterior por nome+regulação/CNS, dentro de 15 dias, com contexto seguro ao médico e LLM2.

## 3. Read first
- `AGENTS.md`, `PROJECT_CONTEXT.md`, `openspec/config.yaml`.
- `../proposal.md`, `../design.md` D1–D3 e Migration Plan.
- `../specs/case-reappearance/spec.md` identidade, idade e CNS; `../specs/llm-pipeline/spec.md`; `../specs/doctor-decision/spec.md` card prior-case.
- `apps/pipeline/prior_case.py`, `apps/pipeline/tests/test_prior_case.py`, `apps/pipeline/tests/test_llm2.py`.
- `apps/anonymization/{deterministic,services,recognizers}.py`, respectivos testes; `apps/intake/services.py` reset de documento.
- `apps/doctor/presenters.py`, `apps/doctor/tests/test_detail.py`; `config/settings/base.py`.
Paths relativos deste contrato resolvem sob o diretório slices; arquivos de código são relativos à raiz do projeto.

## 4. Requirements
- R1: CNS somente do bloco textual fechado de D1 (revisão estrutural aprovada após review3): título inteiro, labels Paciente/Código corroborantes, assinatura→CNS→Dias em tela e término Nome Social, sem atravessar seção clínica. Demografia/campos soltos não abrem bloco; nenhuma heurística de distância. Evidência: fixtures sintéticas derivadas da ordem textual observada em 31 páginas, positivos repetidos/valor vazio/inválido/ambíguo, negativos de narrativa-demografia e de cada marcador ausente/fora de ordem. Extrator + wrapper real + migration; reset IMEDIATO no resubmit e sobrescrita vazia posterior permanecem.
- R2: classificador único implementa matriz D1, vazio/idade zero/conflito/tipos efetivos. Evidência: tabela parametrizada com nome normalizado, CNS, regulação, origem, idades 0/null/±1/±2 e multiprocedimento legado.
- R3: prior-case forte por procedimento na janela fechada de 15 dias, sem futuro/próprio; mais recente e contagem mantidas. Fraco não confirmado não vira contexto. Evidência: testes lookup/LLM2 incluindo CLEANED minimizado e nº em 14 dias antes rejeitado pelo default antigo.
- R4: card mostra motivo real/origem/referência desambiguada com códigos fechados occurrence_number/patient_cns/nir_confirmed/none (nir_confirmed será consumido só em S5); LLM recebe somente motivo anonimizado. Evidência: integração presenter e varredura recursiva payload externo.
- R5: flag false usa lookup legado 7/15 e impede novos efeitos; flag true usa SOMENTE janela nova default 15 e identidade ampliada. Evidência: override false/true em prior_case, defaults prod false/dev-test true, .env.example com depreciação das settings antigas e teste de parsing compose cobrindo passthrough explícito em web/pdf/anonymization/llm/attachments (settings legadas em web/llm). Nenhuma janela antiga concorre no modo novo.

## 5. Expected blast radius
Esperados: `apps/cases/reappearance.py` (novo classificador), `apps/cases/models.py`, migration aditiva, anonimização/extrator/reset intake, `prior_case.py`, presenter médico e seus testes, settings, `.env.example`, `docker-compose.prod.yml` OBRIGATÓRIO para passthrough, manual existente em templates/accounts.
Sensíveis: prompts, schemas, policy clínica, FSM, closure, acesso scheduler. Não mudar neste slice. Justificar arquivos adicionais no relatório.

## 6. Failing-before plan
RED original: adicionar casos em `apps/pipeline/tests/test_prior_case.py` para CNS sem nascimento e regulação em 14 dias; teste wrapper e negativo narrativo antes de produção. Preservar todos os logs anteriores. Na reparação estrutural aprovada, criar regressão falhando da demografia/CNS em narrativa (repro review3), além de blocos parciais/ordem incorreta, ANTES de alterar extrator. Adaptar somente fixtures positivas que dependiam da antiga proximidade para formato completo D1, sem retirar asserções de checksum/persistência/reset/privacidade. Registrar adaptação deliberada e comportamento desconhecido→vazio, não falsificar RED por fixture/schema impossível.

## 7. Implementation constraints
Follow the repository engineering policy in `AGENTS.md` and preserve existing conventions. Prefer the smallest correct change; avoid speculative abstraction and unrelated refactoring.
Não criar vínculos/encerramento/espera antecipadamente. Match fraco permanece sem efeito até S5. Reusar `valid_cns`; nunca inferir nascimento nem copiar CNS narrativo. Extrair do texto já limpo, não usar coordenadas nem processar amostras reais em testes/LLM. Remover a heurística antiga de proximidade se ficar morta; não preservar fallback para layouts parciais. Sem backfill lendo PDFs históricos. Nome sempre necessário, inclusive quando nº igual. Campo CNS varchar(15) permanece: padding persistido de CNS válido é irrepresentável, usar controles válidos e prova pura de strip, sem migração extra.

## 8. Validation
**Focused:** pytest prior_case, llm2, anonimização e detalhe médico; `makemigrations --check --dry-run` e migração em banco de testes isolado.
**Project-required gates:** quality gate de AGENTS.md antes de READY_FOR_REVIEW.
**Runtime:** renderização HTTP do detalhe médico com fixture sintética, origem correta e nenhum evento criado pelo GET; flag false reproduz lookup legado e não cria novos efeitos de reapresentação.

## 9. Acceptance criteria
- [ ] R1 prova bloco D1 completo, rejeição de narrativa-demografia e formatos parciais, sem proximidade/reabertura fraca; persistência/reset/privacidade continuam corretos.
- [ ] R2 cobre a matriz e não resolve tipos diferentes.
- [ ] R3 demonstra fronteiras 15 dias/futuro/mais recente/contagem.
- [ ] R4 distingue motivo interno de payload externo.
- [ ] R5 prova lookup legado/nova janela e parsing de env em todos os serviços; todos os gates passam.

## 10. Escalation conditions
Parar com `BLOCKED_NEEDS_DECISION` por contradição spec/design, nova decisão de produto/arquitetura, expansão material, mudança pública não aprovada, persistência/FSM além da adição autorizada, nova implicação de segurança/privacidade/integridade/clínica ou impossibilidade de cumprir contrato. Não inventar campos obrigatórios nem afrouxar anonimização.

## 11. Evidence report
`/tmp/sirhosp-slice-001-report.md`: status READY_FOR_REVIEW ou BLOCKED_NEEDS_DECISION; R→evidência; arquivos alterados; failing-before e passing-after; comandos/exit status; runtime; expansões justificadas; riscos/limitações e decisões humanas. Não duplicar diff.

## 12. Worker handoff
Implement only this approved slice. Reconstruct context from listed files, not conversation. Follow repository engineering policy. Produce failing-before evidence, smallest correct change, focused validation, project gates and required report. Do not update tasks.md, commit, push, merge, archive or start another slice. Stop on a new decision with BLOCKED_NEEDS_DECISION; otherwise READY_FOR_REVIEW and REPORT_PATH=/tmp/sirhosp-slice-001-report.md.
