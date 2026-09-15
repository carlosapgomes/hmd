"""Testes de "Meus casos e detalhe do NIR" (slice 004 do intake NIR).

Cobre R1 (lista escopada ao criador, ordenada, com badges de retenção/FAILED),
R2 (detalhe do próprio caso com documentos/trilha/comunicações; caso alheio →
404), R3 (serve_document seguro: 200 com content-type pdf e filename original
no próprio caso; 404 para caso alheio ou documento de outro caso), R4 (links
de navegação na navbar quando papel ativo é ``nir``), R5 (redirect do POST de
criação aponta para o detalhe) e R6 (não-nir → 403 em todas as rotas) — os 3
cenários da spec "Meus casos e detalhe do NIR".

Referência de padrão (somente-leitura): ats-web ``apps/intake/views.py``
``my_cases``/``case_detail``/``serve_pdf``. Divergência deliberada do HMD
(D6/D9): o acesso é restrito ao NIR **criador** — caso alheio → 404, sem
vazamento.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.cases.closure import ADMINISTRATIVE_CLOSURE_REASONS, administratively_close_case
from apps.cases.communications import post_user_communication
from apps.cases.models import Case, CaseDocument, CaseStatus
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
# Motivo canônico de retenção (mesmo shape do gate, D4) usado nos setups.
RETENTION_REASON = "documento fora do padrão SESAB do relatório (motivo: missing_header)"


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação sem processamento (slice 004): estados controlados pelo teste.

    Os PDFs fake destes testes não têm camada de texto — a suíte roda o
    processamento inline por default e degradaria/FAILED os casos criados.
    Os testes avançam os estados que precisam via operações da FSM.
    """

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _create_case(
    user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    *names: str,
) -> Case:
    """Cria um caso NEW com 1 PDF (o primeiro nome, se informado) e 1 tipo."""
    name = names[0] if names else ""
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        file=pdf_factory(name=name) if name else pdf_factory(),
        procedure_type="art_perif",
    )


def _retain_for_review(case: Case) -> Case:
    """Simula o resultado do gate (slice 003): retido em PDF_EXTRACTING com flag."""
    case.start_pdf_extraction(user=None, role="system")
    case.manual_review_required = True
    case.manual_review_reason = RETENTION_REASON
    case.save(update_fields=["manual_review_required", "manual_review_reason"])
    return case


def _fail_case(case: Case) -> Case:
    """Simula falha de extração (slice 003): NEW → PDF_EXTRACTING → FAILED."""
    case.start_pdf_extraction(user=None, role="system")
    case.fail_processing(reason="falha na extração do PDF", user=None, role="system")
    return case


@pytest.mark.django_db
def test_list_scoped_to_creator(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/cenário spec: a lista mostra apenas os casos criados pelo NIR logado."""
    own_case_1 = _create_case(nir_user, pdf_factory)
    own_case_2 = _create_case(nir_user, pdf_factory)
    other_nir = user_factory("nir-alheio", NIR_ROLE)
    foreign_case = _create_case(other_nir, pdf_factory)

    client.force_login(nir_user)
    response = client.get(reverse("intake:my_cases"))

    assert response.status_code == 200
    body = response.content.decode()
    assert str(own_case_1.case_id) in body
    assert str(own_case_2.case_id) in body
    # Caso alheio não aparece (sem vazamento).
    assert str(foreign_case.case_id) not in body
    # Cada caso da lista leva ao detalhe.
    assert reverse("intake:case_detail", args=[own_case_1.case_id]) in body


@pytest.mark.django_db
def test_retention_and_failed_badges(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: badges de retenção (manual_review_required) e FAILED visíveis na lista."""
    retained = _retain_for_review(_create_case(nir_user, pdf_factory))
    failed = _fail_case(_create_case(nir_user, pdf_factory))
    assert retained.status == CaseStatus.PDF_EXTRACTING
    assert failed.status == CaseStatus.FAILED

    client.force_login(nir_user)
    response = client.get(reverse("intake:my_cases"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Retido para revisão" in body
    assert "Falha de processamento" in body
    assert str(retained.case_id) in body
    assert str(failed.case_id) in body


@pytest.mark.django_db
def test_detail_shows_docs_and_communications_without_trail(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/cenário spec: detalhe do próprio caso mostra docs e comunicações —
    SEM a trilha de eventos (slice 003 do painel-ats-parity: a trilha vive no
    painel)."""
    case = _retain_for_review(_create_case(nir_user, pdf_factory, "relatorio-sesab.pdf"))
    documents = list(case.documents.all())
    assert len(documents) == 1
    post_user_communication(
        case, user=nir_user, role=NIR_ROLE, body="Paciente aguardando complemento do relatório."
    )

    client.force_login(nir_user)
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    # Status legível e flag de retenção com motivo.
    assert "Extraindo PDF" in body
    assert "Retido para revisão" in body
    assert RETENTION_REASON in body
    # Documentos com nome original e botão de abrir (serve_document seguro).
    for document in documents:
        assert document.original_filename in body
        assert reverse("intake:serve_document", args=[case.case_id, document.pk]) in body
    # Sem trilha de eventos no detalhe do NIR (vive no painel).
    assert "Trilha de eventos" not in body
    assert "Procedimentos declarados pelo NIR" not in body
    assert "CASE_STATUS_PDF_EXTRACTING" not in body
    # Thread de comunicações (change 03).
    assert "Comunicações" in body
    assert "Paciente aguardando complemento do relatório." in body


@pytest.mark.django_db
def test_detail_foreign_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/cenário spec: detalhe de caso alheio é negado com 404 (sem vazamento)."""
    other_nir = user_factory("nir-alheio", NIR_ROLE)
    foreign_case = _create_case(other_nir, pdf_factory)

    client.force_login(nir_user)
    response = client.get(reverse("intake:case_detail", args=[foreign_case.case_id]))

    assert response.status_code == 404
    assert str(foreign_case.case_id) not in response.content.decode()


@pytest.mark.django_db
def test_serve_document_ok(
    client: Client,
    nir_user: User,
) -> None:
    """R3: serve_document do próprio caso responde o PDF com content-type e filename."""
    uploaded = SimpleUploadedFile(
        "relatorio-sesab.pdf",
        b"%PDF-1.4 conteudo original do relatorio",
        content_type="application/pdf",
    )
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=uploaded,
        procedure_type="art_perif",
    )
    document = list(case.documents.all())[0]

    client.force_login(nir_user)
    response = client.get(reverse("intake:serve_document", args=[case.case_id, document.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'inline; filename="{document.original_filename}"'
    uploaded.seek(0)
    expected_bytes = uploaded.read()
    # FileResponse é streaming: o test client do Django não materializa em
    # ``content``; ``streaming_content`` existe em runtime (os stubs tipam a
    # resposta do client como não-streaming).
    served_bytes = b"".join(response.streaming_content)  # type: ignore[attr-defined]
    assert served_bytes == expected_bytes


@pytest.mark.django_db
def test_serve_document_foreign_case_404(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: documento de caso alheio → 404 (sem vazamento)."""
    other_nir = user_factory("nir-alheio", NIR_ROLE)
    foreign_case = _create_case(other_nir, pdf_factory)
    document = list(foreign_case.documents.all())[0]

    client.force_login(nir_user)
    response = client.get(
        reverse("intake:serve_document", args=[foreign_case.case_id, document.pk])
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_serve_document_wrong_case_404(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: documento pertencente a outro caso do mesmo NIR → 404 sob este caso."""
    case_one = _create_case(nir_user, pdf_factory, "caso-um.pdf")
    case_two = _create_case(nir_user, pdf_factory, "caso-dois.pdf")
    document_of_other_case = list(case_two.documents.all())[0]

    client.force_login(nir_user)
    response = client.get(
        reverse("intake:serve_document", args=[case_one.case_id, document_of_other_case.pk])
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_navbar_links_for_nir(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
) -> None:
    """R4: navbar mostra links do intake quando o papel ativo é nir (e não para outro)."""
    # follow=True: a home despacha o papel ativo à sua fila (change
    # painel-gerencial-e-home, slice 002); a navbar da página final segue
    # decidindo os links pelo papel ativo.
    client.force_login(nir_user)
    response = client.get(reverse("home"), follow=True)
    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("intake:my_cases") in body
    assert f'href="{reverse("intake:home")}">Enviar relatório</a>' in body

    doctor = user_factory("doctor-fora", DOCTOR_ROLE)
    client.force_login(doctor)
    response = client.get(reverse("home"), follow=True)
    assert response.status_code == 200
    assert reverse("intake:my_cases") not in response.content.decode()
    assert reverse("intake:home") not in response.content.decode()


@pytest.mark.django_db
def test_create_redirects_to_detail(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R5: o POST de criação redireciona para o detalhe do caso criado."""
    client.force_login(nir_user)
    response = client.post(
        reverse("intake:home"),
        {
            "documents": [pdf_factory(name="relatorio-sesab.pdf")],
            "procedure_type": "art_perif",
        },
    )

    assert response.status_code == 302
    case = Case.objects.get()
    assert response["Location"] == reverse("intake:case_detail", args=[case.case_id])

    followed = client.get(response["Location"])
    assert followed.status_code == 200
    assert "criado com sucesso" in followed.content.decode()


@pytest.mark.django_db
def test_non_nir_forbidden_on_all_routes(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R6: papel ativo fora de nir → 403 em lista, detalhe e serve_document."""
    case = _create_case(nir_user, pdf_factory)
    document = list(case.documents.all())[0]
    doctor = user_factory("doctor-fora", DOCTOR_ROLE)
    client.force_login(doctor)

    assert client.get(reverse("intake:my_cases")).status_code == 403
    assert client.get(reverse("intake:case_detail", args=[case.case_id])).status_code == 403
    assert (
        client.get(reverse("intake:serve_document", args=[case.case_id, document.pk])).status_code
        == 403
    )
    assert CaseDocument.objects.count() == 1


# ── R3: resultado do encerramento administrativo ao criador (slice 003) ───

# Motivo do catálogo do encerramento administrativo usado nas fixtures.
ADMIN_CLOSURE_REASON_CODE = "stuck_lock"
ADMIN_CLOSURE_REASON_TEXT = "lock do worker nao liberado apos 24h"
MANAGER_ROLE = "manager"


def _close_administratively(case: Case, manager: User) -> Case:
    """Encerra o caso pelo serviço real do núcleo (papel ativo do supervisor)."""
    administratively_close_case(
        case=case,
        user=manager,
        active_role=MANAGER_ROLE,
        reason_code=ADMIN_CLOSURE_REASON_CODE,
        reason_text=ADMIN_CLOSURE_REASON_TEXT,
    )
    assert case.status == CaseStatus.CLEANED
    return case


@pytest.mark.django_db
def test_closed_tab_shows_administrative_closure_result(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/D4: na aba de encerrados, o CRIADOR vê o resultado "Encerrado
    administrativamente" com o rótulo do motivo e o texto registrado."""
    case = _create_case(nir_user, pdf_factory)
    manager = user_factory("gestor-mycases", MANAGER_ROLE)
    _close_administratively(case, manager)

    client.force_login(nir_user)
    response = client.get(reverse("intake:my_cases"), {"tab": "closed"})
    body = response.content.decode()

    assert response.status_code == 200
    assert str(case.case_id) in body
    assert "Encerrado administrativamente" in body
    assert ADMINISTRATIVE_CLOSURE_REASONS[ADMIN_CLOSURE_REASON_CODE] in body
    assert ADMIN_CLOSURE_REASON_TEXT in body


@pytest.mark.django_db
def test_administrative_closure_result_is_scoped_to_creator(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/D4: o resultado administrativo nunca vaza para outro NIR e o caso
    encerrado sai da aba de ativos do próprio criador."""
    case = _create_case(nir_user, pdf_factory)
    manager = user_factory("gestor-mycases-alheio", MANAGER_ROLE)
    _close_administratively(case, manager)
    other_nir = user_factory("nir-alheio-adm", NIR_ROLE)

    client.force_login(other_nir)
    foreign_body = client.get(reverse("intake:my_cases"), {"tab": "closed"}).content.decode()

    assert str(case.case_id) not in foreign_body
    assert ADMIN_CLOSURE_REASON_TEXT not in foreign_body

    client.force_login(nir_user)
    active_body = client.get(reverse("intake:my_cases")).content.decode()

    assert str(case.case_id) not in active_body
    assert ADMIN_CLOSURE_REASON_TEXT not in active_body


# ── R1/R2: identificação do paciente nos cards (queue-cards-wait-time) ────

AGE_SUFFIX_IN_CARD = re.compile(r"\d+ a</div>")


def _card_slice(body: str, case_id: object) -> str:
    """Fatia o HTML do card do caso (do href até o ``</a>`` que fecha o link).

    O escopo por card evita asserts por contagem global (ex.: ``—`` de outros
    cards ou do próprio «Nº de ocorrência: —»). O card é um único ``<a>`` sem
    âncoras aninhadas, então o primeiro ``</a>`` após o ``case_id`` o fecha.
    """
    start = body.index(str(case_id))
    end = body.index("</a>", start)
    return body[start:end]


def _card_visible_text(card: str) -> str:
    """Corpo visível do card (após a tag de abertura), sem atributos de rota."""
    return re.sub(r'href="[^"]*"', "", card[card.index(">") + 1 :])


def _identification_line(card: str) -> str:
    """Conteúdo da linha ``fw-semibold`` do card (nome + idade)."""
    match = re.search(r'<div class="fw-semibold">(.*?)</div>', card, re.S)
    assert match is not None, "linha de identificação (fw-semibold) ausente no card"
    return match.group(1).strip()


@pytest.mark.django_db
def test_card_shows_patient_name_and_age(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R2: card identifica o paciente com nome e idade extraídos do cabeçalho."""
    case = _create_case(nir_user, pdf_factory)
    case.patient_name = "Paciente Identificado"
    case.patient_age = 84
    case.save(update_fields=["patient_name", "patient_age"])

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert _identification_line(card) == "Paciente Identificado · 84 a"


@pytest.mark.django_db
def test_card_without_identification_shows_dash_and_omits_age(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R2: caso sem identificação → ``—`` no nome e SEM sufixo de idade.

    Asserts escopados ao card (não contagem global de ``—``, que existe no
    «Nº de ocorrência: —» do próprio card).
    """
    case = _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert _identification_line(card) == "—"
    assert AGE_SUFFIX_IN_CARD.search(card) is None


@pytest.mark.django_db
def test_card_shows_zero_age(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: idade ``0`` é válida e EXIBE ``0 a`` (``is not None``, não truthiness)."""
    case = _create_case(nir_user, pdf_factory)
    case.patient_name = "Paciente Zero"
    case.patient_age = 0
    case.save(update_fields=["patient_name", "patient_age"])

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert _identification_line(card) == "Paciente Zero · 0 a"


@pytest.mark.django_db
def test_list_ordering_created_at_desc_preserved(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2 (pin): a ordem histórica ``-created_at`` segue valendo (mais novo primeiro)."""
    older = _create_case(nir_user, pdf_factory)
    newer = _create_case(nir_user, pdf_factory)
    now = timezone.now()
    Case.objects.filter(pk=older.pk).update(created_at=now - timedelta(days=2))
    Case.objects.filter(pk=newer.pk).update(created_at=now)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    assert body.index(str(newer.case_id)) < body.index(str(older.case_id))


@pytest.mark.django_db
def test_foreign_case_identification_not_leaked(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2 (pin reforçado): identificação de caso alheio não vaza na lista."""
    own_case = _create_case(nir_user, pdf_factory)
    own_case.patient_name = "Paciente Do Criador"
    own_case.save(update_fields=["patient_name"])
    other_nir = user_factory("nir-alheio-ident", NIR_ROLE)
    foreign_case = _create_case(other_nir, pdf_factory)
    foreign_case.patient_name = "Paciente Alheio"
    foreign_case.save(update_fields=["patient_name"])

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    assert str(own_case.case_id) in body
    assert "Paciente Do Criador" in body
    assert str(foreign_case.case_id) not in body
    assert "Paciente Alheio" not in body


@pytest.mark.django_db
def test_detail_failed_case_shows_error_badge_without_trail(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/D4: caso ``FAILED`` exibe badge de erro e nenhuma trilha/motivo técnico."""
    case = _fail_case(_create_case(nir_user, pdf_factory, "relatorio-sesab.pdf"))

    client.force_login(nir_user)
    response = client.get(reverse("intake:case_detail", args=[case.case_id]))
    body = response.content.decode()

    assert response.status_code == 200
    assert case.status == CaseStatus.FAILED
    assert "Trilha de eventos" not in body
    assert re.search(
        r'<span class="badge rounded-pill text-bg-danger[^"]*">Falha no processamento</span>',
        body,
    )
    # O motivo técnico não aparece no detalhe do NIR (vive na trilha do painel).
    assert "falha na extração do PDF" not in body


# ── R2: cards sem o uid do caso (queues-remove-uid) ───────────────────────


@pytest.mark.django_db
def test_card_does_not_show_case_uid(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2: o CORPO do card não exibe o uid — nº de ocorrência e paciente bastam.

    O uid permanece apenas no ``href`` da rota (chave técnica): o assert é
    escopado ao card e o atributo de rota é removido do texto antes da
    checagem, de modo que a reintrodução de «Caso <uid>» no corpo falha.
    """
    case = _create_case(nir_user, pdf_factory)
    case.patient_name = "Paciente Sem Uid"
    case.agency_record_number = "5040778"
    case.save(update_fields=["patient_name", "agency_record_number"])

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert f"Caso {case.case_id}" not in card
    assert str(case.case_id) not in _card_visible_text(card)
    assert "Paciente Sem Uid" in card
    assert "Nº de ocorrência: <strong>5040778</strong>" in card
    # A rota do card segue levando ao detalhe (uid como chave técnica).
    assert str(case.case_id) in body


@pytest.mark.django_db
def test_card_without_occurrence_shows_dash_without_uid(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/D1 (opção a): sem nº de ocorrência → «—» + paciente, sem fallback uid."""
    case = _create_case(nir_user, pdf_factory)
    case.patient_name = "Paciente Sem Ocorrencia"
    case.agency_record_number = ""
    case.save(update_fields=["patient_name", "agency_record_number"])

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    card = _card_slice(body, case.case_id)
    assert "Nº de ocorrência: <strong>—</strong>" in card
    assert "Paciente Sem Ocorrencia" in card
    assert f"Caso {case.case_id}" not in card
    assert str(case.case_id) not in _card_visible_text(card)
