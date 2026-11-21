"""Helper de janela do dia local (slice 005, R1/R2/D7).

Fonte ÚNICA compartilhada pelos dois apps (sem duplicação): limites
``[start, end)`` do dia corrente no timezone local configurado (nunca a data
UTC de ``timezone.now()`` — evita o bug de fronteira UTC/local). Espelha o
``local_day_bounds`` do ats-web (``apps/cases/services.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta

import pytest
from django.utils import timezone

from apps.cases.day_bounds import local_day_bounds


def test_bounds_contain_now_and_span_one_day() -> None:
    """A janela contém o agora local e mede exatamente 1 dia."""
    start, end = local_day_bounds()

    assert start.tzinfo is not None
    assert end.tzinfo is not None
    assert end - start == timedelta(days=1)
    now = timezone.now()
    assert start <= now < end


def test_bounds_are_local_midnight() -> None:
    """As bordas são meia-noite do fuso local (não UTC)."""
    start, end = local_day_bounds()

    local_start = timezone.localtime(start)
    assert (local_start.hour, local_start.minute, local_start.second) == (0, 0, 0)
    assert timezone.localdate(start) == timezone.localdate()
    assert timezone.localdate(end) == timezone.localdate() + timedelta(days=1)


def test_bounds_accept_explicit_day() -> None:
    """Dia explícito ancora a janela naquele dia local."""
    day: date = timezone.localdate() - timedelta(days=3)

    start, end = local_day_bounds(day)

    assert timezone.localdate(start) == day
    assert end - start == timedelta(days=1)
    assert timezone.localtime(start).hour == 0


def test_explicit_day_bypasses_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Com dia explícito, ``timezone.now`` não é consultado (pureza)."""
    reported: list[str] = []

    real_now: Callable[[], datetime] = timezone.now

    def _boom() -> datetime:
        reported.append("called")
        return real_now()

    day = timezone.localdate() - timedelta(days=3)
    monkeypatch.setattr(timezone, "now", _boom)

    local_day_bounds(day)

    assert reported == []
