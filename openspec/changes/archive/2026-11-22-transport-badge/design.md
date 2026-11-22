# Design

## Context

O worker de PDF (`apps/intake/tasks.py`, `process_case_documents`) já extrai,
de forma determinística sobre o texto limpo, os metadados do cabeçalho SESAB
(`extract_header_metadata` em `apps/intake/pdf_utils.py`) e os persiste no
mesmo write transacional do `extracted_text` (writer único sob lock, update
explícito de campos). As três filas (NIR, doctor, scheduler) montam os cards
nas views e renderizam demografia/metadados com badges Bootstrap. O label
`TRANSPORTE:` é um campo estrutural do bloco «RELATÓRIO MÉDICO» do relatório
de regulação — verificado no corpus real (9/9 PDFs capturados por regex
linha-âncorado sobre texto PyMuPDF; formas: `Ambulância Básica`, `UTI Móvel`,
`BÁSICA`).

## Goals / Non-Goals

**Goals:**

- Extração determinística, auditável e barata do tipo de transporte, no molde
  dos metadados do cabeçalho (função pura em `pdf_utils.py`).
- Sinal de gravidade visível nas três filas e no detalhe médico, com paridade
  de apresentação entre papéis.

**Non-Goals:**

- Mudar pipeline LLM (prompts, schemas, policy, `summary_text`) — o badge é
  metadado determinístico, não conteúdo clínico do LLM.
- Extrair transporte de texto narrativo («Transporte em ambulância avançada»)
  ou de anexos OCR.
- Exibir transporte nos detalhes NIR/agendador/painel (extensão futura se o
  dono pedir; cards + detalhe médico cobrem a demanda).
- Regra «mais grave vence» para ocorrências múltiplas (ver D2).

## Decisions

### D1 — Extração determinística no worker de PDF, sem delimitação de seção

**Decisão:** função pura em `apps/intake/pdf_utils.py` com regex linha-âncorado
`^[ \t]*TRANSPORTE[ \t]*:[ \t]*(.*?)[ \t]*$` (multiline, case-insensitive;
whitespace HORIZONTAL — `\s` consumiria a quebra de linha e violaria o
contrato «valor na mesma linha», capturando valor da linha seguinte) sobre o
texto limpo do caso inteiro — **sem** delimitar o bloco «RELATÓRIO MÉDICO»;
normalização com o helper `_fold_line` (sem acento/minúscula/colapsado):
`ambulancia basica|basica` → `basic`; `uti movel` → `mobile_icu`. Persistência
no campo novo `Case.transport_type` (`CharField` com choices `basic|mobile_icu`,
`null=True`), reatribuído no mesmo `update_fields` do write existente.

**Por que sem delimitação de seção (revisão independente):** o label é autorado
no bloco do relatório médico, mas delimitar a seção sobre texto de scanner
multi-PDF exige parsing frágil (âncoras de seção variam entre layouts) para um
dado de triagem consultiva — o PDF original é a fonte. Na prática, citações de
solicitação anterior aparecem no «Complemento da Solicitação», ANTES dos blocos
de evolução, então a regra última-ocorrência já prefere a evolução atual
(cenário dedicado na spec). O contrato da spec segue o implementável: label
estruturado no texto do caso, última ocorrência vence.

**Alternativas:** (a) campo novo no schema LLM1 com evidence span — rejeitado:
exige prompt/schema/reconciliação para um dado estrutural estável, com custo de
latência e variabilidade; (b) extração na renderização — rejeitado: recalcular
regex por request e sem persistência para históricos/detalhes; (c) delimitação
da seção do relatório médico — rejeitada pela fragilidade acima.

**Por que no texto limpo (pós-watermark):** o label nunca contém a marca nem o
nº; rodar após o `strip_watermark` segue o molde dos metadados do cabeçalho e
evita falsos positivos em linhas de marca.

### D2 — Ocorrências múltiplas: última vence, na ordem declarada dos documentos

**Decisão:** com N ocorrências do label, a última do texto do caso vence. O
texto do caso é `extracted_text`, concatenado na ordem declarada dos documentos
(`CaseDocument.position` — ordem do scanner/NIR, fonte única já contratada
pela spec de extração) — a regra opera exatamente sobre a mesma fonte dos
demais extratores, sem ordenação nova. Evoluções são cronológicas dentro do
relatório; citações de solicitação anterior vêm antes dos blocos de evolução,
logo a última ocorrência reflete o transporte atualmente solicitado. No corpus
não há conflito semântico real (`BÁSICA` e `Ambulância Básica` normalizam
igual).

**Alternativa:** «mais grave vence» (qualquer `UTI Móvel` domina) — rejeitada
por agora: pode perpetuar transporte de evolução superada. Se o dono preferir,
é troca de uma linha na função pura + testes.

### D3 — Domínio fechado com rejeição silenciosa de desconhecidos

**Decisão:** valor fora do domínio (ex.: `Aéreo`) → campo vazio, sem badge,
sem erro, sem retenção no gate. O desconhecido NÃO é persistido nem logado em
evento (paridade com metadados ausentes). Telemetria de aprendizado do domínio
real fica como extensão futura (Non-Goal) — o corpus de 9 docs não justifica
infra de telemetria ainda.

**Trade-off:** variantes novas no piloto não geram badge até normalizadas —
aceitável: ausência nunca é mentira; o PDF original permanece acessível.

### D4 — Apresentação: badge nas filas, linha no detalhe médico

**Decisão:** nas três filas, badge Bootstrap rounded-pill junto da linha de
metadados do card: `UTI Móvel` com classe de ênfase (`text-bg-warning`) e
`Ambulância Básica` neutra (`text-bg-info`); rótulos canônicos («UTI Móvel»,
«Ambulância Básica») vivem no próprio model como `TextChoices` de
`Case.transport_type` (`get_transport_type_display()` no molde de
`get_status_display()`); o mapeamento de classe CSS é um dict único (ex.:
`TRANSPORT_BADGE_CLASSES` exportado de `apps/cases/models.py` junto das
choices — fonte única anti-drift consumida pelos três templates). Ausente →
sem badge. No detalhe médico, linha rotulada no card de identificação com
placeholder «—» (padrão do card). Ícone 🚑 opcional no badge, alinhado ao 🏥
da unidade de origem.

**Alternativa:** badge somente no doctor — rejeitada: a demanda é visibilidade
para todos os papéis que triam o caso.

### D5 — Migração aditiva, sem backfill

**Decisão:** migration apenas cria a coluna (`transport_type` nulável). Sem
backfill: casos já processados ficam sem badge (campo vazio é estado legítimo);
reprocessamentos futuros (reenvio) populam. O PDF original segue disponível
para consulta manual dos casos antigos.

**Alternativa:** backfill varrendo `extracted_text` dos casos ativos —
rejeitado: mutate-em-massa de produção fora do fluxo auditado do worker para
um dado consultivo; casos ativos transitam rápido para CLEANED.

### D6 — `transport_type` sobrevive à minimização do CLEANED

**Decisão:** o campo é metadado administrativo não-identificante (categoria de
recurso solicitado, não dado do paciente) e SHALL ser preservado na
minimização ativa, no molde dos metadados do cabeçalho (`patient_age`,
`origin_unit` etc. também sobrevivem — nenhum entra em
`_CLEANED_EMPTY_VALUES`, `apps/cases/closure.py`, que zera apenas textos e
artefatos PHI-derivados). Zerar acontece apenas pelo reenvio de documento
(worker reatribui a cada extração). **Sem mudança em `closure.py`** — a
ausência do campo do dict é a preservação; teste dedicado trava o contrato
contra drift futuro.

**Alternativa:** zerar no CLEANED — rejeitada: quebraria a paridade com os
demais metadados administrativos sem ganho de privacidade (o valor não
identifica o paciente).

## Risks / Trade-offs

- [Variantes de escrita fora do corpus aparecem no piloto] → rejeição
  silenciosa mantém honestidade (sem badge ≠ erro); normalizador centralizado
  em um único ponto facilita extensão; cenário de valor desconhecido coberto
  por teste.
- [`UTI Móvel` em evolução antiga seguido de `Básica` na atual gera badge
  «menos grave»] → decisão D2 é a leitura cronológica correta; revisão de
  regra é one-line se o dono pedir.
- [Label dentro de citação de relatório anterior] → resolvido por construção:
citações vêm antes das evoluções no texto e a última ocorrência vence (cenário
dedicado); resíduo teórico (citação APÓS a última evolução) aceito — badge é
  sinal de triagem, o PDF é a fonte.
- [Falso negativo por quebra de linha no valor] → não observado no corpus com
  PyMuPDF (pdftotext sem -layout quebra em acento, o app não usa); regex
  âncorado por linha segue o padrão dos demais extratores.

## Migration Plan

1. Migration aditiva (coluna nulável) — deploy seguro antes/Depois do código
   (sem dependência entre si; coluna nova com default nulo).
2. Código (extração + UI) deployado em seguida; novos processamentos populam.
3. Rollback: reverter código; coluna órfã é inerte (nulável, sem constraint);
  drop opcional em limpeza futura.
