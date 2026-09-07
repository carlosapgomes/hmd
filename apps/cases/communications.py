"""Comunicações operacionais por caso (change 03, slice 005, D8).

Espelha o ats-web ``create_system_communication_notice_for_event`` com as
divergências deliberadas do HMD: ``post_user_communication`` é serviço novo do
HMD com papel explícito (o ats-web só tem a projeção system e o post manual
cria notificações de menção — change 11, fora de escopo); o texto das mensagens
system é constante canônica por tipo de evento (sem templates por evento além
de constantes). A projeção roda pelo signal ``CaseEvent.post_save`` em
``apps/cases/signals.py`` e é idempotente por ``source_event`` O2O (R4): um
evento gera no máximo uma mensagem system, sem autor e sem notificação (R5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.cases.events import CaseEventType
from apps.cases.models import CaseCommunicationMessage, MessageType

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.cases.models import Case, CaseEvent

# Texto canônico das mensagens system por tipo canônico de evento (design D8):
# conjunto inicial de tipos projetados; changes de fluxo futuros estendem.
SYSTEM_COMMUNICATION_TEXTS: dict[str, str] = {
    CaseEventType.CASE_STATUS_AWAITING_DOCTOR.value: "Caso disponível para decisão médica.",
    CaseEventType.CASE_STATUS_DOCTOR_DENIED.value: "Decisão médica: procedimentos negados.",
    CaseEventType.CASE_STATUS_SCHEDULING_CONFIRMED.value: "Agendamento confirmado.",
    CaseEventType.CASE_STATUS_SCHEDULING_DENIED.value: "Agendamento negado.",
}


def post_user_communication(
    case: Case,
    *,
    user: User,
    role: str,
    body: str,
) -> CaseCommunicationMessage:
    """Posta uma mensagem ``user`` na thread do caso (R2, serviço novo do HMD).

    ``role`` é o papel ativo explícito: o caller (views dos changes 04+)
    extrai da sessão do usuário — o serviço não toca a sessão. A mensagem
    grava autor, papel e timestamp; mensagens manuais não têm
    ``source_event``/``system_event_type``.
    """
    return CaseCommunicationMessage.objects.create(
        case=case,
        message_type=MessageType.USER,
        author=user,
        author_role=role,
        body=body,
    )


def create_system_communication_notice_for_event(
    event: CaseEvent,
) -> CaseCommunicationMessage | None:
    """Projeta um ``CaseEvent`` suportado em mensagem ``system`` (R3/R4).

    - Tipo fora do conjunto inicial (``SYSTEM_COMMUNICATION_TEXTS``) → ``None``
      (nenhuma mensagem).
    - Evento já projetado (``source_event`` O2O) → devolve a mensagem existente
      (idempotente; signal disparado 2× não duplica).
    - Senão, cria a mensagem sem autor, com ``source_event`` e o texto canônico
      do tipo. Nada além da row (R5): nenhuma notificação/badge/estado de
      leitura.
    """
    body = SYSTEM_COMMUNICATION_TEXTS.get(event.event_type)
    if body is None:
        return None
    try:
        return event.communication_notice
    except CaseCommunicationMessage.DoesNotExist:
        pass
    return CaseCommunicationMessage.objects.create(
        case=event.case,
        message_type=MessageType.SYSTEM,
        author=None,
        author_role="",
        body=body,
        source_event=event,
        system_event_type=event.event_type,
    )
