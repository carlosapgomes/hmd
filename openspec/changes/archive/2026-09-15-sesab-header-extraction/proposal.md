# Proposal: sesab-header-extraction

## Why

Todo relatório PDF emitido pela Secretaria de Saúde traz, **em cada página**, um
cabeçalho padrão com os campos: Código (ocorrência), Abertura (data/hora),
Dias em tela, Data Adm. Unid., Dias Unid., **Paciente (nome)**, Idade, Sexo,
Raça/Cor, Nome Social (quando aplicável — pode estar em branco, não é o campo
de captura do nome) e CNS (pode estar em branco). **Não há data de
nascimento no relatório.**

A extração atual do HMD lê rótulo e valor como se estivessem na mesma linha —
na extração linear do PyMuPDF, o valor do campo `Paciente:` vem desalinhado
do rótulo (confirmado no corpus real do piloto, por extração posicional:
o nome completo está na linha da demografia, ANTES do rótulo `Paciente:`,
repetido em cada página). Consequências:

1. **Privacidade real**: o nome do paciente está no `extracted_text` e vai ao
   LLM **em claro** (o mapa só tokeniza CNS/ocorrência — a pré-extração de
   nome nunca acha o valor no layout real).
2. **Identificação cega**: `patient_name`/`patient_birth_date` vazios — o
   card de identificação do médico e as filas não mostram o paciente
   (backlog itens 1-2 do dono).
3. **Metadados administrativos perdidos**: idade/sexo/raça (demografia
   clínica) e dias em tela (tempo de tela oficial do regulador, usado pelo
   ats-web para ordenar filas) existem no cabeçalho e não são capturados.

A data de nascimento **não existe** no relatório — `patient_birth_date`
permanece não-populado por extração (a idade vem pronta no cabeçalho).

## What Changes

- **Extração determinística do cabeçalho padrão SESAB**: nome do paciente
  (campo `Paciente`, âncora na linha de demografia), nome social (quando
  presente), idade, sexo, raça/cor e dias em tela (maior ocorrência, molde
  ats-web) — patterns no texto extraído, sem captura manual no intake.
- **Novos campos no caso** (migration): `patient_age`, `patient_gender`,
  `patient_race`, `days_on_screen` — persistidos pelo worker pdf junto de
  `extracted_text`/`agency_record_number`; `patient_name` segue persistido
  pela anonimização (linkage, writer único).
- **Tokenização do paciente fechada**: nome e nome social extraídos viram
  candidatos PESSOA garantidos — todas as ocorrências (em todas as páginas)
  tokenizadas antes de qualquer LLM, pelo mecanismo determinístico existente.
- **Demografia não é tokenizada**: idade/sexo/raça seguem no texto ao LLM
  (dados clínicos necessários à análise, postura ats-web).
- **Benchmark com o corpus real** como aceite operacional (recall de PESSOA
  com o layout real) + fixture sintética versionada com o mesmo layout.

Sem captura manual no upload (o formulário não muda); sem nova UI (cards e
filas são os changes seguintes do backlog, que passam a ter dados).

## Impact

- **Specs**: `intake-nir` (extração no cluster pdf popula metadados do
  cabeçalho) e `anonymization` (pré-extração determinística cobre o layout
  real do cabeçalho + nome social).
- **Código**: `apps/intake/pdf_utils.py` (metadados), `apps/intake/tasks.py`
  (persistência), `apps/anonymization/deterministic.py` (nome/nome social),
  `apps/cases/models.py` (+migration com 4 campos).
- **Privacy**: nome/nome social tokenizados antes do LLM (corrige o
  vazamento do layout real); metadados demográficos são PHI estrutural leve
  nos campos do caso, sobrevivendo ao CLEANED por paridade com
  `agency_record_number`/`patient_name` (histórico administrativo).
- **Backlog**: desbloqueia itens 1-2 (identificação nas filas/detalhe, tempo
  de tela oficial) e mantém a base dos changes seguintes.
