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

from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
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
    """Cria um caso NEW com 1–N PDFs (nomes opcionais) e dois tipos declarados."""
    files = [pdf_factory(name=name) if name else pdf_factory() for name in (names or ("",))]
    return create_case_with_documents(
        user=user,
        role=NIR_ROLE,
        files=files,
        procedure_types=["art_perif", "cat_cardiaco"],
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
def test_detail_shows_docs_events_communications(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R2/cenário spec: detalhe do próprio caso mostra docs, trilha e comunicações."""
    case = _retain_for_review(
        _create_case(nir_user, pdf_factory, "relatorio-sesab.pdf", "pagina-2.pdf")
    )
    documents = list(case.documents.all())
    assert len(documents) == 2
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
    # Trilha de eventos com labels legíveis.
    assert "Trilha de eventos" in body
    assert "Procedimentos declarados pelo NIR" in body
    assert "Extraindo PDF" in body
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
        files=[uploaded],
        procedure_types=["art_perif"],
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
            "procedure_types": ["art_perif"],
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
