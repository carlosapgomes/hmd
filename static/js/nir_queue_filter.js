/* nir_queue_filter.js — Reaplicação pós-swap de "meus casos" (HMD).
 *
 * Diferente das filas médicas/agendador (busca 100 % client-side), em
 * "meus casos" a busca textual é SERVER-side (ui-parity-queues slice 003,
 * R2/D4): este script NÃO filtra cards no cliente. A sua única função é
 * reaplicar o estado de UI fora do fragmento trocado pelo htmx após cada
 * swap do auto-refresh de 20 s — o contador de cards exibidos
 * (``[data-my-cases-status]``, com ``aria-live="polite"``) é recalculado
 * sobre o fragmento novo, em vez de ficar com o total da página cheia.
 * OLI-004: esse live region é visualmente oculto (``visually-hidden``)
 * — a única contagem visual é o badge do cabeçalho da lista — e segue
 * atualizado após cada swap.
 *
 * Sem dependências, sem persistência (URL/storage/cookie/sessão).
 */
(function () {
  "use strict";

  var statusEl = null;

  /** Recalcula o contador de cards exibidos no fragmento corrente. */
  function applyStatus() {
    if (!statusEl) return;
    var cards = document.querySelectorAll("[data-my-cases-card]");
    var total = cards.length;
    if (total === 1) {
      statusEl.textContent = "Mostrando 1 caso.";
    } else {
      statusEl.textContent = "Mostrando " + total + " casos.";
    }
  }

  /** Reaplica o contador após o swap do polling (controle fora do swap). */
  function onHtmxAfterSwap(e) {
    if (e && e.detail && e.detail.target && e.detail.target.id === "my-cases-content") {
      applyStatus();
    }
  }

  function init() {
    statusEl = document.querySelector("[data-my-cases-status]");
    if (!statusEl) return;
    document.addEventListener("htmx:afterSwap", onHtmxAfterSwap);
    applyStatus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
