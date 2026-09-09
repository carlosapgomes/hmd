"""Conteúdo versionado dos 29 prompts do seed (change llm-pipeline-per-type,
slice 003, R2 + change attachment-processing-ocr, slice 003, R3).

Sistema de nomes do design D3 (emenda aprovada no plano §7): systems NEUTROS
compartilhados por estágio (``llm1.system``/``llm2.system``) e users por tipo
(``proc.<type>.llm1.user``/``proc.<type>.llm2.user`` × 13 = 28 templates) —
a composição união exige UMA chamada por estágio, então o system não carrega
tipo. O 29º (``ATTACHMENT_VERIFICATION``, aditivo do change de anexos) fica
FORA do mecanismo por-perfil: um prompt único da verificação de patient-match
dos anexos clínicos, com os placeholders do texto anonimizado e do token do
paciente do caso.

Conteúdo clínico por tipo deriva da seção do tipo em
``temp/parametrosHMD.md`` (labels do catálogo em
``apps/cases/procedure_catalog.py``). Guardas transversais do design D3:
JSON estrito, idioma pt-BR, evidence obrigatória, nunca inventar, tokens
(ex.: ``<PESSOA_1>``) e status tri-state (``confirmado|nao_informado|incerto``).
"""

from __future__ import annotations

from apps.cases.procedure_catalog import PROCEDURE_PROFILES, get_procedure_profile
from apps.llm.models import SYSTEM_NAME_SUFFIX, USER_NAME_PREFIX, USER_NAME_SUFFIX

# Estágios do pipeline (design D3): llm1 = extração; llm2 = sumarização.
STAGES: tuple[str, ...] = ("llm1", "llm2")


def system_prompt_name(stage: str) -> str:
    """Nome canônico do system neutro do estágio (ex.: ``llm1.system``)."""
    if stage not in STAGES:
        raise ValueError(f"estágio de prompt desconhecido: {stage!r}")
    return f"{stage}.{SYSTEM_NAME_SUFFIX}"


def user_prompt_name(procedure_type: str, stage: str) -> str:
    """Nome canônico do user de um tipo (ex.: ``proc.art_perif.llm1.user``)."""
    if stage not in STAGES:
        raise ValueError(f"estágio de prompt desconhecido: {stage!r}")
    # Fail-fast como o catálogo: tipo fora do catálogo levanta KeyError.
    get_procedure_profile(procedure_type)
    return f"{USER_NAME_PREFIX}.{procedure_type}.{stage}.{USER_NAME_SUFFIX}"


# ── Systems neutros (papel + guardas por estágio) ───────────────────────────

_LLM1_SYSTEM_PROMPT = (
    "Você é o assistente de extração clínica da Hemodinâmica (HMD) — Hospital "
    "Geral Roberto Santos. Você recebe o texto ANONIMIZADO de um relatório de "
    "solicitação de procedimento e responde APENAS com o objeto JSON pedido no "
    "schema fornecido (response_format).\n"
    "Regras obrigatórias:\n"
    "- Retorne somente o JSON do schema, sem markdown, sem blocos de código e "
    "sem texto antes ou depois do objeto.\n"
    "- Escreva todos os campos narrativos em português brasileiro (pt-BR); não "
    "use palavras em inglês nos campos narrativos.\n"
    "- Proveniência obrigatória: todo dado clínico exige evidence_spans "
    "(field_path + trecho literal) citando o texto que o sustenta.\n"
    "- Nunca invente procedimento, exame, valor, medicamento, comorbidade ou "
    "achado; registre apenas o que está no texto.\n"
    "- Use o status tri-state: confirmado (explícito no texto), nao_informado "
    "(ausente) ou incerto (ambíguo) — nunca complete lacunas por inferência.\n"
    "- Pessoas e dados identificadores estão tokenizados no texto (ex.: "
    "<PESSOA_1>, <DATA_1>); cite os tokens, nunca valores reais.\n"
    "- Você apenas extrai evidência: não avalie critérios de aceite/recusa nem "
    "aplique limiares clínicos."
)

_LLM2_SYSTEM_PROMPT = (
    "Você é o assistente de apresentação clínica da Hemodinâmica (HMD) — "
    "Hospital Geral Roberto Santos. Você recebe os dados estruturados da "
    "extração (somente tokens), os resultados determinísticos da política "
    "pré-operatória por procedimento e os resumos de casos anteriores, e "
    "responde APENAS com o objeto JSON pedido no schema fornecido "
    "(response_format): o sumário do caso e uma sugestão por procedimento.\n"
    "Regras obrigatórias:\n"
    "- Retorne somente o JSON do schema, sem markdown, sem blocos de código e "
    "sem texto antes ou depois do objeto.\n"
    "- Escreva todos os campos narrativos em português brasileiro (pt-BR); não "
    "use palavras em inglês nos campos narrativos.\n"
    "- Produza exatamente um item por procedimento da lista fornecida: não "
    "omita, não duplique e não adicione procedimento.\n"
    "- A política fornecida é determinística e prevalece: onde ela recomenda "
    "recusar, a sugestão deve ser recusar com os motivos da política; não "
    "reavalie limiares nem suavize a recomendação.\n"
    "- Não invente razões, motivos, dados ou casos anteriores; use apenas o "
    "que foi fornecido ao final da mensagem.\n"
    "- Pessoas e dados identificadores aparecem como tokens (ex.: <PESSOA_1>); "
    "nunca introduza valores reais."
)

# Conteúdo por estágio (systems neutros).
_SYSTEM_PROMPT_CONTENTS: dict[str, str] = {
    "llm1": _LLM1_SYSTEM_PROMPT,
    "llm2": _LLM2_SYSTEM_PROMPT,
}

# ── Contexto clínico por tipo (seção do tipo em temp/parametrosHMD.md) ─────
#
# Frases curtas e fiéis à fonte — informativas para o LLM (desambiguação e
# direção de extração), nunca thresholds nem decisão de aceite. Flebografia não
# é descrita pelo parametrosHMD: o plano (§2) registra a decisão de usar os
# critérios da seção 1, mas não há conteúdo clínico próprio a citar — o bloco
# do tipo fica sem frase de contexto (sem decisão nova).
_TYPE_CONTEXT: dict[str, str] = {
    "art_perif": "Diagnóstico de doenças arteriais (membros).",
    "flebografia": "",
    "angio_art_perif": "Tratamento de estenoses ou oclusões arteriais (membros).",
    "angio_venosa_central": (
        "Tratamento de estenoses venosas em veias centrais "
        "(jugulares, subclávias, ilíacas ou cavas)."
    ),
    "angio_fav": (
        "Correção de estenoses em fístulas para hemodiálise; a condição do "
        "acesso vascular (fístula) é avaliada previamente."
    ),
    "permicath": (
        "Acesso vascular para terapia de hemodiálise; exige a avaliação de "
        "infecção sistêmica ativa e do acesso vascular por ultrassom."
    ),
    "filtro_cava": (
        "Prevenção de embolia pulmonar em pacientes com trombose venosa "
        "profunda e contraindicação ao uso de anticoagulantes."
    ),
    "angio_carotidas": "Tratamento de estenoses ou oclusões arteriais (carótidas).",
    "art_cerebral": "Diagnóstico de doenças arteriais (território cerebral).",
    "cat_cardiaco": "Diagnóstico de doenças arteriais.",
    "angio_coronariana": "Tratamento de estenoses ou oclusões arteriais (coronárias).",
    "dren_biliar": "Alívio de obstrução biliar extra-hepática.",
    "nefrostomia": "Alívio de obstrução ureteral.",
}

# ── Instruções por estágio (cauda de cada user por tipo) ────────────────────

_LLM1_USER_INSTRUCTIONS = (
    "Instruções para este procedimento:\n"
    "- Registre o item em pedido.procedimentos_solicitados SOMENTE se o texto "
    "da solicitação atual o sustentar, com evidence_spans citando o trecho; "
    "menção histórica, negação ou inferência nunca criam item.\n"
    "- Extraia com evidence_spans e status tri-state os dados objetivos do "
    "caso relacionados a este procedimento descritos no texto (contexto "
    "clínico, linha do tempo, exames, medicações, comorbidades, "
    "contraindicações), incluindo o bloco específico do tipo quando presente "
    "no schema.\n"
    "- Informação ausente no texto → status nao_informado; nunca invente, "
    "complete ou assuma dado não descrito.\n"
    "- Não avalie critérios de aceite/recusa: a avaliação é determinística e "
    "ocorre em etapa posterior."
)

_LLM2_USER_INSTRUCTIONS = (
    "Instruções para este procedimento:\n"
    "- No JSON de resposta, produza exatamente o item deste procedimento "
    "(sugestão aceitar/recusar, motivos e resumo do raciocínio), baseado "
    "APENAS nos dados fornecidos ao final desta mensagem (visão estruturada, "
    "política determinística por procedimento e casos anteriores).\n"
    "- A política fornecida prevalece: onde ela recomenda recusar, reproduza a "
    "recusa com os motivos da política; não suavize nem reavalie limiares.\n"
    "- Nunca invente motivos, exames, valores ou casos anteriores; informação "
    "não fornecida fica ausente ou nao_informado.\n"
    "- Pessoas e dados identificadores aparecem como tokens (ex.: <PESSOA_1>); "
    "não introduza valores reais."
)

_USER_INSTRUCTIONS_BY_STAGE: dict[str, str] = {
    "llm1": _LLM1_USER_INSTRUCTIONS,
    "llm2": _LLM2_USER_INSTRUCTIONS,
}

# ── Conteúdo seedado (ordem canônica: systems e users do catálogo) ─────────


def _build_user_prompt(procedure_type: str, stage: str) -> str:
    """Monta o user de um tipo: identificação + contexto clínico + instruções."""
    if stage not in _USER_INSTRUCTIONS_BY_STAGE:
        raise ValueError(f"estágio de prompt desconhecido: {stage!r}")
    profile = get_procedure_profile(procedure_type)
    heading = (
        f"Procedimento declarado no caso: {procedure_type} ({profile.label})."
        if stage == "llm1"
        else f"Procedimento a apresentar: {procedure_type} ({profile.label})."
    )
    lines = [heading]
    context = _TYPE_CONTEXT[procedure_type]
    if context:
        lines.append(f"Indicação clínica do tipo: {context}")
    lines.append("")
    lines.append(_USER_INSTRUCTIONS_BY_STAGE[stage])
    return "\n".join(lines)


# ── Verificação de anexos (change attachment-processing-ocr, D4/R3) ────────
#
# 29º conteúdo do seed, FORA do mecanismo por-perfil: a verificação de
# patient-match de um anexo clínico é UMA chamada com prompt próprio — o
# consumidor (apps/attachments/verification.py) monta a mensagem diretamente a
# partir deste conteúdo, substituindo os placeholders ``{{attachment_text}}``
# (texto ANONIMIZADO do anexo — só tokens) e ``{{patient_token}}`` (token do
# paciente do caso, ou indicação de ausência). Regras: classificar se o
# documento é exame/laudo/relatório clínico; comparar o paciente do documento
# com o token quando houver; nunca introduzir valores reais; saída JSON com
# patient_match (match|mismatch|unknown) + summary + evidence. Sem token do
# paciente → sem comparação possível → patient_match=unknown.
ATTACHMENT_VERIFICATION = "ATTACHMENT_VERIFICATION"

_ATTACHMENT_VERIFICATION_PROMPT = (
    "Você é o verificador de anexos clínicos da Hemodinâmica (HMD) — Hospital "
    "Geral Roberto Santos. Você recebe o texto ANONIMIZADO de um documento "
    "anexado ao processo (exame, laudo ou relatório) e, quando houver, o token "
    "do paciente do caso, e responde APENAS com o objeto JSON pedido no schema "
    "fornecido (response_format).\n"
    "Regras obrigatórias:\n"
    "- Retorne somente o JSON do schema, sem markdown, sem blocos de código e "
    "sem texto antes ou depois do objeto.\n"
    "- Escreva os campos narrativos em português brasileiro (pt-BR).\n"
    "- Avalie se o documento é exame, laudo ou relatório clínico e registre em "
    "summary/evidence o que ele é e o que sustenta a conclusão.\n"
    "- Quando o token do paciente do caso for informado, compare-o com o "
    "paciente identificado no documento: patient_match=match quando o paciente "
    "do documento corresponde ao token; patient_match=mismatch quando o "
    "documento identifica OUTRO paciente; patient_match=unknown quando o "
    "documento não identifica paciente ou a comparação é impossível.\n"
    "- Quando o token do paciente NÃO for informado (paciente do caso "
    "desconhecido), não há comparação possível: responda patient_match=unknown "
    "e registre em summary/evidence apenas a avaliação do documento.\n"
    "- Pessoas e dados identificadores estão tokenizados no texto (ex.: "
    "<PESSOA_1>, <CPF_1>); cite apenas tokens nos campos narrativos e NUNCA "
    "introduza valores reais.\n"
    "- Não invente: summary e evidence refletem apenas o texto fornecido; "
    "mismatch/unknown nunca descartam nada — são informação para o médico.\n"
    "\n"
    "DOCUMENTO ANEXADO (texto anonimizado):\n"
    "{{attachment_text}}\n"
    "\n"
    "PACIENTE DO CASO (token):\n"
    "{{patient_token}}"
)


# Registro canônico (nome → conteúdo) dos 29 prompts seedados.
PROMPT_SEED_CONTENTS: dict[str, str] = {}
for stage in STAGES:
    PROMPT_SEED_CONTENTS[system_prompt_name(stage)] = _SYSTEM_PROMPT_CONTENTS[stage]
    for profile in PROCEDURE_PROFILES:  # ordem canônica do catálogo
        name = user_prompt_name(profile.procedure_type, stage)
        PROMPT_SEED_CONTENTS[name] = _build_user_prompt(profile.procedure_type, stage)
PROMPT_SEED_CONTENTS[ATTACHMENT_VERIFICATION] = _ATTACHMENT_VERIFICATION_PROMPT
