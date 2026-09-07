"""Parser tolerante de JSON de respostas LLM (slice 004, R2, herança ats-web).

Estratégias em ordem (espelho do ``apps/pipeline/json_parser.py`` do ats-web):
1. ``json.loads`` direto; 2. remoção de fences de markdown (````` ```json ```
`````); 3. remoção de trailing commas; 4. varredura do primeiro objeto JSON
embutido via ``raw_decode``. Falha total → ``LlmJsonParseError`` (o serviço
trata como falha de schema e aciona o retry corretivo tipado).
"""

from __future__ import annotations

import json
import re
from typing import Any


class LlmJsonParseError(ValueError):
    """Resposta do LLM não pôde ser interpretada como objeto JSON."""


def decode_llm_json_object(raw_response: str) -> dict[str, object]:
    """Extrai o primeiro objeto JSON de ``raw_response`` (tolerante a ruído).

    Aceita JSON puro, blocos de código markdown, trailing commas e objeto
    embutido em texto arbitrário. Falha → ``LlmJsonParseError``.
    """
    # Estratégia 1: JSON direto.
    stripped = raw_response.strip()
    try:
        decoded = json.loads(stripped)
        if isinstance(decoded, dict):
            return decoded
    except json.JSONDecodeError:
        pass

    # Estratégia 2: remove fences de blocos de código markdown.
    text = stripped
    if text.startswith("```"):
        lines = [line for line in text.split("\n") if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
        try:
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            pass

    # Estratégia 3: remove trailing commas e tenta de novo.
    clean = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        decoded = json.loads(clean)
        if isinstance(decoded, dict):
            return decoded
    except json.JSONDecodeError:
        pass

    # Estratégia 4: varredura do primeiro objeto JSON embutido.
    return _extract_first_embedded_json_object(clean)


def _extract_first_embedded_json_object(text: str) -> dict[str, object]:
    """Localiza o primeiro objeto JSON (``{...}``) completo dentro do texto."""
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        if text[index] == "{":
            try:
                obj, _end = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(obj, dict):
                    return _as_object_dict(obj)
        index += 1
    raise LlmJsonParseError("resposta do LLM sem objeto JSON válido")


def _as_object_dict(obj: Any) -> dict[str, object]:
    """Recupera o dict tipado do objeto decodificado (json devolve Any)."""
    return {key: value for key, value in obj.items()}
