"""Cards de texto extraído + PDF original no detalhe do painel (slice PVP-005, R2–R3).

Os DOIS cards entram APÓS "Procedimentos declarados" e ANTES da "Trilha de
eventos" (D8): ``Texto Extraído do PDF`` (collapse fechado, ``<pre>``;
OMITIDO quando vazio — padrão NIR/agendador) + ``Visualizar PDF Original``
(um sub-collapse por doc com ``data-pdf-doc-collapse``, embed lazy com
``data-src`` SEM ``src``, "Abrir em nova aba", link mobile
``d-md-none`` identificando cada documento ("PDF N — arquivo") com
``?next=``, bloco ``<noscript>`` fora dos collapses) + ``extra_js`` com
``pdf-lazy-embed.js``.

Comportamento preservado (R3): detalhe com textos/IDs existentes segue
renderizando identificação, procedimentos, trilha e encerramento intactos.
Molde: ``apps/scheduler/tests/test_detail_cards.py`` (PVP-004).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import unescape

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.cases.models import Case, CaseDocument, CaseStatus
from apps.dashboard.case_labels import CASE_NEXT_STEP_LABELS

MANAGER_ROLE = "manager"

# Storage de teste: arquivos apenas em memória (nada de disco) — mesmo
# padrão de ``apps/scheduler/tests/test_detail_cards.py``.
_TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture(autouse=True)
def _memory_storage() -> Iterator[None]:
    """Storage default em memória durante cada teste do arquivo."""
    with override_settings(STORAGES=_TEST_STORAGES):
        yield


def _make_user(username: str, role_name: str = MANAGER_ROLE) -> User:
    """Usuário com um papel (para o middleware resolver o papel ativo)."""
    role, _ = Role.objects.get_or_create(name=role_name)
    user = User.objects.create_user(username=username, password="senha-teste")
    user.roles.add(role)
    return user


def _login(client: Client, user: User, role: str = MANAGER_ROLE) -> None:
    """Autentica no client com o papel ativo ``role`` na sessão."""
    client.force_login(user)
    session = client.session
    session["active_role"] = role
    session.save()


def _make_case(creator: User, *, extracted_text: str = "") -> Case:
    """Caso do painel com texto extraído opcional (sem passar pela FSM)."""
    case = Case.objects.create(created_by=creator)
    if extracted_text:
        Case.objects.filter(pk=case.pk).update(extracted_text=extracted_text)
        case.refresh_from_db()
    return case


def _add_document(case: Case, uploaded_by: User, *, position: int) -> CaseDocument:
    """Anexa um CaseDocument fake ao caso na posição informada."""
    uploaded = SimpleUploadedFile(
        f"relatorio-{position}.pdf",
        f"%PDF-1.4 conteudo do pdf {position}".encode(),
        content_type="application/pdf",
    )
    return CaseDocument.objects.create(
        case=case,
        file=uploaded,
        position=position,
        original_filename=f"relatorio-painel-{position}.pdf",
        content_type="application/pdf",
        size_bytes=len(uploaded.read()),
        uploaded_by=uploaded_by,
    )


def _detail_url(case: Case) -> str:
    return reverse("dashboard:case_detail", args=[case.case_id])


def _detail_body(client: Client, case: Case) -> str:
    response = client.get(_detail_url(case))
    assert response.status_code == 200
    return response.content.decode()


# ── R2: posição D8 ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_detail_cards_position_after_procedures_before_trail(
    client: Client,
) -> None:
    """R2/D8: os dois cards ficam APÓS "Procedimentos declarados" e ANTES da trilha."""
    manager = _make_user("gestor-cards-posicao")
    case = _make_case(manager, extracted_text="TEXTO EXTRAIDO DO RELATORIO DO PAINEL")
    _add_document(case, manager, position=1)
    _login(client, manager)

    body = _detail_body(client, case)

    procedures_index = body.index("Procedimentos declarados")
    text_index = body.index("Texto Extraído do PDF")
    pdf_index = body.index("Visualizar PDF Original")
    trail_index = body.index("Trilha de eventos")
    assert procedures_index < text_index < pdf_index < trail_index, (
        "cards fora da ordem D8 (procedimentos → texto → pdf → trilha)"
    )


# ── R2: cards fechados/lazy/links/noscript/script/omissão ────────────────


@pytest.mark.django_db
def test_detail_shows_text_and_pdf_cards_closed_by_default(
    client: Client,
) -> None:
    """R2: manager vê os dois cards fechados (sem ``collapse show``)."""
    manager = _make_user("gestor-cards-painel")
    case = _make_case(manager, extracted_text="TEXTO EXTRAIDO DO RELATORIO DO PAINEL")
    document = _add_document(case, manager, position=1)
    serve_url = reverse("dashboard:case_pdf", args=[case.case_id, document.position])
    _login(client, manager)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" in body
    assert "Visualizar PDF Original" in body
    assert "TEXTO EXTRAIDO DO RELATORIO DO PAINEL" in body
    assert serve_url in unescape(body)
    assert "collapse show" not in body


@pytest.mark.django_db
def test_detail_embed_lazy_without_src(client: Client) -> None:
    """R2: embed lazy — data-src aponta o serve; SEM src; sub-collapse marcado."""
    manager = _make_user("gestor-lazy-painel")
    case = _make_case(manager, extracted_text="TEXTO EXTRAIDO DO RELATORIO DO PAINEL")
    document = _add_document(case, manager, position=1)
    serve_url = reverse("dashboard:case_pdf", args=[case.case_id, document.position])
    _login(client, manager)

    body = _detail_body(client, case)

    assert "data-pdf-doc-collapse" in body
    assert f'id="pdf-collapse-{document.position}"' in body
    assert f'data-src="{serve_url}"' in unescape(body)
    assert "pdf-lazy-embed" in body
    assert "<embed src=" not in body


@pytest.mark.django_db
def test_detail_mobile_links_new_tab_and_noscript(client: Client) -> None:
    """R2: link mobile (d-md-none) identificado com ?next=, nova aba e noscript."""
    manager = _make_user("gestor-links-painel")
    case = _make_case(manager, extracted_text="TEXTO EXTRAIDO DO RELATORIO DO PAINEL")
    document = _add_document(case, manager, position=1)
    detail_url = _detail_url(case)
    serve_url = reverse("dashboard:case_pdf", args=[case.case_id, document.position])
    viewer_url = reverse("dashboard:pdf_viewer", args=[case.case_id, document.position])
    _login(client, manager)

    body = _detail_body(client, case)
    plain = unescape(body)

    assert "d-md-none" in body
    assert viewer_url in plain
    assert detail_url in plain
    assert "Abrir em nova aba" in body
    assert 'target="_blank"' in body
    noscript_start = body.index("<noscript>")
    noscript_end = body.index("</noscript>")
    assert serve_url in unescape(body[noscript_start:noscript_end])
    assert (
        body.index("pdf-collapse-") > noscript_end or body.index("pdf-collapse-") < noscript_start
    )


@pytest.mark.django_db
def test_detail_loads_lazy_script(client: Client) -> None:
    """R2: pdf-lazy-embed.js carrega no detalhe do painel."""
    manager = _make_user("gestor-script-painel")
    case = _make_case(manager, extracted_text="TEXTO")
    _add_document(case, manager, position=1)
    _login(client, manager)

    body = _detail_body(client, case)

    assert "js/pdf-lazy-embed.js" in body


@pytest.mark.django_db
def test_detail_omits_text_card_without_extracted_text(client: Client) -> None:
    """R2 (padrão NIR/agendador): sem extracted_text o card de texto é OMITIDO."""
    manager = _make_user("gestor-omissao-painel")
    case = _make_case(manager)
    _add_document(case, manager, position=1)
    _login(client, manager)

    body = _detail_body(client, case)

    assert "Texto Extraído do PDF" not in body
    assert "Texto extraído indisponível" not in body
    assert "Visualizar PDF Original" in body


@pytest.mark.django_db
def test_mobile_links_identify_each_document(client: Client) -> None:
    """R2 (P1 do PVP-003): cada link mobile identifica o documento ("PDF N — arquivo")."""
    manager = _make_user("gestor-mobile-distintos-painel")
    case = _make_case(manager, extracted_text="TEXTO")
    _add_document(case, manager, position=1)
    _add_document(case, manager, position=2)
    _login(client, manager)

    body = _detail_body(client, case)

    anchors = re.findall(r"(<a[^>]*d-md-none[^>]*>.*?</a>)", body, re.DOTALL)
    viewer_anchors = [a for a in anchors if "/viewer/" in a.split(">", 1)[0]]
    assert len(viewer_anchors) == 2, (
        f"esperados 2 links mobile do viewer, obtidos {len(viewer_anchors)}"
    )
    texts = [
        re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", anchor)).strip() for anchor in viewer_anchors
    ]
    assert texts[0] != texts[1], f"links mobile idênticos: {texts!r}"
    assert "PDF 1" in texts[0] and "relatorio-painel-1.pdf" in texts[0]
    assert "PDF 2" in texts[1] and "relatorio-painel-2.pdf" in texts[1]


# ── R3: preservado ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_detail_preserves_identification_trail_and_close(client: Client) -> None:
    """R3: identificação, procedimentos, trilha e encerramento seguem intactos."""
    manager = _make_user("gestor-preservado-painel")
    case = _make_case(manager, extracted_text="TEXTO EXTRAIDO DO RELATORIO DO PAINEL")
    case.patient_name = "Maria da Silva"
    case.agency_record_number = "33345"
    case.save(update_fields=["patient_name", "agency_record_number"])
    _add_document(case, manager, position=1)
    _login(client, manager)

    body = _detail_body(client, case)

    assert "Maria da Silva" in body
    assert "33345" in body
    assert CASE_NEXT_STEP_LABELS[str(CaseStatus.NEW)] in body
    assert "Procedimentos declarados" in body
    assert "Trilha de eventos" in body
    assert reverse("dashboard:admin_close_confirm", args=[case.case_id]) in body
