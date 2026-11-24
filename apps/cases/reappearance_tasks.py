"""Worker de recuperação da substituição médica (slice 002, D6).

Entry point do Schedule ONCE no cluster ``llm``: a lógica vive em
``apps.cases.reappearance.handle_reappearance_retry`` (testável sem broker);
este módulo é só o alvo serializável do scheduler. Nunca nova análise LLM —
somente o efeito pendente, com orçamento limitado.
"""

from __future__ import annotations

import uuid


def retry_reappearance_application(
    reappearance_id: uuid.UUID | str,
    revision_digest: str,
    attempt: int | str,
) -> None:
    """Executa uma tentativa de recuperação do vínculo pendente (D6).

    Guards idempotentes antes de qualquer efeito: vínculo ausente, aplicado,
    flag desligada, revisão/tentativa divergentes ou ``next_attempt_at``
    futuro são no-op sem consumir orçamento.
    """
    from apps.cases.reappearance import handle_reappearance_retry

    handle_reappearance_retry(reappearance_id, revision_digest, attempt)
