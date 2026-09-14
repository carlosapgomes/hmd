"""Testes do envio em lote e da primitiva de caso único (slice 001, R1/R2).

R1: ``submit_report_batch`` cria **um caso por PDF** (tipo único por lote),
com falhas parciais (arquivo inválido/erro de persistência por arquivo
preservando os casos já criados), limites de lote (contagem/tamanho total) e
regra de anexos × nº de PDFs (anexos só com exatamente 1 PDF; multi-PDF cria
casos sem anexos; 1 PDF + anexo inválido aborta tudo). R2: a primitiva
``create_case_with_documents`` cria atomicamente 1 caso com 1 documento, 1
tipo e anexos opcionais. Cobre também o fluxo via view (papel ativo ``nir``;
papel diferente → 403).

Os casos nascem em ``NEW`` (R7): o módulo desliga o processamento automático
(``INTAKE_RUN_TASKS_INLINE=False``) porque os PDFs fake não têm camada de
texto — o caminho inline em si é coberto em test_tasks.py.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.cases.events import CaseEventType
from apps.cases.models import Case, CaseDocument, CaseEvent, CaseProcedure, CaseStatus
from apps.cases.procedures import set_declared_procedures
from apps.intake.services import create_case_with_documents, submit_report_batch

NIR_ROLE = "nir"
DOCTOR_ROLE = "doctor"
ANGIO_TYPE = "art_perif"


@pytest.fixture(autouse=True)
def _creation_without_processing() -> Iterator[None]:
    """Criação apenas (R7): processamento desligado nos testes deste módulo.

    Sem o override, o caso avançaria sozinho (inline pinado no test.py) com os
    PDFs fake (sem camada de texto); os testes fixam o comportamento "nasce em
    NEW".
    """

    with override_settings(INTAKE_RUN_TASKS_INLINE=False):
        yield


def _assert_nothing_persisted() -> None:
    """Zero efeito em banco: validação falhou antes de qualquer escrita (R2)."""
    assert Case.objects.count() == 0
    assert CaseDocument.objects.count() == 0
    assert CaseProcedure.objects.count() == 0
    assert CaseEvent.objects.count() == 0


def _attachment(name: str, content_type: str = "image/jpeg") -> SimpleUploadedFile:
    """Anexo fake (conteúdo não lido neste slice)."""
    return SimpleUploadedFile(name, b"anexo fake", content_type=content_type)


# ── R2: primitiva de caso único ───────────────────────────────────────────


@pytest.mark.django_db
def test_single_case_primitive_single_document_and_type(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: a primitiva cria 1 caso NEW com 1 documento (position 1), o tipo
    único declarado e o evento de declaração na trilha."""
    uploaded = pdf_factory(name="relatorio.pdf")
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=uploaded,
        procedure_type=ANGIO_TYPE,
    )

    assert case.status == CaseStatus.NEW
    assert case.created_by == nir_user
    documents = list(case.documents.all())
    assert len(documents) == 1
    assert documents[0].position == 1
    assert documents[0].original_filename == "relatorio.pdf"
    assert documents[0].content_type == "application/pdf"
    assert documents[0].uploaded_by == nir_user
    assert (documents[0].file.name or "").startswith(f"case_documents/{case.case_id}/")

    declared_rows = CaseProcedure.objects.filter(case=case, declared_by_nir=True)
    assert {row.procedure_type for row in declared_rows} == {ANGIO_TYPE}
    event = case.events.get(event_type=CaseEventType.CASE_PROCEDURES_DECLARED)
    assert event.actor == nir_user
    assert event.actor_role == NIR_ROLE
    assert event.payload == {"procedure_types": [ANGIO_TYPE]}


@pytest.mark.django_db
def test_single_case_primitive_new_fields_defaults(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: campos de extração/gate chegam vazios/neutros na criação."""
    case = create_case_with_documents(
        user=nir_user,
        role=NIR_ROLE,
        file=pdf_factory(),
        procedure_type=ANGIO_TYPE,
    )

    assert case.status == CaseStatus.NEW
    assert case.extracted_text == ""
    assert case.manual_review_required is False
    assert case.manual_review_reason == ""


@pytest.mark.django_db
def test_single_case_primitive_rejects_non_pdf(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: arquivo não-PDF é rejeitado nomeando o arquivo, sem persistir nada."""
    with pytest.raises(ValueError, match="imagem.png"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="imagem.png", content_type="image/png"),
            procedure_type=ANGIO_TYPE,
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_single_case_primitive_rejects_extension(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: PDF com content-type mas extensão errada também é rejeitado."""
    with pytest.raises(ValueError, match="relatorio.txt"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(name="relatorio.txt"),
            procedure_type=ANGIO_TYPE,
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_single_case_primitive_rejects_absent_type(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: tipo ausente é rejeitado sem criar nada."""
    with pytest.raises(ValueError, match="único tipo"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(),
            procedure_type="",
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_single_case_primitive_rejects_invalid_type(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: tipo fora do catálogo rejeita nomeando o tipo, sem criar nada."""
    with pytest.raises(ValueError, match="fora do catálogo"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(),
            procedure_type="procedimento_inexistente",
        )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_single_case_primitive_file_over_size_limit(
    nir_user: User,
) -> None:
    """R2: arquivo acima de INTAKE_MAX_UPLOAD_BYTES_PER_FILE é rejeitado
    nomeando o arquivo."""
    big_file = SimpleUploadedFile(
        "grande.pdf",
        b"x" * (1024 * 1024 + 1),
        content_type="application/pdf",
    )

    with override_settings(INTAKE_MAX_UPLOAD_BYTES_PER_FILE=1):
        with pytest.raises(ValueError, match="grande.pdf"):
            create_case_with_documents(
                user=nir_user,
                role=NIR_ROLE,
                file=big_file,
                procedure_type=ANGIO_TYPE,
            )

    _assert_nothing_persisted()


@pytest.mark.django_db
def test_single_case_primitive_invalid_attachment_rejects_all(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R2: anexo inválido rejeita TUDO antes de qualquer gravação."""
    with pytest.raises(ValueError, match="diagrama.gif"):
        create_case_with_documents(
            user=nir_user,
            role=NIR_ROLE,
            file=pdf_factory(),
            procedure_type=ANGIO_TYPE,
            attachments=[_attachment("diagrama.gif", "image/gif")],
        )

    _assert_nothing_persisted()


# ── R1: envio em lote ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submit_batch_creates_one_case_per_pdf(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1/cenário spec: 2 PDFs válidos + 1 tipo → 2 casos independentes em NEW,
    cada um com 1 documento e o tipo declarado."""
    files = [pdf_factory(name="a.pdf"), pdf_factory(name="b.pdf")]

    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=files,
        procedure_type=ANGIO_TYPE,
    )

    assert errors == []
    assert len(cases) == 2
    assert {case.status for case in cases} == {CaseStatus.NEW}
    assert [case.documents.get().original_filename for case in cases] == ["a.pdf", "b.pdf"]
    for case in cases:
        assert case.documents.count() == 1
        assert {row.procedure_type for row in case.procedures.filter(declared_by_nir=True)} == {
            ANGIO_TYPE
        }


@pytest.mark.django_db
def test_submit_batch_invalid_file_rejects_only_it(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1/cenário spec: lote com 2 PDFs válidos + 1 imagem cria os 2 casos e
    lista o erro nomeado do arquivo inválido, sem interromper os demais."""
    files = [
        pdf_factory(name="a.pdf"),
        pdf_factory(name="imagem.png", content_type="image/png"),
        pdf_factory(name="b.pdf"),
    ]

    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=files,
        procedure_type=ANGIO_TYPE,
    )

    assert [case.documents.get().original_filename for case in cases] == ["a.pdf", "b.pdf"]
    assert len(errors) == 1
    assert "imagem.png" in errors[0]


@pytest.mark.django_db
def test_submit_batch_absent_type_rejects_all(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1: tipo ausente rejeita o lote inteiro (nenhum caso criado)."""
    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory(), pdf_factory()],
        procedure_type="",
    )

    assert cases == []
    assert len(errors) == 1
    assert "único tipo" in errors[0]
    _assert_nothing_persisted()


@pytest.mark.django_db
def test_submit_batch_invalid_type_rejects_all(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1/cenário spec: tipo fora do catálogo rejeita o lote inteiro."""
    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory()],
        procedure_type="procedimento_inexistente",
    )

    assert cases == []
    assert len(errors) == 1
    assert "fora do catálogo" in errors[0]
    _assert_nothing_persisted()


@pytest.mark.django_db
def test_submit_batch_empty_rejects(
    nir_user: User,
) -> None:
    """R1: lote vazio é rejeitado antes de qualquer persistência."""
    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=[],
        procedure_type=ANGIO_TYPE,
    )

    assert cases == []
    assert len(errors) == 1
    assert "ao menos um" in errors[0]
    _assert_nothing_persisted()


@pytest.mark.django_db
def test_submit_batch_count_limit_rejects_all(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1/cenário spec: acima do limite de arquivos por lote → nada criado."""
    files = [pdf_factory() for _ in range(3)]

    with override_settings(INTAKE_MAX_FILES_PER_BATCH=2):
        cases, errors = submit_report_batch(
            user=nir_user,
            role=NIR_ROLE,
            files=files,
            procedure_type=ANGIO_TYPE,
        )

    assert cases == []
    assert len(errors) == 1
    assert "Máximo de 2" in errors[0]
    _assert_nothing_persisted()


@pytest.mark.django_db
def test_submit_batch_total_size_limit_rejects_all(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1/cenário spec: tamanho total do lote acima do limite → nada criado."""
    files = [pdf_factory(), pdf_factory()]

    with override_settings(INTAKE_MAX_UPLOAD_BYTES_PER_BATCH=1):
        cases, errors = submit_report_batch(
            user=nir_user,
            role=NIR_ROLE,
            files=files,
            procedure_type=ANGIO_TYPE,
        )

    assert cases == []
    assert len(errors) == 1
    assert "Tamanho total do lote" in errors[0]
    _assert_nothing_persisted()


@pytest.mark.django_db
def test_submit_batch_single_pdf_attachments_saved(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1: 1 PDF + anexos válidos → anexos gravados no caso único."""
    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory()],
        procedure_type=ANGIO_TYPE,
        attachments=[_attachment("foto.jpg")],
    )

    assert errors == []
    assert len(cases) == 1
    assert cases[0].attachments.count() == 1


@pytest.mark.django_db
def test_submit_batch_single_pdf_invalid_attachment_aborts(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1/cenário spec: 1 PDF + anexo inválido aborta tudo (nada criado)."""
    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory()],
        procedure_type=ANGIO_TYPE,
        attachments=[_attachment("diagrama.gif", "image/gif")],
    )

    assert cases == []
    assert len(errors) == 1
    assert "diagrama.gif" in errors[0]
    _assert_nothing_persisted()


@pytest.mark.django_db
def test_submit_batch_multi_pdf_with_attachments_creates_without_attachments(
    nir_user: User, pdf_factory: Callable[..., SimpleUploadedFile]
) -> None:
    """R1/cenário spec: 2 PDFs + anexos → 2 casos SEM anexos e o erro informa
    que anexos só são permitidos com exatamente 1 relatório."""
    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory(), pdf_factory()],
        procedure_type=ANGIO_TYPE,
        attachments=[_attachment("foto.jpg")],
    )

    assert len(cases) == 2
    assert all(case.attachments.count() == 0 for case in cases)
    assert len(errors) == 1
    assert "exatamente 1" in errors[0]


@pytest.mark.django_db
def test_submit_batch_persistence_failure_preserves_earlier_cases(
    monkeypatch: pytest.MonkeyPatch,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R1/cenário spec: exceção de persistência no 2º caso vira erro por
    arquivo (nome + "erro interno") e o 1º caso permanece criado e íntegro."""
    real = set_declared_procedures
    calls = {"count": 0}

    def _flaky(
        case: Case,
        procedure_types: Iterable[str],
        *,
        user: User | None,
        role: str | None,
    ) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("falha simulada de persistência")
        real(case, procedure_types, user=user, role=role)

    monkeypatch.setattr("apps.intake.services.set_declared_procedures", _flaky)

    cases, errors = submit_report_batch(
        user=nir_user,
        role=NIR_ROLE,
        files=[pdf_factory(name="a.pdf"), pdf_factory(name="b.pdf")],
        procedure_type=ANGIO_TYPE,
    )

    assert [case.documents.get().original_filename for case in cases] == ["a.pdf"]
    assert len(errors) == 1
    assert "b.pdf" in errors[0]
    assert "erro interno" in errors[0]
    assert Case.objects.count() == 1


# ── Fluxo via view (R4/R5 do slice original — UI migrada no slice 002) ─────


@pytest.mark.django_db
def test_upload_flow_via_view(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """GET renderiza o form (1–N PDFs + tipo único em radio); POST de 1 PDF +
    1 tipo cria o caso e redireciona ao detalhe (contrato redirect×resultado
    preservado: 1 caso sem erros — design D4)."""
    client.force_login(nir_user)
    url = reverse("intake:home")

    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="documents"' in body
    assert 'type="radio"' in body
    assert "Arteriografia periférica" in body
    assert 'value="cat_cardiaco"' in body

    response = client.post(
        url,
        {
            "documents": [pdf_factory()],
            "procedure_type": ANGIO_TYPE,
        },
        follow=True,
    )
    assert response.status_code == 200

    case = Case.objects.get()
    assert case.status == CaseStatus.NEW
    assert case.created_by == nir_user
    assert case.documents.count() == 1
    assert {row.procedure_type for row in case.procedures.filter(declared_by_nir=True)} == {
        ANGIO_TYPE
    }
    assert "criado com sucesso" in response.content.decode()
    assert response.redirect_chain[-1][0].endswith(
        reverse("intake:case_detail", args=[str(case.case_id)])
    )


@pytest.mark.django_db
def test_upload_invalid_file_shows_result_with_error(
    client: Client,
    nir_user: User,
    pdf_factory: Callable[..., SimpleUploadedFile],
) -> None:
    """R4/design D4: arquivo inválido → página de resultado com o erro
    nomeando o arquivo (nenhum caso criado)."""
    client.force_login(nir_user)
    url = reverse("intake:home")

    response = client.post(
        url,
        {
            "documents": [pdf_factory(name="foto.jpg", content_type="image/jpeg")],
            "procedure_type": ANGIO_TYPE,
        },
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "foto.jpg" in body
    assert "0 casos criados" in body
    assert Case.objects.count() == 0


@pytest.mark.django_db
def test_non_nir_role_forbidden(client: Client, user_factory: Callable[[str, str], User]) -> None:
    """R5: papel ativo diferente de nir recebe HTTP 403 no intake."""
    doctor = user_factory("doctor-intake", DOCTOR_ROLE)
    client.force_login(doctor)

    response = client.get(reverse("intake:home"))

    assert response.status_code == 403
