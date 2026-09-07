# Design: presidio-anonymization

## Contexto

Pesquisa web-verificada: `temp/research/presidio-django-ptbr.md` (Presidio 2.2.364 sob `data-privacy-stack`, MIT; recomendação #1: **bibliotecas embutidas em worker persistente**, não containers REST; spaCy `pt_core_news_lg` 3.8.0 ~541 MB; sem recognizers BR nativos → CPF/CNS/CRM próprios com checksum; pseudônimos estáveis por documento; fail-closed). Plano §6 (7 itens do dono) e roadmap row 05 (inclui harness de benchmark + ADR). Estado atual: caso chega a `ANONYMIZING` com `extracted_text` (change 04); transições `start_anonymization` (self) / `complete_anonymization` (→ `LLM_EXTRACTING`) / `fail_processing` existem (change 03).

### D1 — Bibliotecas embutidas em worker dedicado (não REST)

`presidio-analyzer==2.2.364` + `presidio-anonymizer==2.2.364` + `spacy==3.8.*` + wheel travado de `pt_core_news_lg` (URL do release do spaCy no `pyproject` — instalado no build, nunca em runtime). Engine **singleton por processo** (`lru_cache`) carregado apenas no worker `anonymization`; o processo web nunca carrega o modelo. Containers REST presidio rejeitados (pesquisa §2: só valem com múltiplos consumers/isolamento de rede — não é o caso; custo HTTP/auth/observabilidade). Alternativa transformers (BERTimbau/BioBERTpt/GLiNER) registrada como evolução pós-benchmark.

### D2 — Pré-extração determinística (`deterministic.py`)

Funções puras sobre `extracted_text`: nº de ocorrência (**reusa os padrões do `pdf_utils` do change 04** — import, sem duplicar), nome do paciente e nascimento (padrões de campos do relatório SESAB: "Paciente:"/"Nome:" e "Nascimento:"/"Data de Nascimento:" dd/mm/aaaa), CPF/CNS via os validadores de checksum de D3. Saída `DeterministicExtraction` congelável. Nome/nascimento persistem no `Case` (`patient_name`, `patient_birth_date`; consumers: prior-case 06, presenter 07) — migração incremental.

### D3 — Recognizers BR (`recognizers.py`)

Espelho do esboço da pesquisa, adaptado: `CpfRecognizer` (regex + **checksum** — sem checksum é falso positivo), `CnsRecognizer` (15 dígitos + soma ponderada 15..1 % 11), `CrmRecognizer` (padrão CRM/UF/número, sem validação oficial). Entidades `BR_CPF`, `BR_CNS`, `BR_CRM` (idioma pt). Validadores como funções puras testadas com valores gerados algoritmicamente nos testes (CPF válido gerado por código; nunca PII real). Caveats da pesquisa registrados (colisões processo/AIH vs CNS; contexto ajuda).

### D4 — Engine singleton (`engine.py` + `config/presidio-nlp-pt.yaml`)

`NlpEngineProvider` com YAML da pesquisa (mapping PER/LOC/ORG/DATE → PERSON/LOCATION/ORGANIZATION/DATE_TIME), `RecognizerRegistry` com predefined + recognizers BR, `AnalyzerEngine(supported_languages=["pt"])`, `AnonymizerEngine`. `score_threshold` por env `ANONYMIZATION_SCORE_THRESHOLD` (default 0.45 — pesquisa §5). Singleton `get_anonymization_engine()` com `lru_cache(maxsize=1)`; o carregamento do modelo é o custo dominante (~centenas de MB RSS) — só no worker. `ANONYMIZATION_SPACY_MODEL` (default `pt_core_news_lg`) permite trocar por `md` em ambientes enxutos.

### D5 — Pseudônimos estáveis: operador customizado (com spike e fallback)

`PseudonymOperator(Operator)` (presidio-anonymizer): **spike obrigatório no início do slice 003** contra `presidio-anonymizer==2.2.364` (registrar via `OperatorConfig("custom", {"operator": instância})` reutilizando a MESMA instância para todas as entidades; conferir se a ordem de chamada acompanha os results ordenados por offset). **Fallback declarado**: se a API não sustentar, substituição por código próprio sobre os results mesclados ordenados por offset (Presidio fica para a análise) — mesma saída contratual. Instância por caso; mantém `dict` valor→token por categoria (`<PESSOA_1>`, `<CPF_1>`, `<DATA_1>`, `<CRM_1>`, `<LOCAL_1>`, `<ORGANIZACAO_1>`, `<TELEFONE_1>`, `<EMAIL_1>`; numeração por primeira ocorrência); `operators` do `anonymize()` usa a mesma instância para as entidades do escopo. Após a chamada, o operador expõe o **mapa** (token → {valor real, tipo}). Escopo de entidades (allowlist — pesquisa §5): PERSON, LOCATION, ORGANIZATION, DATE_TIME, PHONE_NUMBER, EMAIL_ADDRESS, BR_CPF, BR_CNS, BR_CRM; `DEFAULT` → `<PII_N>`. Caveat de datas: `DATE_TIME` vira `<DATA_N>` (datas relativas como "há 3 dias" não são detectadas e permanecem — preservado o timeline clínico relativo); nascimento absoluto fica no `Case.patient_birth_date` para o linkage (06) sem precisar do LLM.

### D6 — Núcleo puro `anonymize_text` + wrapper `anonymize_case_text`

**Núcleo puro `anonymize_text(text) -> AnonymizationCoreResult`** (sem DB — consumido pelo benchmark): pipeline (1) `DeterministicExtraction` sobre `extracted_text`; (2) analyzer no texto; (3) **merge com política determinística completa**: localizar TODAS as ocorrências de cada valor determinístico no texto (`str.find` em loop → spans com offsets válidos); candidatar spans determinísticos com categoria própria (nome→PESSOA, nascimento→DATA, nº→OCORRENCIA, CPF→CPF, CNS→CNS) mesmo sem NER; ordenar todos os spans por `(start asc, end desc, determinístico > NER)`; resolver sobreposições mantendo o primeiro da ordem (mais à esquerda, mais largo, determinístico vence); spans descartados somem; offsets sempre validados contra o texto; (4) anonymizer com `PseudonymOperator`; (5) persiste `anonymized_text`, `pseudonym_map` (JSON), `anonymization_report` (JSON: contagens por tipo, modelo, versões presidio, threshold) + `patient_name`/`patient_birth_date`; (6) evento `CASE_ANONYMIZATION_COMPLETED` (payload enxuto: contagens + versões). Exceção em qualquer etapa propaga (a task converte em fail-closed). Novos campos de `Case` + migration + evento canônico em `apps/cases/events.py`. **Invariantes**: (a) `anonymized_text` vazio ⇒ pipeline não avança (o 06 lê APENAS `anonymized_text` — regra registrada aqui, consumida lá); (b) mapa nunca sai do perímetro (nenhum prompt recebe o mapa).

### D7 — Worker e trigger (`tasks.py` + signal)

Cluster `anonymization` no `ALT_CLUSTERS` do `Q_CLUSTER` (workers 2, timeout 300, retry 360 — pesquisa §10; separado do `pdf` para o modelo não competir com extração). **Trigger desacoplado, com guarda anti-recursão**: receiver do signal `CaseEvent.post_save` (padrão das comunicações) para `event_type == CASE_STATUS_ANONYMIZING` **E `payload["source"] == "PDF_EXTRACTING"`** (entrada REAL no estado — a self-transition `start_anonymization` da própria task emite `source=ANONYMIZING→ANONYMIZING` e **não** re-dispara; sem esse filtro, o modo inline recursaria com o lock na mão e o async duplicaria tasks). O enqueue é feito via **`transaction.on_commit`** (nunca dentro da transação que ainda vai commitar — rollback não enfileira). Cobre os 3 paths de entrada (extração concluída, gate liberado, reenvio reprocessado) sem tocar o intake. Task `process_case_anonymization(case_id)`: idempotente por estado (apenas `ANONYMIZING`; demais no-op), claim lock `worker_anonymization`/`system` (release no finally), `start_anonymization` (self/evento de início), **porteiro do texto vazio: `extracted_text == ""` → `fail_processing("sem texto extraído")`** (o núcleo do 003 é defensivo e não valida — quem falha fechado é a task); sucesso: `with transaction.atomic(): anonymize_case_text(case); case.complete_anonymization(...)` (persistência e transição atômicas — em falha nada foi escrito, `anonymized_text` permanece vazio); exceção → `fail_processing(motivo)` → `FAILED` (fail-closed). Flag `ANONYMIZATION_RUN_TASKS_INLINE` (base/teste `True` — determinístico; prod `False`) espelhando o padrão do change 04. Compose dev: serviço `worker-anonymization` (`Q_CLUSTER_NAME=anonymization`). **Imagem de worker com deps+modelo instalados no BUILD** (o `Dockerfile` atual só tem slim+uv e o compose sincroniza em runtime — isso muda no slice 004: `Dockerfile` instala `uv sync --frozen` no build, incluindo o modelo spaCy; workers (`worker-pdf`/`worker-anonymization`) passam a usar a imagem pronta em vez de sync no startup; o web dev mantém o fluxo atual de volume/runserver).

### D8 — Re-identificação (`reidentify.py`)

`reidentify_text(case, text)`: **uma única passada de regex** com alternation de todos os tokens (`re.sub` com callback pelo mapa — imune a substituições em cascata e a `<PESSOA_1>` casar dentro de `<PESSOA_10>`); testes cobrem valores que literalmente contêm um token no texto original. Serviço puro; **controle de acesso é dos consumers** (presenter do 07 permitirá doctor/manager/admin; NIR não re-identifica — decisão do dono). Roundtrip garantido pelo mapa 1:1 (testado).

### D9 — Benchmark harness (`management/commands/anonymization_benchmark.py`)

Corpus JSONL: `{"text": ..., "expected": [{"value": ..., "entity_type": ...}]}`. Para cada entrada: anonimiza (serviço D6 sobre texto puro, sem caso) e calcula: recall por tipo (valor esperado ausente do output = acerto), contagens de entidades, latência; agregados p50/p95 + varredura zero-PII (regex CPF/CNS com checksum no output) + **pico de RSS do processo** (`resource.getrusage`, critério operacional da pesquisa) + **documentos bloqueados** (entradas cuja anonimização erroa = fail-closed durante o benchmark — qualquer bloqueio reprova o comando). Exit ≠ 0 se recall de qualquer tipo < `ANONYMIZATION_BENCHMARK_MIN_RECALL` (default 0.90), vestígio encontrado, OU documento bloqueado. RSS é reportado (limite duro opcional via `ANONYMIZATION_BENCHMARK_MAX_RSS_MB`, default desligado). Corpus **sintético** versionado (`apps/anonymization/tests/fixtures/benchmark_corpus.jsonl` — dados fabricados: CPFs válidos gerados, nomes fictícios) roda na suíte (CI); aceitação com relatórios **reais** é passo operacional manual (pré-produção), documentado no ADR — critério do plano §6.7.

### D10 — ADR-0007 e variáveis

`docs/adr/ADR-0007-anonimizacao-presidio-fail-closed.md`: decisão (embedded worker engine, pseudônimos estáveis por caso, merge determinístico, fail-closed, benchmark como aceite), alternativas (REST containers; spaCy puro; transformers; LLM local), consequências (imagem ~700 MB no worker; recall clínico pt-BR não benchmark público — benchmark local obrigatório; mapa em claro no banco interno — decisão do dono registrada, revisitável com DPO). Env novas: `ANONYMIZATION_SCORE_THRESHOLD`, `ANONYMIZATION_SPACY_MODEL`, `ANONYMIZATION_RUN_TASKS_INLINE`, `ANONYMIZATION_BENCHMARK_MIN_RECALL` (+ cluster no `Q_CLUSTER`). `.env.example` atualizado.

## Riscos e mitigações

- **Recall clínico pt-BR não garantido fora-do-box** (pesquisa §8/13): merge determinístico cobre os identificadores canônicos; corpus real no benchmark pré-produção; revisão amostral humana documentada; falso positivo aceitável (recall > precisão antes de LLM).
- **Colisões CNS × nº de processo/AIH**: contexto + checksum; benchmark com amostras reais calibra.
- **Memória do worker**: singleton, workers 2, medir RSS no benchmark (relatório inclui).
- **Modelo no CI**: wheel pinado no lockfile (instalação determinística); testes de recognizer puros não carregam o modelo.

## Fora de escopo

Pipeline LLM (06), OCR/anexos (10), re-identificação em UI (07), recognizers extras (AIH/APAC/CEP) e transformers — pós-benchmark.
