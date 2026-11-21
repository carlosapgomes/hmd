"""Janela do dia local (slice 005 da change paridade-ats, D7).

Fonte ÚNICA compartilhada pelos dois apps (médico e agendador) — sem
duplicação: limites ``[start, end)`` do dia corrente no timezone local
configurado para filtros ORM por decisor+data. Espelha o
``local_day_bounds`` do ats-web (``apps/cases/services.py``).

Usa ``timezone.localdate()``, nunca a data UTC de ``timezone.now()`` —
evita o bug de fronteira UTC/local onde ``now()`` já está no dia seguinte
UTC enquanto o fuso local ainda está no dia anterior.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def local_day_bounds(day: date | None = None) -> tuple[datetime, datetime]:
    """Início e fim do dia local (timezone-aware) para filtros ORM.

    ``day=None`` ancora no dia corrente local; dia explícito ancora naquele
    dia (puro — sem consultar ``timezone.now()``). A janela mede exatamente
    1 dia: ``[meia-noite local, meia-noite seguinte)``.
    """
    from django.utils import timezone

    local_day = day or timezone.localdate()
    current_tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(local_day, time.min), current_tz)
    end = start + timedelta(days=1)
    return start, end
