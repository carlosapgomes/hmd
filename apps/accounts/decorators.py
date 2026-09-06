"""Decorators de autorização por papel ativo (slice 005, R4).

Referência: ats-web ``apps/accounts/decorators.py``. Divergência deliberada do
HMD (R4): lá o papel ativo errado redireciona para ``/`` com mensagem; aqui o
acesso é negado com HTTP 403 explícito (contrato da spec account-access).
"""

from collections.abc import Callable
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

ROLE_DENIED_MESSAGE = "Seu papel ativo não permite acessar esta página."


def role_required(
    *allowed_roles: str,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Protege a view pelo papel ativo da sessão (R4).

    Uso::

        @role_required("doctor", "manager")
        def my_view(request): ...

    - Usuário anônimo → redireciona ao login (LOGIN_URL);
    - Papel ativo fora de ``allowed_roles`` → HTTP 403 (PermissionDenied),
      ainda que o usuário possua o papel entre os seus.
    """

    def decorator(
        view_func: Callable[..., HttpResponse],
    ) -> Callable[..., HttpResponse]:
        @wraps(view_func)
        def _wrapper(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.session.get("active_role") not in allowed_roles:
                raise PermissionDenied(ROLE_DENIED_MESSAGE)
            return view_func(request, *args, **kwargs)

        return _wrapper

    return decorator
