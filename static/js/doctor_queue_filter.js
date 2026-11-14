/* doctor_queue_filter.js — Filtros client-side da fila médica (HMD).
 *
 * Adaptado do molde `doctor_queue_filter.js` do ats-web para as rotas/
 * campos do HMD: o "tipo de exame" é o subtipo de procedimento declarado
 * (``data-exam-type`` do card, lista separada por espaço — um caso pode
 * ter vários subtipos) e as opções do <select> [data-doctor-exam-filter]
 * são renderizadas pelo template a partir dos subtipos do usuário
 * (``data-exam-type-label``/``data-exam-type-count``), nunca hardcoded
 * aqui. A busca é 100 % client-side por nome/ocorrência sobre os cards
 * da página (D3): o filtro ``?subtype=`` server-side continua
 * autoritativo — este JS só oculta cards do renderizado, nunca amplia
 * o escopo. Os controles vivem FORA de #doctor-queue-content, então o
 * polling htmx de 20 s não os reseta; após cada swap, o handler
 * ``htmx:afterSwap`` reaplica busca + filtro ativos.
 *
 * Sem dependências, sem persistência (URL/storage/cookie/sessão).
 */
(function () {
  "use strict";

  var searchInput = null;
  var clearButton = null;
  var statusEl = null;
  var noResultsEl = null;
  var typeSelect = null;

  function resolveElements() {
    searchInput = document.querySelector("[data-doctor-queue-search]");
    clearButton = document.querySelector("[data-doctor-queue-clear]");
    statusEl = document.querySelector("[data-doctor-queue-filter-status]");
    noResultsEl = document.querySelector("[data-doctor-queue-no-results]");
    typeSelect = document.querySelector("select[data-doctor-exam-filter]");
  }

  /** Normaliza: minúsculas, sem diacríticos (NFD), sem espaços das pontas. */
  function normalize(text) {
    return String(text)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function getCards() {
    return document.querySelectorAll("[data-doctor-queue-card]");
  }

  function selectedTypeOption() {
    if (!typeSelect) return null;
    return typeSelect.options[typeSelect.selectedIndex] || null;
  }

  function getSelectedType() {
    return typeSelect && typeSelect.value ? typeSelect.value : "all";
  }

  function scopeLabel() {
    var option = selectedTypeOption();
    if (!option) return "Todos";
    return option.getAttribute("data-exam-type-label") || option.value || "Todos";
  }

  function pluralCasos(n) {
    return n !== 1 ? "casos" : "caso";
  }

  /** Subtipos do card (``data-exam-type`` separado por espaço). */
  function cardSubtypes(card) {
    var raw = card.getAttribute("data-exam-type") || "";
    return raw.split(/\s+/).filter(Boolean);
  }

  function emptyCounts() {
    var counts = {};
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-exam-type-count]"),
      function (el) {
        counts[el.getAttribute("data-exam-type-count") || "all"] = 0;
      }
    );
    return counts;
  }

  /** Contadores por subtipo recomputados dos cards e rótulos "(n)" refeitos. */
  function updateCounts() {
    var cards = getCards();
    var counts = emptyCounts();
    counts.all = cards.length;
    Array.prototype.forEach.call(cards, function (card) {
      cardSubtypes(card).forEach(function (subtype) {
        if (counts[subtype] !== undefined) counts[subtype]++;
      });
    });
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-exam-type-count]"),
      function (el) {
        var type = el.getAttribute("data-exam-type-count") || "all";
        var label = el.getAttribute("data-exam-type-label") || type;
        var count = counts[type] !== undefined ? counts[type] : 0;
        el.textContent = label + " (" + count + ")";
      }
    );
  }

  /** Filtro composto exame + termo; só oculta (nunca amplia o server). */
  function applyFilter() {
    if (!statusEl || !noResultsEl) return;

    var term = searchInput ? searchInput.value : "";
    var trimmed = term.trim();
    var type = getSelectedType();

    if (clearButton) {
      clearButton.style.display = trimmed.length > 0 ? "" : "none";
    }

    updateCounts();

    var cards = getCards();
    var total = cards.length;
    var normTerm = normalize(trimmed);
    var visibleCount = 0;

    Array.prototype.forEach.call(cards, function (card) {
      var matchesType = type === "all" || cardSubtypes(card).indexOf(type) !== -1;
      var matchesTerm = true;
      if (trimmed.length > 0) {
        var name = card.getAttribute("data-patient-name") || "";
        var record = card.getAttribute("data-agency-record-number") || "";
        matchesTerm =
          normalize(name).indexOf(normTerm) !== -1 ||
          normalize(record).indexOf(normTerm) !== -1;
      }
      var visible = matchesType && matchesTerm;
      card.hidden = !visible;
      if (visible) visibleCount++;
    });

    noResultsEl.style.display = "none";
    if (visibleCount === 0) {
      if (total > 0) {
        noResultsEl.style.display = "";
        statusEl.textContent = "";
      } else {
        statusEl.textContent = "";
      }
      return;
    }
    var scope = type !== "all" ? " de " + scopeLabel() + "." : ".";
    if (visibleCount === total) {
      statusEl.textContent =
        "Mostrando todos os " + total + " " + pluralCasos(total) + scope;
    } else {
      statusEl.textContent =
        "Mostrando " + visibleCount + " de " + total + " " + pluralCasos(total) + scope;
    }
  }

  function clearFilter() {
    if (!searchInput) return;
    searchInput.value = "";
    applyFilter();
    searchInput.focus();
  }

  function onInput() {
    applyFilter();
  }

  function onKeydown(e) {
    if (e.key === "Escape" && document.activeElement === searchInput) {
      e.preventDefault();
      clearFilter();
    }
  }

  function onClearClick() {
    clearFilter();
  }

  function onTypeChange() {
    applyFilter();
  }

  /** Reaplica busca/filtro após o swap do polling (controles fora do swap). */
  function onHtmxAfterSwap(e) {
    if (e && e.detail && e.detail.target && e.detail.target.id === "doctor-queue-content") {
      applyFilter();
    }
  }

  function init() {
    resolveElements();
    if (!statusEl || !noResultsEl || !typeSelect) return;

    if (searchInput) {
      searchInput.addEventListener("input", onInput);
      searchInput.addEventListener("keydown", onKeydown);
    }
    if (clearButton) {
      clearButton.addEventListener("click", onClearClick);
    }
    typeSelect.addEventListener("change", onTypeChange);
    document.addEventListener("htmx:afterSwap", onHtmxAfterSwap);
    applyFilter();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
