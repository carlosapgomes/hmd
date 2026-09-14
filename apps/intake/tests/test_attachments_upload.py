"""Testes do upload de anexos no intake (slice 001, R3–R5).

Cobre R3 (kwarg ADITIVO ``attachments`` em ``create_case_with_documents``:
rows+arquivos no MESMO atomic da criação com path seguro/``status=pending``;
anexo inválido rejeita TUDO antes de qualquer gravação; default ``()``
preserva o change 04 — regressão testada; ``create_corrected_resubmission``
repassa), R4 (form de criação aceita anexos múltiplos via UI; o detalhe do
NIR lista os anexos com nome/tamanho/tipo/status legível; sem anexos → bloco
ausente) e a regressão do reenvio sem anexos.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.attachments.models import AttachmentStatus, CaseAttachment
from apps.attachments.services import AttachmentValidationError
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseDocument, CaseEvent, CaseProcedure, CaseStatus
from apps.intake.services import create_case_with_documents, create_corrected_resubmission

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
SYSTEM_ROLE = "system"
ANGIO_TYPE = "art_perif"

# Extensão de arquivo derivada do MIME aceito (D1/D7 — espelho do path).
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
}
_UUID4_HEX_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|pdf)$")


@pytest.fixture(autouse=True)
def _attachments_without_inline_processing() -> Iterator[None]:
    """Módulo sem processamento automático pós-criação (padrão do intake).

    Os PDFs fake não têm camada de texto — os testes de anexo fixam o
    comportamento que exercitam (criação até ``NEW`` + rows de anexo)
    desligando o processamento inline (mesmo padrão do test_creation.py).
    """

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


@pytest.fixture
def attachment_factory() -> Callable[..., SimpleUploadedFile]:
    """Fábrica de arquivos de anexo fake (conteúdo não lido neste slice)."""

    counter = itertools.count(1)

    def _factory(
        name: str | None = None,
        content_type: str = "image/jpeg",
        content: bytes | None = None,
    ) -> SimpleUploadedFile:
        ordinal = next(counter)
        extension = _CONTENT_TYPE_EXTENSIONS.get(content_type, ".bin")
        filename = name or f"anexo-{ordinal}{extension}"
        return SimpleUploadedFile(
            filename,
            content or b"anexo fake (conteudo nao lido neste slice)",
            content_type=content_type,
        )

    return _factory


def _drive_to_cleaned(case: Case, *, nir: User, doctor: User) -> None:
    """Dirige o caso pelas operações FSM até ``CLEANED`` (setup dos testes)."""
    case.start_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_pdf_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_anonymization(user=None, role=SYSTEM_ROLE)
    case.complete_llm_extraction(user=None, role=SYSTEM_ROLE)
    case.complete_llm_summarization(user=None, role=SYSTEM_ROLE)
    case.record_doctor_decision(accepted=False, user=doctor, role=DOCTOR_ROLE)
    case.post_final_reply(user=doctor, role=DOCTOR_ROLE)
    case.nir_acknowledge(user=nir, role=NIR_ROLE)
    case.start_cleaning(user=nir, role=NIR_ROLE)
    case.complete_cleaning(user=nir, role=NIR_ROLE)
    case.refresh_from_db()
    assert case.status == CaseStatus.CLEANED


def _cleaned_case(*, created_by: User, doctor: User) -> Case:
    """Caso ``CLEANED`` do criador (setup dos testes de resubmission)."""
    case = Case.objects.create(created_by=created_by)
    _drive_to_cleaned(case, nir=created_by, doctor=doctor)
    return case


def _event_types(case: Case) -> list[str]:
    """Tipos de evento da trilha na ordem de gravação."""
    return [event.event_type for event in case.events.order_by("id")]


def _assert_attachment_row(
    attachment: CaseAttachment,
    *,
    case: Case,
    user: User,
    original_name: str,
    content_type: str,
) -> None:
    """Row + arquivo de um anexo gravado no atomic da criação (R3/D1)."""
    stored_name = attachment.file.name or ""
    prefix = f"case_attachments/{case.case_id}/"
    assert stored_name.startswith(prefix)
    basename = stored_name.rsplit("/", 1)[-1]
    assert _UUID4_HEX_RE.fullmatch(basename) is not None
    assert basename.endswith(_CONTENT_TYPE_EXTENSIONS[content_type])
    assert original_name not in stored_name
    assert attachment.original_filename == original_name
    assert attachment.content_type == content_type
    assert attachment.size_bytes > 0
    assert attachment.uploaded_by == user
    assert attachment.status == AttachmentStatus.PENDING
    assert attachment.extraction_method == ""
    assert attachment.extracted_text == ""
    assert attachment.patient_match is None
    assert attachment.processed_at is None


def _assert_nothing_persisted() -> None:
    """Zero efeito em banco: validação falhou antes de qualquer escrita (R3)."""
    assert Case.objects.count() == 0
    assert CaseDocument.objects.count() == 0
    assert CaseAttachment.objects.count() == 0
    assert CaseProcedure.objects.count() == 0
    assert CaseEvent.objects.count() == 0


def _valid_document(
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> SimpleUploadedFile:
    """PDF fake do relatório (1 arquivo — a primitiva cria 1 caso por PDF)."""
    return pdf_factory(name="relatorio.pdf")


# ── R3: serviço — rows + arquivos no atomic da criação ────────────────────


@pytest.mark.django_db
def test_create_case_with_attachments(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: no MESMO atomic da criação, as rows+arquivos dos anexos são
    gravados com path seguro, ``uploaded_by=user`` e ``status=pending``."""
    jpg = attachment_factory(name="foto-exame.jpg", content_type="image/jpeg")
    pdf_attachment = attachment_factory(name="exame-extra.pdf", content_type="application/pdf")

    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=_valid_document(pdf_factory),
        procedure_type=ANGIO_TYPE,
        attachments=[jpg, pdf_attachment],
    )

    assert case.status == CaseStatus.NEW
    assert case.documents.count() == 1
    attachments = list(case.attachments.all())
    assert len(attachments) == 2
    by_content_type = {attachment.content_type: attachment for attachment in attachments}
    assert set(by_content_type) == {"image/jpeg", "application/pdf"}
    _assert_attachment_row(
        by_content_type["image/jpeg"],
        case=case,
        user=nir_user,
        original_name="foto-exame.jpg",
        content_type="image/jpeg",
    )
    _assert_attachment_row(
        by_content_type["application/pdf"],
        case=case,
        user=nir_user,
        original_name="exame-extra.pdf",
        content_type="application/pdf",
    )


@pytest.mark.django_db
def test_create_without_attachments_unchanged(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/regressão change 04: sem o kwarg ``attachments`` (default ``()``) a
    criação pura segue idêntica — nenhuma row de anexo, caso/documento/declaração
    intactos."""
    uploaded = pdf_factory(name="relatorio-1.pdf")

    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=uploaded,
        procedure_type=ANGIO_TYPE,
    )

    assert case.status == CaseStatus.NEW
    assert case.documents.count() == 1
    assert case.attachments.count() == 0
    assert CaseAttachment.objects.count() == 0
    assert CaseProcedure.objects.filter(case=case, declared_by_nir=True).count() == 1
    assert _event_types(case) == [CaseEventType.CASE_PROCEDURES_DECLARED.value]


@pytest.mark.django_db
def test_invalid_attachment_type_rejects_all(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: anexo com MIME não aceito rejeita TUDO antes de qualquer gravação
    (nem caso, nem documentos) nomeando o arquivo."""
    with pytest.raises(AttachmentValidationError, match="diagrama.gif"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=_valid_document(pdf_factory),
            procedure_type=ANGIO_TYPE,
            attachments=[attachment_factory(name="diagrama.gif", content_type="image/gif")],
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_invalid_attachment_size_rejects_all(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: anexo acima de ATTACHMENTS_MAX_SIZE_MB rejeita TUDO nomeando o
    arquivo — antes de qualquer gravação."""
    oversized = SimpleUploadedFile(
        "enorme.png",
        b"x" * (1024 * 1024 + 1),
        content_type="image/png",
    )

    with override_settings(ATTACHMENTS_MAX_SIZE_MB=1):
        with pytest.raises(AttachmentValidationError, match="enorme.png"):
            create_case_with_documents(
                user=nir_user,
                role=NIR_ROLE,
                file=_valid_document(pdf_factory),
                procedure_type=ANGIO_TYPE,
                attachments=[oversized],
            )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_invalid_attachment_count_rejects_all(
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: lote de anexos acima de ATTACHMENTS_MAX_COUNT rejeita TUDO — antes
    de qualquer gravação."""
    many = [
        attachment_factory(content_type="image/jpeg"),
        attachment_factory(content_type="image/png"),
        attachment_factory(content_type="application/pdf"),
    ]

    with override_settings(ATTACHMENTS_MAX_COUNT=2):
        with pytest.raises(AttachmentValidationError, match="Máximo de 2"):
            create_case_with_documents(
                user=nir_user,
                role=NIR_ROLE,
                file=_valid_document(pdf_factory),
                procedure_type=ANGIO_TYPE,
                attachments=many,
            )

    _assert_nothing_persisted()


# ── R3: resubmission repassa attachments ──────────────────────────────────


@pytest.mark.django_db
def test_resubmission_with_attachments(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: o reenvio corrigido repassa o kwarg aditivo — o NOVO caso nasce com
    as rows+arquivos de anexo (path com o id do novo caso), o original segue
    CLEANED e sem anexos."""
    doctor = user_factory("doctor-resub-att", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    jpg = attachment_factory(name="foto-reenvio.jpg", content_type="image/jpeg")

    new_case = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_valid_document(pdf_factory),
        procedure_type=ANGIO_TYPE,
        correction_reason="laudo com dados divergentes do exame original",
        attachments=[jpg],
    )

    assert new_case.status == CaseStatus.NEW
    assert new_case.corrects_case_id == original.case_id
    assert new_case.attachments.count() == 1
    _assert_attachment_row(
        new_case.attachments.get(),
        case=new_case,
        user=nir_user,
        original_name="foto-reenvio.jpg",
        content_type="image/jpeg",
    )
    # O original não herda anexos (novo intake = nova seleção) e segue íntegro.
    original.refresh_from_db()
    assert original.status == CaseStatus.CLEANED
    assert original.attachments.count() == 0
    assert CaseEventType.CASE_MARKED_SUPERSEDED.value in _event_types(original)


@pytest.mark.django_db
def test_resubmission_without_attachments_unchanged(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3/regressão: resubmission sem o kwarg (default ``()``) não cria anexos
    e mantém o fluxo do change 09 (novo caso vinculado + eventos)."""
    doctor = user_factory("doctor-resub-noatt", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)

    new_case = create_corrected_resubmission(
        original_case=original,
        user=nir_user,
        role=NIR_ROLE,
        file=_valid_document(pdf_factory),
        procedure_type=ANGIO_TYPE,
        correction_reason="revisão do laudo",
    )

    assert new_case.corrects_case_id == original.case_id
    assert new_case.attachments.count() == 0
    assert CaseAttachment.objects.count() == 0
    assert CaseEventType.CASE_CORRECTION_CREATED.value in _event_types(new_case)


@pytest.mark.django_db
def test_resubmission_invalid_attachment_rejected(
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R3: anexo inválido no reenvio corrigido rejeita TUDO — nenhum caso novo,
    original intacto sem evento de supersedição."""
    doctor = user_factory("doctor-resub-invatt", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    events_before = original.events.count()

    with pytest.raises(AttachmentValidationError, match="fora-do-padrao.bin"):
        create_corrected_resubmission(
            original_case=original,
            user=nir_user,
            role=NIR_ROLE,
            file=_valid_document(pdf_factory),
            procedure_type=ANGIO_TYPE,
            correction_reason="revisão do laudo",
            attachments=[
                SimpleUploadedFile(
                    "fora-do-padrao.bin",
                    b"conteudo",
                    content_type="application/octet-stream",
                )
            ],
        )

    assert Case.objects.count() == 1
    assert CaseAttachment.objects.count() == 0
    assert original.status == CaseStatus.CLEANED
    assert original.events.count() == events_before


# ── R4: UI — form de criação e detalhe do NIR ─────────────────────────────


@pytest.mark.django_db
def test_upload_ui_with_attachments(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: o form de criação expõe o input múltiplo de anexos; o POST cria o
    caso com as rows de anexo (status ``pending``) e o detalhe lista o bloco."""
    client.force_login(nir_user)
    url = reverse("intake:home")

    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="attachments"' in body
    assert "multiple" in body
    assert 'name="documents"' in body

    response = client.post(
        url,
        {
            "documents": [pdf_factory()],
            "procedure_type": ANGIO_TYPE,
            "attachments": [
                attachment_factory(name="foto.jpg"),
                attachment_factory(name="exame.pdf", content_type="application/pdf"),
            ],
        },
        follow=True,
    )

    assert response.status_code == 200
    case = Case.objects.get()
    assert case.status == CaseStatus.NEW
    assert case.attachments.count() == 2
    assert {attachment.content_type for attachment in case.attachments.all()} == {
        "image/jpeg",
        "application/pdf",
    }
    assert all(
        attachment.status == AttachmentStatus.PENDING for attachment in case.attachments.all()
    )
    assert "criado com sucesso" in response.content.decode()
    assert "Anexos" in response.content.decode()


@pytest.mark.django_db
def test_detail_lists_attachments_status(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: o detalhe do NIR lista cada anexo com nome/tamanho/tipo e o status
    legível via ``get_status_display`` (bloco "Anexos")."""
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=_valid_document(pdf_factory),
        procedure_type=ANGIO_TYPE,
        attachments=[attachment_factory(name="evidencia-1.jpg", content_type="image/jpeg")],
    )
    attachment = case.attachments.get()
    stored_name = attachment.file.name or ""
    assert stored_name != ""
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    body = response.content.decode()
    block_start = body.index("Anexos")
    block = body[block_start:]
    assert "evidencia-1.jpg" in block  # nome legível (original_filename), não o path UUID
    assert stored_name not in block
    assert "image/jpeg" in block
    assert str(attachment.size_bytes) in block
    assert attachment.get_status_display() in block  # "Pendente"


@pytest.mark.django_db
def test_detail_hides_block_without_attachments(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4: caso sem anexos → o detalhe não tem o bloco "Anexos" (UI idêntica à
    do change 04)."""
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=_valid_document(pdf_factory),
        procedure_type=ANGIO_TYPE,
    )
    client.force_login(nir_user)

    response = client.get(reverse("intake:case_detail", args=[case.case_id]))

    assert response.status_code == 200
    assert "Anexos" not in response.content.decode()


@pytest.mark.django_db
def test_resubmission_ui_with_attachments(
    client: Client,
    nir_user: User,
    user_factory: Callable[[str, str], User],
    pdf_factory: Callable[..., SimpleUploadedFile],
    attachment_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4/F2 (review slice 001): o form do reenvio corrigido expõe o input de
    anexos — o POST cria o novo caso com as rows de anexo, sem herdar nada do
    original."""
    doctor = user_factory("doctor-resub-att-ui", DOCTOR_ROLE)
    original = _cleaned_case(created_by=nir_user, doctor=doctor)
    client.force_login(nir_user)
    url = reverse("intake:case_resubmit", args=[original.case_id])

    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="attachments"' in body
    assert "multiple" in body

    response = client.post(
        url,
        {
            "documents": [pdf_factory()],
            "procedure_type": ANGIO_TYPE,
            "correction_reason": "laudo ilegível, reenvio com foto do exame",
            "attachments": [attachment_factory(name="foto-exame.jpg", content_type="image/jpeg")],
        },
        follow=True,
    )

    assert response.status_code == 200
    new_case = Case.objects.exclude(pk=original.pk).get()
    assert new_case.corrects_case_id == original.case_id
    _assert_attachment_row(
        new_case.attachments.get(),
        case=new_case,
        user=nir_user,
        original_name="foto-exame.jpg",
        content_type="image/jpeg",
    )
    original.refresh_from_db()
    assert original.attachments.count() == 0
