/* HMD — Heartbeat e liberação da reserva do caso (change case-work-lock, slice 003, D3).
 *
 * Adaptado de ``/projects/dev/ats-web/static/js/work_lock.js`` (mesma lógica,
 * já em produção no ATS Web): monitora a atividade humana na página e renova
 * periodicamente a reserva (lease) enquanto o usuário interage. Ao navegar
 * para fora (clique em link, ``pagehide``) tenta liberar a reserva
 * (best-effort — o «Sair sem decidir» também libera server-side).
 *
 * Guarda de submit: quando um formulário que contém o ``lock_token`` corrente
 * é submetido, uma flag impede o release de disparar — evita a corrida em que
 * ``pagehide`` liberaria a reserva antes do POST principal terminar.
 *
 * Configuração lida do elemento com ``data-work-lock-config`` (ou id
 * ``work-lock-config``) — renderizado em ``templates/doctor/decide.html``:
 *   data-renew-url     — endpoint POST de renovação da reserva
 *   data-release-url   — endpoint POST de liberação da reserva
 *   data-lock-token    — token da reserva corrente (UUID)
 *   data-heartbeat-ms  — intervalo do heartbeat em ms (default 60000)
 *   data-grace-ms      — inatividade máxima antes de pausar a renovação
 *                        (default 240000)
 *   data-debug         — "true" habilita o log de depuração
 *
 * Sem dependências externas.
 */
(function () {
    "use strict";

    var configEl = document.querySelector('[data-work-lock-config]');
    if (!configEl) {
        configEl = document.getElementById('work-lock-config');
    }
    if (!configEl) return;  /* página sem reserva gerenciada pelo script */

    var renewUrl    = configEl.getAttribute('data-renew-url') || '';
    var releaseUrl  = configEl.getAttribute('data-release-url') || '';
    var lockToken   = configEl.getAttribute('data-lock-token') || '';
    var heartbeatMs = parseInt(configEl.getAttribute('data-heartbeat-ms'), 10) || 60000;
    var graceMs     = parseInt(configEl.getAttribute('data-grace-ms'), 10) || 240000;

    if (!renewUrl || !releaseUrl || !lockToken) return;

    /* ── Guarda de submit protegido ─────────────────────────────────── */

    var protectedSubmitInProgress = false;

    function isProtectedSubmitInProgress() {
        return protectedSubmitInProgress || window.HMD_WORK_LOCK_SUBMITTING === true;
    }

    /* Ouve submits de formulários com o NOSSO token.
     * Fase de bubble para que um modal/validação possa chamar
     * ``preventDefault()`` antes. Só marca como protegido quando o submit NÃO
     * foi cancelado e o formulário contém ``input[name="lock_token"]`` com o
     * valor do token corrente — o mini-form do «Sair sem decidir» também
     * contém o token, e nesse caso o release server-side é quem libera. */
    document.addEventListener('submit', function (event) {
        if (event.defaultPrevented) return;

        var form = event.target;
        if (!form || !form.querySelector) return;

        var tokenInput = form.querySelector('input[name="lock_token"]');
        if (tokenInput && tokenInput.value === lockToken) {
            protectedSubmitInProgress = true;
            window.HMD_WORK_LOCK_SUBMITTING = true;
            log('submit protegido detectado — release suprimido');
        }
    });

    /* ── Log de depuração (gate ``data-debug``) ────────────────────── */

    var DEBUG = configEl.getAttribute('data-debug') === 'true';

    function log() {
        if (!DEBUG) return;
        var args = Array.prototype.slice.call(arguments);
        args.unshift('[work-lock]');
        console.log.apply(console, args);
    }

    log('inicializado — renewUrl:', renewUrl, 'token:', lockToken);

    /* ── Rastreio de atividade ─────────────────────────────────────── */

    var lastActivity = Date.now();
    var activityEvents = ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart', 'focus'];

    function onActivity() {
        lastActivity = Date.now();
        hideWarning();
    }

    for (var i = 0; i < activityEvents.length; i++) {
        window.addEventListener(activityEvents[i], onActivity, { passive: true });
    }

    function hasRecentActivity() {
        return (Date.now() - lastActivity) < graceMs;
    }

    /* ── Elemento de aviso ─────────────────────────────────────────── */

    var warningEl = document.getElementById('work-lock-warning');

    function showWarning(msg) {
        if (!warningEl) return;
        warningEl.textContent = msg;
        warningEl.style.display = 'block';
    }

    function hideWarning() {
        if (!warningEl) return;
        warningEl.style.display = 'none';
        warningEl.textContent = '';
    }

    function disableSubmit() {
        var btn = document.getElementById('btn-submit');
        if (btn) btn.disabled = true;
    }

    /* ── CSRF ──────────────────────────────────────────────────────── */

    function getCSRFToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');

        var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (csrfInput) return csrfInput.value;

        var cookie = document.cookie.match(/csrftoken=([^;]+)/);
        return cookie ? cookie[1] : '';
    }

    /* ── Heartbeat de renovação ────────────────────────────────────── */

    function sendRenew() {
        if (!hasRecentActivity()) {
            return;  /* sem atividade recente — não renova (a lease vence) */
        }

        var body = new FormData();
        body.append('lock_token', lockToken);

        fetch(renewUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCSRFToken(),
            },
            body: body,
            credentials: 'same-origin',
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    /* Sem resetar ``lastActivity``: renovar NÃO fabrica
                     * atividade — a grace de 4min continua contando a partir
                     * da última interação real do usuário, e uma aba
                     * abandonada deixa de renovar e perde a reserva
                     * (review P1 do case-work-lock). */
                    hideWarning();
                } else {
                    showWarning(data.error || 'Falha ao renovar reserva.');
                    disableSubmit();
                }
            })
            .catch(function () {
                showWarning('Erro de conexão ao renovar reserva.');
                disableSubmit();
            });
    }

    /* ── Liberação da reserva ──────────────────────────────────────── */

    var released = false;

    function sendRelease() {
        /* Não libera durante um submit protegido. */
        if (isProtectedSubmitInProgress()) return;

        if (released) return;
        released = true;

        var body = new FormData();
        body.append('lock_token', lockToken);
        var csrfToken = getCSRFToken();

        log('sendRelease — url:', releaseUrl, 'token:', lockToken);

        fetch(releaseUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken,
            },
            body: body,
            credentials: 'same-origin',
            keepalive: true,
        }).then(function (r) {
            log('release — status:', r.status, r.statusText);
        }).catch(function (e) {
            log('erro no release:', e);
        });
    }

    /* ── Gatilhos de liberação ─────────────────────────────────────── */

    /* 1. Intercepta cliques em links que navegam para FORA da página da
     *    reserva — o gatilho mais confiável, dispara antes da navegação. */
    document.addEventListener('click', function (e) {
        var link = e.target.closest('a[href]');
        if (!link) return;

        var href = link.getAttribute('href') || '';
        /* Ignora links ``javascript:`` e âncoras internas (ex.: «Ir para o
         * formulário de decisão»). */
        if (href.indexOf('javascript:') === 0 || href.charAt(0) === '#') return;
        /* Ignora links que abrem em nova aba. */
        if (link.target === '_blank') return;

        log('clique em link interceptado — href:', href);
        sendRelease();
    }, true);

    /* 2. ``pagehide`` — dispara quando a página está sendo descarregada. */
    window.addEventListener('pagehide', function () {
        log('pagehide — enviando release');
        sendRelease();
    });

    /* ── Heartbeat periódico ───────────────────────────────────────── */

    setInterval(sendRenew, heartbeatMs);
})();
