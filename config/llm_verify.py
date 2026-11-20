"""Cliente LLM determinístico exclusivo de ``config.settings.verify`` (D9).

Prova de navegador da confirmação NIR sem rede: respostas LLM1/LLM2 válidas
para o schema solicitado, determinadas por **estágio/perfil da chamada** —
o singleton declarado é lido dos blocos de prompt embutidos na mensagem
(conteúdo semeado pelo ``seed_prompts``, sem contador global, sem sequência
em memória, sem ORM e sem rede). Novas requisições/workers podem criar novo
cliente sem mudar o resultado.

Cenários sintéticos explícitos (chave = singleton declarado da chamada):

- LLM1 ``art_perif``/``flebografia``/``dren_biliar`` → divergência repetida
  contra ``cat_cardiaco`` (inclusive pós-confirmação: testa a autoridade
  real sem recriar retenção);
- LLM1 ``angio_art_perif``/``art_cerebral`` → concordância exata
  (``pedido`` igual ao declarado);
- LLM1 ``nefrostomia`` → detecção vazia (``pedido`` vazio);
- LLM2 ``art_cerebral`` → falha tipada programada (fixture 990104);
- LLM2 demais reconhecidos → sumarização válida para exatamente o declarado;
- qualquer outro declarado, estágio ou schema → ``LlmError`` com kind
  existente e mensagem genérica (nunca exceção genérica nem sucesso
  fabricado), sem PHI.

Fail-closed como ``apps.accounts.backends_verify``: o módulo recusa importar
fora da identidade positiva de ``config.settings.verify`` (settings
programáticas ou módulo vazio recusam mesmo com banco ``hmd_verify_*``) e
cada chamada revalida a identidade antes de responder.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured

_VERIFY_DB_NAME_RE = re.compile(r"hmd_verify_[a-z0-9_]+")


def _assert_verify_settings() -> None:
    """Recusa operar fora de ``config.settings.verify`` (D9, fail-closed).

    Identidade positiva: settings configuradas exigem
    ``SETTINGS_MODULE == "config.settings.verify"`` e banco efetivo
    ``hmd_verify_*`` sem ``prod``; settings programáticas, módulo vazio ou
    qualquer outro módulo recusam.
    """
    from django.conf import settings

    if not settings.configured:
        module = os.environ.get("DJANGO_SETTINGS_MODULE", "")
        if module != "config.settings.verify":
            raise ImproperlyConfigured(
                "llm_verify só opera sob config.settings.verify "
                f"(DJANGO_SETTINGS_MODULE={module!r}, settings não configuradas)."
            )
        return
    settings_module: str | None = settings.SETTINGS_MODULE
    if settings_module != "config.settings.verify":
        raise ImproperlyConfigured(
            f"llm_verify só opera sob config.settings.verify (settings ativas={settings_module!r})."
        )
    name = str(settings.DATABASES.get("default", {}).get("NAME", ""))
    if not _VERIFY_DB_NAME_RE.fullmatch(name) or "prod" in name:
        raise ImproperlyConfigured(
            f"llm_verify só opera com banco hmd_verify_* (banco configurado={name!r})."
        )


# Guard ANTES de qualquer import do pipeline: fora de verify, o módulo falha
# sem carregar o cliente real nem o catálogo.
_assert_verify_settings()

from apps.pipeline.llm import LlmClient, LlmError  # noqa: E402

_LLM1_SCHEMA_NAME = "Llm1CaseArtifact"
_LLM2_SCHEMA_NAME = "Llm2Response"

# LLM1: singleton declarado → detecção sintética da nova passada (D9).
_LLM1_DETECTED_BY_DECLARED: dict[str, list[str]] = {
    "art_perif": ["cat_cardiaco"],
    "flebografia": ["cat_cardiaco"],
    "angio_art_perif": ["angio_art_perif"],
    "nefrostomia": [],
    "dren_biliar": ["cat_cardiaco"],
    "art_cerebral": ["art_cerebral"],
}

# LLM2: declarado com falha tipada programada (fixture 990104, D9).
_LLM2_FAILURE_TYPES = frozenset({"art_cerebral"})


def _stage_of(json_schema: dict[str, Any] | None) -> str:
    """Estágio da chamada pelo nome do schema solicitado (D9)."""
    name = json_schema.get("name", "") if isinstance(json_schema, dict) else ""
    if name == _LLM1_SCHEMA_NAME:
        return "llm1"
    if name == _LLM2_SCHEMA_NAME:
        return "llm2"
    raise LlmError(
        "invalid_response",
        "llm_verify: schema solicitado não reconhecido — cenário sintético ausente.",
    )


def _detect_declared(messages: Sequence[dict[str, str]], stage: str) -> tuple[str, ...]:
    """Singleton declarado da chamada pelos blocos de prompt embutidos (D9).

    Cada tipo declarado contribui com seu bloco ``proc.<tipo>.<stage>.user``
    (conteúdo do ``seed_prompts``) concatenado na mensagem user; o declarado
    é o conjunto de blocos presentes no texto — sem contador, sem ORM e sem
    inferir pelo conteúdo clínico.
    """
    from apps.cases.procedure_catalog import PROCEDURE_PROFILES
    from apps.llm.prompts_seed import PROMPT_SEED_CONTENTS, user_prompt_name

    text = "\n".join(message.get("content", "") for message in messages)
    matched: list[str] = []
    for profile in PROCEDURE_PROFILES:
        try:
            block = PROMPT_SEED_CONTENTS[user_prompt_name(profile.procedure_type, stage)]
        except KeyError:
            continue
        if block and block in text:
            matched.append(profile.procedure_type)
    return tuple(matched)


def _llm1_artifact(detected: list[str]) -> str:
    """Artefato LLM1 sintético válido (só tokens, pt-BR, sem invenção)."""
    return json.dumps(
        {
            "pedido": {
                "procedimentos_solicitados": detected,
                "evidence_spans": [
                    {
                        "field_path": "pedido.procedimentos_solicitados",
                        "excerpt": "O relatorio sintetico de verificacao lista os procedimentos acima.",
                    }
                ],
            },
            "contexto_clinico": (
                "Paciente <PESSOA_1> com quadro clinico sintetico de verificacao, "
                "sem dados alem dos tokens do caso."
            ),
            "linha_do_tempo": [],
            "exames": None,
            "medicacoes": [],
            "comorbidades": [],
            "contraindicacoes": [],
            "trechos_nao_classificados": [],
        },
        ensure_ascii=False,
    )


def _llm2_response(declared: tuple[str, ...]) -> str:
    """Resposta LLM2 sintética válida para exatamente o conjunto declarado."""
    return json.dumps(
        {
            "summary_text": (
                "Sumario clinico sintetico de verificacao para o procedimento "
                "confirmado, com analise dos dados anonimizados disponiveis e "
                "ressalva das informacoes ausentes no relatorio."
            ),
            "procedures": [
                {
                    "procedure_type": procedure_type,
                    "suggestion": "aceitar",
                    "motivos": ["sem contraindicoes identificadas no relatorio sintetico"],
                    "evidence_spans": [],
                }
                for procedure_type in declared
            ],
        },
        ensure_ascii=False,
    )


class VerifyLlmClient:
    """Transporte sintético: só responde cenários reconhecidos (D9).

    Não grava eventos, não confirma caso, não muda FSM, não executa policy
    nem pré-calcula ``APPLIED`` — apenas devolve o texto da resposta
    programada para o schema/estágio/perfil solicitados.
    """

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        _assert_verify_settings()
        stage = _stage_of(json_schema)
        declared = _detect_declared(messages, stage)
        if len(declared) != 1:
            raise LlmError(
                "invalid_response",
                "llm_verify: declarado da chamada não reconhecido — cenário sintético ausente.",
            )
        if stage == "llm1":
            detected = _LLM1_DETECTED_BY_DECLARED.get(declared[0])
            if detected is None:
                raise LlmError(
                    "invalid_response",
                    "llm_verify: declarado sem cenário sintético — entrada não reconhecida.",
                )
            return _llm1_artifact(detected)
        if declared[0] in _LLM2_FAILURE_TYPES:
            raise LlmError(
                "other",
                "llm_verify: falha sintética programada do estágio llm2 para o cenário.",
            )
        return _llm2_response(declared)


def create_verify_llm_client() -> LlmClient:
    """Factory do adaptador, referenciada só por ``config.settings.verify``."""
    _assert_verify_settings()
    return VerifyLlmClient()
