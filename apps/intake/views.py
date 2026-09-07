"""Views do intake do NIR (change intake-nir-upload, slice 001, R4/R5).

``intake_home`` é a tela de envio do relatório (multi-PDF + declaração de
tipos): GET renderiza o form; POST valida tudo via
``apps.intake.services.create_case_with_documents`` — erro de validação
re-renderiza o form com resumo nomeando arquivo/tipo; sucesso redireciona à
home com mensagem (meus casos/detalhe são o slice 004; o redirect ajusta lá).
"""

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.decorators import role_required
from apps.accounts.models import User

from .forms import IntakeUploadForm
from .services import create_case_with_documents

logger = logging.getLogger(__name__)


def _require_user(request: HttpRequest) -> User:
    """Usuário autenticado de uma view já protegida por ``@role_required``."""
    user = request.user
    if not isinstance(user, User):
        raise PermissionDenied
    return user


@role_required("nir")
def intake_home(request: HttpRequest) -> HttpResponse:
    """Home do intake NIR: form de envio do relatório com tipos declarados."""
    user = _require_user(request)
    active_role = request.session.get("active_role", "")
    form = IntakeUploadForm()

    if request.method == "POST":
        form = IntakeUploadForm(request.POST, request.FILES)
        if form.is_valid():
            documents = form.cleaned_data["documents"]
            procedure_types = form.cleaned_data["procedure_types"]
            try:
                case = create_case_with_documents(
                    user=user,
                    role=active_role,
                    files=documents,
                    procedure_types=procedure_types,
                )
            except ValueError as exc:
                logger.warning("intake_upload_rejected user=%s motivo=%s", user.pk, exc)
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request,
                    f"Caso {case.case_id} criado com sucesso.",
                )
                # Meus casos/detalhe são o slice 004 — por ora a home recebe o
                # redirect do POST (out_of_scope do slice).
                return redirect(reverse("home"))

    return render(request, "intake/home.html", {"form": form})
