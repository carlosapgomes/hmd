"""URLs de conta, sessão, manual e notificações (slice 004, R5; slice 005, R1).

Incluídas na raiz em ``config/urls.py`` **SEM namespace** (D2/Nota de rotas):
seus nomes (``home``, ``login``, ``manual``, ``notifications``, …) são globais e
referenciados sem prefixo. Rotas de notificação in-app adicionadas no slice 002
do change 11; a de manual no slice 005.
"""

from django.urls import URLPattern, path

from . import views

urlpatterns: list[URLPattern] = [
    path("", views.home_view, name="home"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("switch-role/", views.switch_role_view, name="switch_role"),
    path("profile/", views.profile_view, name="profile"),
    # Manual de uso (change 11, slice 005; nome GLOBAL, sem namespace).
    path("manual/", views.manual_view, name="manual"),
    # Notificações in-app (change 11, slice 002; nomes GLOBAIS, sem namespace).
    path("notifications/", views.notifications_list, name="notifications"),
    path(
        "notifications/<uuid:notification_id>/open/",
        views.notification_open,
        name="notifications_open",
    ),
    path(
        "notifications/mark-all-read/",
        views.notifications_mark_all_read,
        name="notifications_mark_all_read",
    ),
    path(
        "notifications/unread-count/",
        views.notifications_unread_count,
        name="notifications_unread_count",
    ),
]
