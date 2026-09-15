# Design: sesab-header-extraction

## Contexto — layout real confirmado (corpus do piloto, extração posicional)

O cabeçalho padrão SESAB repete-se por página. Na extração linear do PyMuPDF
(`extracted_text`), rótulos e valores desalinham sistemicamente (o PDF é uma
tabela). Estrutura observada (1ª página; as demais repetem o bloco):

```
L01  RELATÓRIO DE OCORRÊNCIAS
L02  5040778                          ← valor de Código (antes do rótulo)
L03  <NOME COMPLETO> - Idade: 79a. - Sexo: F - Raça/Cor: Parda   ← valor de
     Paciente + demografia, na linha ANTES do rótulo
L04  Paciente:                        ← rótulo sozinho
L05  Abertura:
L06  ##/##/####                       ← valor de Abertura (data)
L07  Código:                          ← rótulo (2ª ocorrência, sem valor)
L08  ##:##                            ← hora
L09-11 Governo/Secretaria/Central (institucional)
L12  CNS:###############              ← rótulo+valor NA MESMA linha
L13  Dias em tela:
L14  #
L15  Data Adm. Unid.:
L16  ##/##/####
L17  Dias Unid.:
L18  ##
L20-24 Pág/Data/Hora/Nome Social:     ← rótulos sozinhos
```

Verificação dupla no corpus: as 8 palavras do valor visual `Paciente:`
aparecem no `extracted_text` nas linhas do cabeçalho (L3/L87/L173); o
`_PATIENT_NAME_FIELD_PATTERN` atual (valor na mesma linha do rótulo) nunca
casa — por isso o nome vai ao LLM em claro.

## D1 — Nome do paciente: âncora na linha de demografia COMPLETA (deterministic.py)

A âncora anti-falso-positivo é a **linha de demografia canônica do cabeçalho**:
linha que contém, NA MESMA LINHA e nesta ordem, os três marcadores
`Idade:\s*(\d+)\s*a\.?`, `Sexo:\s*([FM])` e `Ra[çc]a/Cor:\s*(enum)` — uma
menção clínica isolada de idade (ex. «História clínica Idade: 79a.») NÃO casa.
O pattern da linha de demografia tem **fonte única em
`apps/intake/pdf_utils.py`** (D3) e é importado pelo `deterministic.py`
(mesma direção do reuso vigente de `extract_agency_record_number`).

`extract_patient_name` ganha a âncora, ANTES dos rótulos existentes (que
permanecem para outros layouts):

- linha que casa a demografia canônica E cuja linha SEGUINTE é o rótulo
  `Paciente:` sozinho (fold, dois-pontos opcionais);
- nome = prefixo da linha até ` - Idade:` (ou `Idade:`), strip;
- validação: ≥ 2 palavras com letras; senão descarta e segue (páginas
  repetidas dão múltiplas chances).

Fixture adversarial obrigatória: demografia clínica órfã («…Idade: 79a.» sem
Sexo+Raça/Cor na mesma linha, seguida de «Paciente:») NÃO gera nome.

Primeira ocorrência válida vence (paridade com os rótulos existentes).
`patient_birth_date` permanece pelo pattern vigente (layout que o traga);
o relatório SESAB real não o tem — campo documentado como não-populado.

## D2 — Nome social: candidato PESSOA, não linkage (captura conservadora)

`DeterministicExtraction` ganha `social_name: str | None`. Captura
**apenas o valor na MESMA linha após `Nome Social:`** quando o rótulo tem
valor à direita; validação: ≥ 1 palavra alfabética (≥ 2 letras, sem
dígitos/símbolos) e diferente do nome civil (fold). Rótulo sozinho/valor
vazio → `None` — **sem fallback de linha anterior** (especulativo e
propenso a capturar texto clínico não-nominal; o corpus real tem o campo
vazio e não calibra o desalinhamento). Limitação documentada: se o layout
real preenchido divergir (valor desalinhado), fica NÃO-capturado — o aceite
operacional com PDF real é o detector. Vira candidato PESSOA em
`_deterministic_candidates` (token próprio distinto) e NÃO alimenta
linkage. Fixture sintética versionada cobre preenchido-na-mesma-linha e
vazio-após-texto-clínico.

## D3 — Metadados do cabeçalho (intake/pdf_utils.py, molde ats-web)

`extract_header_metadata(text) -> HeaderMetadata` (dataclass congelável),
por parsing **linha a linha da linha de demografia canônica** (os três
marcadores NA MESMA LINHA, na ordem — anti-falso-positivo clínico):
- `age`: `Idade:\s*(\d+)\s*a\.?`;
- `gender`: `Sexo:\s*([FM])`;
- `race`: `Ra[çc]a/Cor:\s*(enum)` — **enumeração explícita do padrão IBGE +
  ausência** (`Branca|Preta|Parda|Amarela|Indígena|Não informado`,
  case-insensitive/acentuação tolerante) — captura livre de «primeira
  palavra» engoliria mal ou rejeitaria acentuados/compostos;
- `days_on_screen`: `Dias em tela:\s*(\d+)` — **maior** ocorrência (molde
  ats-web `extract_regulation_days_on_screen`); termo administrativo do
  cabeçalho, improvável em texto clínico;
- ausentes → `None` (campos nullable). Abertura/Data Adm. Unid./Dias Unid.
  ficam NO TEXTO (não persistem — sem uso previsto; decisão de mínimo
  necessário).

O pattern da linha de demografia canônica vive AQUI (fonte única) e é
importado por `apps/anonymization/deterministic.py` para a âncora do nome
(D1).

Campos novos em `Case`: `patient_age` (PositiveSmallIntegerField null),
`patient_gender` (CharField(16) blank), `patient_race` (CharField(32)
blank), `days_on_screen` (PositiveSmallIntegerField null) — migration única.

## D4 — Persistência: worker pdf escreve metadados; linkage segue da
anonimização (writer único por campo)

- `apps/intake/tasks.py` (`_extract_and_decide`): no mesmo passo que grava
  `extracted_text`/`agency_record_number`, persiste os 4 metadados
  (`extract_header_metadata` sobre o texto extraído). Sem eventos com os
  valores (payload PHI-free como todo evento).
- `patient_name`: continua sendo escrito SÓ pela anonimização
  (`anonymize_case_text`, linkage da extração) — nenhum writer novo; agora a
  extração acha o valor no layout real, o contrato vigente
  («sobrescrito pela extração») popula o campo corretamente.
- Reprocessamento: extração determinística é estável (mesmo texto) — o
  linkage e os metadados re-persistem iguais.
- **Reenvio de documentos** (`_RESUBMIT_CLEARED_FIELDS`,
  `apps/intake/services.py`): os 4 metadados entram na lista (derivados do
  documento corrente, mesma classe de `extracted_text`/`agency_record_number`
  — `None` para os inteiros, `""` para os chars), zerados na MESMA transação
  vigente do reenvio; se o novo PDF falhar, não restam metadados órfãos do
  documento anterior. `patient_name` NÃO entra (writer único da anonimação —
  D4; residual pré-existente do contrato vigente, fora de escopo).

## D5 — Tokenização fechada pelo mecanismo existente

`_deterministic_candidates` já tokeniza TODAS as ocorrências de
`extraction.patient_name` (varredura folded) — com a âncora D1, as
ocorrências do nome nas 3+ páginas do cabeçalho viram `<PESSOA_1>`. Nome
social (D2) idem com token próprio. CNS/ocorrência seguem como hoje.
Demografia (idade/sexo/raça) NÃO é tokenizada: dado clínico necessário ao
LLM (postura ats-web), sem valor identificatório isolado. O guard
`_assert_tokens_only` segue intocado (o mapa só cresce com entradas usadas).

## D6 — Privacy dos novos campos

- `patient_age/gender/race/days_on_screen` = PHI demográfico estrutural,
  mesma classe de `patient_name`/`agency_record_number`: sobrevivem ao
  CLEANED (NÃO entram em `_CLEANED_EMPTY_VALUES`) — histórico
  administrativo; paridade vigente do encerramento (ack e administrativo
  usam a MESMA limpeza). `days_on_screen` é métrica de gestão (sort de
  filas), não dado clínico.
- O texto (com nome em claro) continua existindo no `extracted_text` até o
  CLEANED — status quo; a tokenização protege o perímetro LLM.

## D7 — Aceite: benchmark + fixture + E2E

- Fixture versionada (`benchmark_corpus.jsonl`): entradas sintéticas com o
  LAYOUT real do cabeçalho (nome desalinhado na linha de demografia, nome
  social preenchido e vazio, múltiplas páginas) — expected PESSOA 100%.
- Aceite operacional (local, fora do CI): corpus REAL do piloto (dev) —
  recall PESSOA 100%, texto anonimizado sem o nome (varredura), metadados
  populados no caso.
- E2E dev: reenvio do PDF real → caso com
  nome/idade/sexo/raça/dias_em_tela + mapa `<PESSOA_1>` + card de
  identificação do médico populado (mata a demonstração dos backlog 1-2).

## D8 — Não-mudanças

- Formulário de upload intocado (sem captura manual — o relatório tem tudo).
- Contrato do linkage inalterado (extração vence/sobrescreve — agora
  funcionando; sem preservação de valor declarado, que não existe mais).
- Patterns vigentes de rótulo-nome/nascimento mantidos (outros layouts);
  NER segue opt-in desligado; `_assert_tokens_only` intocado; sem mudança
  nas specs de closure/minimização (campos novos seguem a política de
  paridade por decisão D6, sem entrar na limpeza).
