# Design: queue-cards-wait-time

## Contexto

- Fila médica: `apps/doctor/views.py` `_queue_cases` (~141-156) retorna
  `order_by("created_at", "case_id")`; items renderizados em
  `templates/doctor/queue.html` (nome, nº ocorrência, recebido em,
  procedimentos).
- Fila do agendador: `apps/scheduler/views.py` `_build_case_card`-like row
  (~150-170: case_id/status_label/patient_name/agency_record_number/
  created_at/unit_label/procedures) + `queue` (~174-187,
  `order_by("created_at", "case_id")`); template `scheduler/queue.html`.
- Meus casos: `apps/intake/views.py` `my_cases` (~296-320,
  `-created_at`), cards em `templates/intake/my_cases.html` (uid/status/
  gate; sem paciente).
- Campos: `patient_name`/`patient_age`/`days_on_screen` (change
  `sesab-header-extraction`).

## D1 — Ordenação por tempo de tela (molde ats-web)

Filas de espera do médico e do agendador (TODAS as abas — a função de
queryset é única e o tempo de tela persiste pós-decisão):
`order_by(F("days_on_screen").desc(nulls_last=True), "created_at", "case_id")`
— maior tempo de tela oficial primeiro; sem cabeçalho (`None`) ao fim,
FIFO entre si. Mesma expressão nas duas filas (duplicada nos dois apps —
mesmo padrão do repo; sem helper compartilhado novo para não criar
acoplamento por 1 linha).

## D2 — Cards das filas de espera

- Idade junto do nome: `84 a` quando `patient_age` presente; nada quando
  ausente (não exibir `— a`).
- Linha de tempo: `⏱ Aguardando há {{ created_at|timesince }}` (sempre) +
  `· {{ days_on_screen }} d em tela` quando presente (fonte oficial do
  relatório — distinta do tempo de espera no HMD).
- Nada mais muda nos cards (status, procedimentos, nº ocorrência,
  unidade).

## D3 — Meus casos (NIR)

Cards ganham nome (`{{ patient_name|default:"—" }}`) e idade; a linha de
tempo NÃO entra (histórico com `-created_at`, não fila de espera). O NIR é
o criador do caso e manuseia o relatório físico do paciente — a
identificação na SUA lista é minimização compatível (escopo por criador
vigente; caso alheio continua 404). O detalhe do NIR não muda (item 4 do
backlog trata da trilha; identificação no detalhe fica para demanda
futura).

## D4 — Não-mudanças

- Painel manager/admin: zero-PHI intacto (nº de ocorrência; sem
  nome/idade/dias).
- Fila médica: acesso por subtipo, contagens por aba, abas — intocados.
- `days_on_screen` não é atualizado dinamicamente (é o valor do último
  processamento do relatório — snapshot fiel ao cabeçalho).
