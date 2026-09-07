"""Testes de criação de caso com upload multi-PDF (slice 001 do intake NIR).

Cobre R1 (``CaseDocument`` ordenado + unicidade (case, position)), R2 (campos
novos do ``Case``: ``extracted_text``/``manual_review_required``/
``manual_review_reason``), R3 (serviço atômico — validação 100% antes de
persistir; erro nomeia arquivo/tipo), R4/R5 (fluxo via view com papel ativo
``nir``; papel diferente → 403) e os 3 cenários da spec "Criação de caso com
upload multi-PDF e declaração de tipos".
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseDocument, CaseEvent, CaseProcedure, CaseStatus
from apps.intake.services import create_case_with_documents

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação apenas (slice 001): processamento desligado nos testes deste módulo.

    Os PDFs fake destes testes não têm camada de texto/estrutura lida pelo
    extrator — o enqueue pós-transação (slice 003) roda inline por default na
    suíte e processaria/descartaria esses arquivos. Os testes de criação fixam
    o comportamento que testam (criação até NEW) desligando o processamento;
    o caminho inline em si é coberto em test_tasks.py.
    """

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _assert_nothing_persisted() -> None:
    """Zero efeito em banco: validação falhou antes de qualquer escrita (R3)."""
    assert Case.objects.count() == 0
    assert CaseDocument.objects.count() == 0
    assert CaseProcedure.objects.count() == 0
    assert CaseEvent.objects.count() == 0


@pytest.mark.django_db
def test_documents_ordered_and_unique(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1: documentos ordenados por position + constraint única (case, position)."""
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory(), pdf_factory(), pdf_factory()],
        procedure_types=["art_perif"],
    )

    assert [document.position for document in case.documents.all()] == [1, 2, 3]
    prefix = f"case_documents/{case.case_id}/"
    assert all((document.file.name or "").startswith(prefix) for document in case.documents.all())
    assert all((document.file.name or "").endswith(".pdf") for document in case.documents.all())

    # Segunda posição no mesmo caso viola a constraint única.
    duplicate = CaseDocument(
        case=case,
        file=pdf_factory(),
        position=1,
        original_filename="duplicado.pdf",
        content_type="application/pdf",
        size_bytes=1,
        uploaded_by=nir_user,
    )
    with pytest.raises(IntegrityError):
        duplicate.save()


@pytest.mark.django_db
def test_case_new_fields_defaults(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: campos de extração/gate chegam vazios/neutros na criação."""
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory()],
        procedure_types=["cat_cardiaco"],
    )

    assert case.status == CaseStatus.NEW
    assert case.extracted_text == ""
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""


@pytest.mark.django_db
def test_create_atomic_success(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R3/cenário spec: 2 PDFs + 2 tipos → caso NEW, docs ordenados, 2 rows declaradas e evento."""
    files = [pdf_factory(), pdf_factory()]
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        files=files,
        procedure_types=["art_perif", "cat_cardiaco"],
    )

    assert case.created_by == nir_user
    assert case.status == CaseStatus.NEW

    documents = list(case.documents.all())
    assert len(documents) == 2
    assert [document.position for document in documents] == [1, 2]
    assert [document.original_filename for document in documents] == [
        files[0].name,
        files[1].name,
    ]
    assert all(document.content_type == "application/pdf" for document in documents)
    assert all(document.uploaded_by == nir_user for document in documents)
    assert all(document.size_bytes > 0 for document in documents)
    prefix = f"case_documents/{case.case_id}/"
    assert all((document.file.name or "").startswith(prefix) for document in documents)
    assert all((document.file.name or "").endswith(".pdf") for document in documents)

    declared_rows = CaseProcedure.objects.filter(case=case, declared_by_nir=True)
    assert len(declared_rows) == 2
    assert {row.procedure_type for row in declared_rows} == {"art_perif", "cat_cardiaco"}

    event = case.events.get(event_type=CaseEventType.CASE_PROCEDURES_DECLARED)
    assert event.actor == nir_user
    assert event.actor_role == NIR_ROLE
    assert event.payload == {"procedure_types": ["art_perif", "cat_cardiaco"]}


@pytest.mark.django_db
def test_non_pdf_rejects_all(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R3/cenário spec: 1 PDF + 1 imagem rejeitam a criação inteira nomeando o arquivo."""
    batch = [pdf_factory(), pdf_factory(name="imagem.png", content_type="image/png")]

    with pytest.raises(ValueError, match="imagem.png"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            files=batch,
            procedure_types=["art_perif"],
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_non_pdf_extension_rejects(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R3: PDF com content-type mas extensão errada também é rejeitado nomeando o arquivo."""
    batch = [pdf_factory(name="relatorio.txt")]

    with pytest.raises(ValueError, match="relatorio.txt"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            files=batch,
            procedure_types=["art_perif"],
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_over_count_limit_rejects(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R3/cenário spec: acima do limite de documentos → rejeição antes de persistir."""
    batch = [pdf_factory(), pdf_factory(), pdf_factory()]

    with override_settings(INTAKE_MAX_DOCUMENTS=2):
        with pytest.raises(ValueError, match="Máximo de 2"):
            create_case_with_documents(
                user=nir_user,
                role=NIR_ROLE,
                files=batch,
                procedure_types=["art_perif"],
            )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_file_over_size_limit_rejects(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R3: arquivo acima de INTAKE_MAX_FILE_MB é rejeitado nomeando o arquivo."""
    big_file = SimpleUploadedFile(
        "grande.pdf",
        b"x" * (1024 * 1024 + 1),
        content_type="application/pdf",
    )

    with override_settings(INTAKE_MAX_FILE_MB=1):
        with pytest.raises(ValueError, match="grande.pdf"):
            create_case_with_documents(
                user=nir_user,
                role=NIR_ROLE,
                files=[big_file],
                procedure_types=["art_perif"],
            )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_requires_at_least_one_type(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R3: lote sem tipo declarado é rejeitado sem criar nada."""
    with pytest.raises(ValueError, match="ao menos um tipo"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            files=[pdf_factory()],
            procedure_types=[],
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_invalid_procedure_type_rejects_all(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R3: tipo fora do catálogo rejeita o lote inteiro nomeando o tipo."""
    with pytest.raises(ValueError, match="fora do catálogo"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            files=[pdf_factory()],
            procedure_types=["art_perif", "procedimento_inexistente"],
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_upload_flow_via_view(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4/R5: GET renderiza o form; POST cria o caso e redireciona ao detalhe.

    O destino do redirect mudou no slice 004 (R5): do POST de criação vai para
    o detalhe do caso criado (antes apontava à home placeholder).
    """
    client.force_login(nir_user)
    url = reverse("intake:home")

    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="documents"' in body
    assert "Arteriografia periférica" in body
    assert 'value="cat_cardiaco"' in body

    response = client.post(
        url,
        {
            "documents": [pdf_factory(), pdf_factory()],
            "procedure_types": ["art_perif", "cat_cardiaco"],
        },
        follow=True,
    )
    assert response.status_code == 200

    case = Case.objects.get()
    assert case.status == CaseStatus.NEW
    assert case.created_by == nir_user
    assert case.documents.count() == 2
    assert {row.procedure_type for row in case.procedures.filter(declared_by_nir=True)} == {
        "art_perif",
        "cat_cardiaco",
    }
    assert "criado com sucesso" in response.content.decode()
    # R5 (slice 004): o redirect final do POST é o detalhe do caso criado.
    assert response.redirect_chain[-1][0].endswith(
        reverse("intake:case_detail", args=[str(case.case_id)])
    )


@pytest.mark.django_db
def test_upload_invalid_rerenders_with_error(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: lote inválido re-renderiza o form com o resumo nomeando o arquivo."""
    client.force_login(nir_user)
    url = reverse("intake:home")

    response = client.post(
        url,
        {"documents": [pdf_factory(name="foto.jpg", content_type="image/jpeg")]},
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "foto.jpg" in body
    assert Case.objects.count() == 0


@pytest.mark.django_db
def test_non_nir_role_forbidden(client: Client, user_factory: Callable[[str, str], User]) -> None:
    """R5: papel ativo diferente de nir recebe HTTP 403 no intake."""
    doctor = user_factory("doctor-intake", DOCTOR_ROLE)
    client.force_login(doctor)

    response = client.get(reverse("intake:home"))

    assert response.status_code == 403
