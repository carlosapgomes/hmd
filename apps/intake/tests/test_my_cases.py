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
def _minimization_mode() -> Iterator[None]:
    """Pino explícito do modo de minimização (ADR-0010/D7) nos fluxos de
    encerramento existentes — o teste de retenção sobrescreve com False."""
    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True):
        yield


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
    assert f'href="{reverse("intake:home")}">Novo Encaminhamento</a>' in body

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


# ── R1/R2: identificação do paciente nos cards (OLI-004 R4/D3) ────
# O card compacto usa ``.patient-name`` só para o nome; idade, sexo e
# unidade de origem vivem nos metadados (``.case-meta``), junto do nº de
# ocorrência e do sufixo de desambiguação.

AGE_IN_CARD = re.compile(r"\d+ a")


def _card_slice(body: str, case_id: object) -> str:
    """Fatia o HTML do card do caso pelo marcador ``data-my-cases-card``.

    O escopo por card evita asserts por contagem global (ex.: ``—`` de outros
    cards ou do próprio «Nº de ocorrência: —»). No padrão OLI-004 o href do
    detalhe é o CTA final (``stretched-link``), não um ``<a>`` que envolve o
    card — o marcador do container é a fronteira estável do card.
    """
    chunks = body.split("data-my-cases-card")
    for chunk in chunks:
        if str(case_id) in chunk:
            return chunk
    raise AssertionError(f"card do caso {case_id} ausente no corpo")


def _card_visible_text(card: str) -> str:
    """Corpo visível do card, sem atributos de rota."""
    return re.sub(r'href="[^"]*"', "", card)


def _patient_name_line(card: str) -> str:
    """Conteúdo de ``.patient-name`` do card (só o nome)."""
    match = re.search(r'<div class="patient-name">(.*?)</div>', card, re.S)
    assert match is not None, "linha .patient-name ausente no card"
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _card_meta_line(card: str) -> str:
    """Conteúdo dos metadados ``.case-meta`` do card (nº, idade, sexos, origem)."""
    match = re.search(r'<div class="[^"]*case-meta[^"]*">(.*?)</div>', card, re.S)
    assert match is not None, "metadados .case-meta ausentes no card"
    return match.group(1).strip()


def _nav_href(body: str, label: str) -> str:
    """Href do link do header com o rótulo ``label`` (badge pode seguir)."""
    match = re.search(rf'href="(?P<h>[^"]*)"[^>]*>{label}(?:</a>| )', body)
    assert match is not None, f"link do header {label!r} ausente"
    return match.group("h")


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
    assert _patient_name_line(card) == "Paciente Identificado"
    assert "84 a" in _card_meta_line(card)


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
    assert _patient_name_line(card) == "—"
    assert AGE_IN_CARD.search(_card_meta_line(card)) is None


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
    assert _patient_name_line(card) == "Paciente Zero"
    assert "0 a" in _card_meta_line(card)


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


# ── R1–R3: sufixo de desambiguação do nº duplicado ────────────────────────
# (case-detail-human-reference, slice 004 — D3)


def _set_occurrence_number(case: Case, number: str) -> Case:
    """Grava o nº de ocorrência exibido na linha rotulada do card."""
    case.agency_record_number = number
    case.save(update_fields=["agency_record_number"])
    return case


@pytest.mark.django_db
def test_duplicate_occurrence_number_gets_short_suffix_on_both_cards(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/D3: nº duplicado na página → os DOIS cards exibem ``N · <8 chars>``.

    O cenário real é o reenvio corrigido: o caso novo coexiste com o original
    compartilhando o nº — o sufixo curto identifica cada card sem expor o uid
    completo.
    """
    original = _set_occurrence_number(_create_case(nir_user, pdf_factory), "5040778")
    corrected = _set_occurrence_number(_create_case(nir_user, pdf_factory), "5040778")

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    assert f"Nº de ocorrência: <strong>5040778 · {original.short_id}</strong>" in _card_slice(
        body, original.case_id
    )
    assert f"Nº de ocorrência: <strong>5040778 · {corrected.short_id}</strong>" in _card_slice(
        body, corrected.case_id
    )


@pytest.mark.django_db
def test_unique_occurrence_number_keeps_card_output_unchanged(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/D3: nº SEM duplicado na página → card exibe apenas ``N``.

    Página com um par duplicado + um nº único: o card do nº único mantém o
    texto atual (sem sufixo e sem espaço extra) e nenhum fragmento de uid.
    """
    duplicated = _set_occurrence_number(_create_case(nir_user, pdf_factory), "5040778")
    _set_occurrence_number(_create_case(nir_user, pdf_factory), "5040778")
    unique = _set_occurrence_number(_create_case(nir_user, pdf_factory), "5040779")

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    unique_card = _card_slice(body, unique.case_id)
    assert "Nº de ocorrência: <strong>5040779</strong>" in unique_card
    assert unique.short_id not in _card_visible_text(unique_card)
    # O par duplicado continua desambiguado na mesma página.
    assert duplicated.short_id in _card_slice(body, duplicated.case_id)


@pytest.mark.django_db
def test_cards_without_occurrence_number_keep_dash_without_suffix(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/D3: caso sem nº → linha permanece ``—``, sem sufixo.

    Dois casos sem nº na mesma página: a chave vazia NÃO é tratada como nº
    duplicado (nenhum ``— · <8 chars>``), e o fallback ``uid <8 chars>`` não se
    aplica à linha rotulada (decisão vigente de ``queues-remove-uid``).
    """
    first = _set_occurrence_number(_create_case(nir_user, pdf_factory), "")
    second = _create_case(nir_user, pdf_factory)
    assert second.agency_record_number == ""

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    for case in (first, second):
        card = _card_slice(body, case.case_id)
        assert "Nº de ocorrência: <strong>—</strong>" in card
        assert case.short_id not in _card_visible_text(card)


# ── OLI-004 R1–R6: paridade da página e da lista compacta ───────────────


@pytest.mark.django_db
def test_oli004_page_title_container_and_no_top_cta_or_internal_pills(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1: título via ``page_title``, container integral, sem CTA/pills internas."""
    _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    response = client.get(reverse("intake:my_cases"))
    body = response.content.decode()

    assert response.status_code == 200
    assert '<h1 class="page-title">Todos os seus encaminhamentos</h1>' in body
    assert "operational-list-page" in body
    assert "col-12 col-lg-10" not in body
    assert "Enviar relatório" not in body
    # Navegação única é o header: nenhuma pill interna Ativos/Encerrados.
    assert 'aria-label="Filtro pela situação do caso"' not in body
    assert ">Ativos<" not in body
    assert "Meus Casos" in body
    assert "Casos Encerrados" in body


@pytest.mark.django_db
def test_oli004_header_tabs_preserve_filters_and_other_pages_stay_clean(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/R2: header carrega ``filter_qs`` só no contexto de meus casos."""
    _create_case(nir_user, pdf_factory, "relatorio-sesab.pdf")

    client.force_login(nir_user)
    body = client.get(
        reverse("intake:my_cases"),
        {"q": "5040778", "status": "FAILED", "procedure_type": "cat_cardiaco"},
    ).content.decode()

    active_href = _nav_href(body, "Meus Casos")
    closed_href = _nav_href(body, "Casos Encerrados")
    assert "tab=active" in active_href
    assert "q=5040778" in active_href
    assert "status=FAILED" in active_href
    assert "procedure_type=cat_cardiaco" in active_href
    assert "tab=closed" in closed_href
    assert "q=5040778" in closed_href
    assert "status=FAILED" in closed_href
    assert "procedure_type=cat_cardiaco" in closed_href

    # Sem filtros: hrefs limpos, sem querystring vazia/legada.
    clean_body = client.get(reverse("intake:my_cases")).content.decode()
    assert _nav_href(clean_body, "Meus Casos").endswith("?tab=active")
    assert _nav_href(clean_body, "Casos Encerrados").endswith("?tab=closed")

    # Outra página do NIR (Novo Encaminhamento) não herda filtros.
    home_body = client.get(reverse("intake:home")).content.decode()
    home_active = _nav_href(home_body, "Meus Casos")
    assert home_active.endswith("?tab=active")
    assert "q=" not in home_active
    assert "status=" not in home_active
    assert "procedure_type=" not in home_active


@pytest.mark.django_db
def test_oli004_list_header_is_neutral_single_count_without_warning(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: card ``p-4`` com «Meus Casos» + badge do total, sem alerta amarelo."""
    _create_case(nir_user, pdf_factory)
    _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()

    assert "alert-warning" not in body
    assert "Meus Casos" in body
    assert re.search(r"Meus Casos.*?2 casos", body, re.S) is not None
    assert "Atualizado automaticamente" in body
    # Live region do JS é visualmente oculto (sem segunda contagem visual).
    assert 'data-my-cases-status class="visually-hidden' in body


@pytest.mark.django_db
def test_oli004_card_compact_four_regions_and_real_cta(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: ``.case-card > .row`` em 4/3/3/2, CTA «Ver detalhes», sem resumo/espera."""
    case = _create_case(nir_user, pdf_factory, "relatorio-sesab.pdf")
    case.patient_name = "Paciente Compacto"
    case.patient_age = 64
    case.agency_record_number = "5040778"
    case.save(update_fields=["patient_name", "patient_age", "agency_record_number"])

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases")).content.decode()
    card = _card_slice(body, case.case_id)

    assert re.search(r'<div class="case-card card p-3">\s*<div class="row">', body) is not None
    assert card.index("col-md-4") < card.index("col-md-3")
    assert card.index("col-md-3") < card.index("col-md-2")
    assert 'class="patient-name"' in card or "patient-name" in card
    assert _patient_name_line(card) == "Paciente Compacto"
    assert "Recebido em" in card
    assert "Aguardando" not in card
    assert "summary-box" not in card
    assert "waiting-time" not in card
    assert "list-group-item" not in card
    detail_url = reverse("intake:case_detail", args=[case.case_id])
    assert f'href="{detail_url}"' in card
    assert "Ver detalhes" in card


@pytest.mark.django_db
def test_oli004_card_metadata_shows_demography_and_procedures(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: demografia (sexo/origem) e procedimentos nos metadados do card."""
    case = _create_case(nir_user, pdf_factory)
    case.patient_name = "Paciente Demografia"
    case.patient_age = 52
    case.patient_gender = "F"
    case.origin_unit = "Hospital Regional"
    case.save(update_fields=["patient_name", "patient_age", "patient_gender", "origin_unit"])

    client.force_login(nir_user)
    card = _card_slice(client.get(reverse("intake:my_cases")).content.decode(), case.case_id)

    meta = _card_meta_line(card)
    assert "52 a" in meta
    assert "F" in meta
    assert "Hospital Regional" in meta
    assert "Procedimentos:" in card


@pytest.mark.django_db
def test_oli004_exceptions_stay_compact_without_summary_box(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R5: falha, retenção e encerramento administrativo sem apagar dados."""
    retained = _retain_for_review(_create_case(nir_user, pdf_factory))
    failed = _fail_case(_create_case(nir_user, pdf_factory))
    closed = _create_case(nir_user, pdf_factory)
    manager = user_factory("gestor-oli004", MANAGER_ROLE)
    _close_administratively(closed, manager)

    client.force_login(nir_user)
    active_body = client.get(reverse("intake:my_cases")).content.decode()
    retained_card = _card_slice(active_body, retained.case_id)
    failed_card = _card_slice(active_body, failed.case_id)
    assert "Retido para revisão" in retained_card
    assert "Falha de processamento" in failed_card
    assert "summary-box" not in retained_card
    assert "summary-box" not in failed_card

    closed_body = client.get(reverse("intake:my_cases"), {"tab": "closed"}).content.decode()
    closed_card = _card_slice(closed_body, closed.case_id)
    assert "Encerrado administrativamente" in closed_card
    assert ADMIN_CLOSURE_REASON_TEXT in closed_card
    assert "summary-box" not in closed_card


@pytest.mark.django_db
def test_oli004_empty_with_filters_offers_limpar_filtros(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R5: vazio com filtros (partial) centralizado com «Limpar filtros»."""
    _create_case(nir_user, pdf_factory)

    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases_partial"), {"q": "9999999"}).content.decode()

    assert "Nenhum caso encontrado para os filtros aplicados." in body
    assert "Limpar filtros" in body
    assert "tab=active" in body


@pytest.mark.django_db
def test_oli004_empty_without_filters_active_offers_novo_encaminhamento(
    client: Client,
    nir_user: User,
) -> None:
    """R5: vazio sem filtros na aba ativa orienta a aba + Novo Encaminhamento."""
    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases_partial")).content.decode()

    assert "Nenhum caso ativo no momento." in body
    assert "Novo Encaminhamento" in body
    assert reverse("intake:home") in body
    assert "Limpar filtros" not in body


@pytest.mark.django_db
def test_oli004_empty_without_filters_closed_offers_novo_encaminhamento(
    client: Client,
    nir_user: User,
) -> None:
    """R5: vazio sem filtros na aba encerrados orienta a aba + Novo."""
    client.force_login(nir_user)
    body = client.get(reverse("intake:my_cases_partial"), {"tab": "closed"}).content.decode()

    assert "Nenhum caso encerrado até agora." in body
    assert "Novo Encaminhamento" in body
    assert reverse("intake:home") in body
    assert "Limpar filtros" not in body


@pytest.mark.django_db
def test_administrative_closure_retention_serve_document_200_for_creator(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """Retenção (flag False): documento de caso encerrado administrativamente
    segue servido (200) ao criador no intake."""
    case = _create_case(nir_user, pdf_factory, "relatorio-sesab.pdf")
    document = list(case.documents.all())[0]
    url = reverse("intake:serve_document", args=[case.case_id, document.pk])
    manager = user_factory("gestor-retencao-serve", MANAGER_ROLE)

    client.force_login(nir_user)
    assert client.get(url).status_code == 200

    with override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=False):
        _close_administratively(case, manager)

    assert case.status == CaseStatus.CLEANED
    assert client.get(url).status_code == 200
