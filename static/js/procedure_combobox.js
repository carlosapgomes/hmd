/**
 * Combobox pesquisável de tipo de procedimento — progressive enhancement
 * sobre um `<select>` canônico (ui-parity-intake-detail, slice 002, D2),
 * adaptado do molde `procedure_combobox.js` do ats-web (somente-leitura).
 *
 * Adaptações HMD (nunca cópia byte a byte):
 * - a busca é restrita a label + código canônico dos 14 perfis do catálogo
 *   (`option.value`); o molde lia sinônimos aprovados em atributo próprio —
 *   o catálogo HMD não tem aliases e este slice não os cria (sinônimos
 *   ficam para change futuro com decisão de produto, R3);
 * - editar/limpar o texto após confirmar uma opção limpa o canônico
 *   (R1): texto visível e valor submetido nunca divergem, e texto
 *   digitado sem confirmar submete nenhum valor (R2) — o molde mantinha
 *   o valor anterior ao editar;
 * - o hint persistente da superfície (`#procedure-type-search-hint`) é
 *   somado ao `aria-describedby` do input aprimorado.
 *
 * O `<select>` marcado com `data-procedure-combobox` permanece o único
 * valor submetido: o combobox apenas espelha nele a opção escolhida e
 * nunca aceita texto livre. Sem JavaScript o select segue visível e
 * funcional. Estilos em `.procedure-combobox*` (`static/css/app.css`).
 */
(function (global) {
  'use strict';

  // Marcas de diacrítico separadas por String.normalize('NFD') (U+0300–U+036F).
  var COMBINING_MARKS = /[\u0300-\u036f]/g;

  /** Normaliza caixa e diacríticos para comparação de busca. */
  function normalizeSearch(value) {
    return String(value == null ? '' : value)
      .normalize('NFD')
      .replace(COMBINING_MARKS, '')
      .toLowerCase()
      .trim();
  }

  /** Texto pesquisável de uma opção: label + código canônico (sem sinônimos). */
  function searchText(option) {
    return normalizeSearch(String(option.text || '') + ' ' + String(option.value || ''));
  }

  /**
   * Busca vazia mantém todas as opções; qualquer termo precisa corresponder
   * ao label canônico ou ao código canônico (nunca cria valor novo).
   */
  function matchesQuery(option, query) {
    var normalized = normalizeSearch(query);
    if (normalized === '') {
      return true;
    }
    return searchText(option).indexOf(normalized) !== -1;
  }

  /**
   * Índice da próxima opção ativa a partir de `rows` (objetos com `navigable`).
   * `delta` +1/-1 percorre circularmente e ignora opções desabilitadas; -1
   * quando nada é navegável.
   */
  function nextActiveIndex(rows, activeIndex, delta) {
    var navigable = [];
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].navigable) {
        navigable.push(i);
      }
    }
    if (navigable.length === 0) {
      return -1;
    }
    var position = navigable.indexOf(activeIndex);
    if (activeIndex < 0 || position === -1) {
      return delta > 0 ? navigable[0] : navigable[navigable.length - 1];
    }
    position += delta;
    if (position < 0) {
      position = navigable.length - 1;
    } else if (position >= navigable.length) {
      position = 0;
    }
    return navigable[position];
  }

  /** Id estável de uma opção derivado do valor canônico. */
  function optionRowId(baseId, value) {
    return baseId + '-option-' + String(value).replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  }

  /** Um nó está contido em `ancestor` (para o fechamento ao clicar fora). */
  function containsNode(ancestor, node) {
    var current = node;
    while (current) {
      if (current === ancestor) {
        return true;
      }
      current = current.parentNode;
    }
    return false;
  }

  /** Dispara `change` no select para que o restante da página reaja. */
  function dispatchChange(element) {
    var EventConstructor = global.Event;
    if (typeof EventConstructor !== 'function') {
      return;
    }
    element.dispatchEvent(new EventConstructor('change', { bubbles: true }));
  }

  /**
   * Aprimora um `<select data-procedure-combobox>` em combobox pesquisável.
   * Idempotente (re-render/dupla carga não duplica o controle). Retorna
   * `{input, list, empty}` ou `null` quando não há o que aprimorar.
   */
  function enhance(select, settings) {
    if (!select || select.getAttribute('data-combobox-enhanced') === 'true') {
      return null;
    }
    var document = (settings && settings.document) || global.document;
    if (!document || !select.parentNode || !select.options) {
      return null;
    }

    var baseId = select.id || select.getAttribute('name') || 'procedure-combobox';
    var describedBy = select.getAttribute('aria-describedby');
    // Hint persistente da superfície (associação estável nos dois fluxos):
    // entra no `aria-describedby` o `describedBy` do select (help + erro).
    var describedByIds = describedBy ? describedBy.split(/\s+/).filter(Boolean) : [];
    var hint = document.getElementById('procedure-type-search-hint');
    if (hint && describedByIds.indexOf(hint.id) === -1) {
      describedByIds.push(hint.id);
    }
    var listId = baseId + '-list';
    var emptyId = baseId + '-empty';

    select.classList.add('procedure-combobox__select');
    select.setAttribute('data-combobox-enhanced', 'true');
    // O select continua sendo o valor submetido, mas sai da árvore de
    // acessibilidade e da ordem de foco: o combobox expõe o mesmo estado e as
    // mesmas opções (sem controle duplicado para tecnologia assistiva).
    select.setAttribute('tabindex', '-1');
    select.setAttribute('aria-hidden', 'true');
    // Sem o `required` nativo, um submit sem confirmação NÃO é bloqueado
    // em silêncio pelo navegador (o select recolhido não é focável e a
    // validação nativa não exibiria nada): quem valida é o servidor, com
    // o mesmo texto de sempre (R2). Sem JS o select mantém o `required`.
    select.removeAttribute('required');

    var wrapper = document.createElement('div');
    wrapper.setAttribute('class', 'procedure-combobox procedure-combobox--enhanced');
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);

    var input = document.createElement('input');
    input.setAttribute('type', 'text');
    input.setAttribute('id', baseId + '-combobox');
    input.setAttribute('class', 'form-control procedure-combobox__input');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', listId);
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('autocomplete', 'off');
    if (describedByIds.length) {
      input.setAttribute('aria-describedby', describedByIds.join(' '));
    }
    // P1-A: o label visível (associado ao select por `for`) dá o nome
    // acessível ao input aprimorado. Sem JS o `for` nomeia o select
    // nativo; com JS o select sai da árvore de acessibilidade e o
    // input precisa do mesmo nome via `aria-labelledby` → id do label.
    // Um `aria-labelledby` pré-existente no select tem precedência.
    var labelElement = null;
    if (select.id && typeof document.querySelector === 'function') {
      labelElement = document.querySelector('label[for="' + select.id + '"]');
    }
    var labelId = labelElement ? labelElement.id : '';
    if (labelElement && !labelId) {
      labelId = baseId + '-label';
      labelElement.id = labelId;
    }
    var labelledBy = select.getAttribute('aria-labelledby') || labelId;
    if (labelledBy) {
      input.setAttribute('aria-labelledby', labelledBy);
    }
    // Placeholder é decoração: só existe quando a superfície declara o copy
    // em `data-combobox-placeholder` (vem do form, com exemplos do catálogo).
    var placeholder = select.getAttribute('data-combobox-placeholder');
    if (placeholder) {
      input.setAttribute('placeholder', placeholder);
    }
    if (select.getAttribute('aria-invalid')) {
      input.setAttribute('aria-invalid', 'true');
    }
    wrapper.appendChild(input);

    var list = document.createElement('ul');
    list.setAttribute('id', listId);
    list.setAttribute('class', 'procedure-combobox__list');
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    wrapper.appendChild(list);

    var empty = document.createElement('p');
    empty.setAttribute('id', emptyId);
    empty.setAttribute('class', 'procedure-combobox__empty');
    empty.setAttribute('role', 'status');
    empty.hidden = true;
    wrapper.appendChild(empty);

    // Uma row por opção publicada; o placeholder (valor vazio) fica fora.
    var rows = [];
    for (var i = 0; i < select.options.length; i++) {
      var option = select.options[i];
      if (option.value === '') {
        continue;
      }
      var row = document.createElement('li');
      row.setAttribute('id', optionRowId(baseId, option.value));
      row.setAttribute('role', 'option');
      row.setAttribute('data-value', option.value);
      row.setAttribute('class', 'procedure-combobox__option');
      row.textContent = option.textContent;
      if (option.disabled) {
        row.setAttribute('aria-disabled', 'true');
        row.classList.add('procedure-combobox__option--disabled');
      }
      list.appendChild(row);
      rows.push({
        element: row,
        value: option.value,
        disabled: option.disabled === true,
        text: option.textContent,
        navigable: false
      });
    }

    var activeIndex = -1;

    function isOpen() {
      return input.getAttribute('aria-expanded') === 'true';
    }

    function refreshNavigable() {
      for (var r = 0; r < rows.length; r++) {
        rows[r].navigable = rows[r].element.hidden !== true && !rows[r].disabled;
      }
    }

    function setActive(index) {
      activeIndex = index;
      for (var r = 0; r < rows.length; r++) {
        if (r === index) {
          rows[r].element.classList.add('procedure-combobox__option--active');
        } else {
          rows[r].element.classList.remove('procedure-combobox__option--active');
        }
      }
      if (index < 0) {
        input.removeAttribute('aria-activedescendant');
      } else {
        input.setAttribute('aria-activedescendant', rows[index].element.id);
        // P1-B: a listbox é rolável (max-height 16rem) — a linha ativa
        // precisa ficar visível, senão setas/End armam opção off-screen.
        var activeElement = rows[index].element;
        if (activeElement && typeof activeElement.scrollIntoView === 'function') {
          activeElement.scrollIntoView({ block: 'nearest' });
        }
      }
    }

    function setEmptyState(hasResults) {
      empty.hidden = hasResults;
      empty.textContent = hasResults ? '' : 'Nenhum procedimento encontrado para "' + input.value + '".';
      var ids = describedByIds.slice();
      if (!hasResults) {
        ids.push(emptyId);
      }
      if (ids.length) {
        input.setAttribute('aria-describedby', ids.join(' '));
      } else {
        input.removeAttribute('aria-describedby');
      }
    }

    function open() {
      if (isOpen()) {
        return;
      }
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      refreshNavigable();
      setActive(nextActiveIndex(rows, -1, 1));
    }

    function close() {
      if (!isOpen()) {
        return;
      }
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      setActive(-1);
    }

    function filter(query) {
      for (var r = 0; r < rows.length; r++) {
        rows[r].element.hidden = !matchesQuery(rows[r], query);
      }
      refreshNavigable();
      var hasResults = false;
      for (var n = 0; n < rows.length; n++) {
        if (rows[n].navigable) {
          hasResults = true;
          break;
        }
      }
      setEmptyState(hasResults);
      setActive(isOpen() ? nextActiveIndex(rows, -1, 1) : -1);
    }

    /**
     * Marca na listbox a row do valor confirmado — distinta da row ativa
     * de teclado (`--active`/`aria-activedescendant`).
     */
    function syncSelectedRow() {
      for (var r = 0; r < rows.length; r++) {
        if (rows[r].value === select.value) {
          rows[r].element.classList.add('procedure-combobox__option--selected');
          rows[r].element.setAttribute('aria-selected', 'true');
        } else {
          rows[r].element.classList.remove('procedure-combobox__option--selected');
          rows[r].element.removeAttribute('aria-selected');
        }
      }
    }

    function syncFromSelect() {
      var selected = null;
      for (var r = 0; r < rows.length; r++) {
        if (rows[r].value === select.value) {
          selected = rows[r];
        }
      }
      input.value = selected ? selected.text : '';
      syncSelectedRow();
    }

    // Só a seleção de uma opção existente escreve no select (nunca texto
    // livre). Editar o texto depois disso limpa o canônico (R1/R2): o
    // valor submetido nunca é o de uma escolha obsoleta.
    function commit(row) {
      if (!row || row.disabled) {
        return;
      }
      select.value = row.value;
      syncFromSelect();
      close();
      dispatchChange(select);
    }

    function syncCanonicalFromInput() {
      var selected = null;
      for (var r = 0; r < rows.length; r++) {
        if (rows[r].value === select.value) {
          selected = rows[r];
        }
      }
      // Texto digitado (incl. apagar tudo) que não é exatamente o label da
      // escolha confirmada invalida o canônico: o submit envia vazio até
      // uma nova confirmação por clique/Enter.
      if (selected && input.value !== selected.text) {
        select.value = '';
        syncSelectedRow();
      }
    }

    function moveActive(delta) {
      var index = nextActiveIndex(rows, activeIndex, delta);
      if (index !== -1) {
        setActive(index);
      }
    }

    function onKeyDown(event) {
      var key = event.key;
      if (key === 'ArrowDown' || key === 'ArrowUp') {
        event.preventDefault();
        if (isOpen()) {
          moveActive(key === 'ArrowDown' ? 1 : -1);
        } else {
          open();
        }
        return;
      }
      if (key === 'Home' || key === 'End') {
        if (!isOpen()) {
          return;
        }
        event.preventDefault();
        setActive(nextActiveIndex(rows, -1, key === 'Home' ? 1 : -1));
        return;
      }
      if (key === 'Enter') {
        if (!isOpen()) {
          return;
        }
        event.preventDefault();
        commit(activeIndex >= 0 ? rows[activeIndex] : null);
        return;
      }
      if (key === 'Escape') {
        if (!isOpen()) {
          return;
        }
        event.preventDefault();
        close();
        return;
      }
      if (key === 'Tab') {
        close();
      }
    }

    function bindRow(row) {
      row.element.addEventListener('click', function () {
        commit(row);
      });
    }
    for (var b = 0; b < rows.length; b++) {
      bindRow(rows[b]);
    }

    input.addEventListener('click', open);
    input.addEventListener('input', function () {
      syncCanonicalFromInput();
      filter(input.value);
      open();
    });
    input.addEventListener('keydown', onKeyDown);
    select.addEventListener('change', syncFromSelect);
    // P1-A: o clique no label visível foca/abre o input aprimorado —
    // o comportamento nativo focaria o select recolhido (oculto).
    if (labelElement) {
      labelElement.addEventListener('click', function (event) {
        event.preventDefault();
        input.focus();
        open();
      });
    }
    document.addEventListener('click', function (event) {
      if (labelElement && containsNode(labelElement, event.target)) {
        return;
      }
      if (isOpen() && !containsNode(wrapper, event.target)) {
        close();
      }
    });

    syncFromSelect();
    return { input: input, list: list, empty: empty };
  }

  /** Aprimora todos os selects publicados em `document` (auto-init da página). */
  function enhanceAll(document) {
    if (!document || typeof document.querySelectorAll !== 'function') {
      return;
    }
    var selects = document.querySelectorAll('select[data-procedure-combobox]');
    for (var i = 0; i < selects.length; i++) {
      enhance(selects[i], { document: document });
    }
  }

  global.ProcedureCombobox = {
    normalizeSearch: normalizeSearch,
    matchesQuery: matchesQuery,
    nextActiveIndex: nextActiveIndex,
    enhance: enhance,
    enhanceAll: enhanceAll
  };

  if (global.document && typeof global.document.addEventListener === 'function') {
    if (global.document.readyState === 'loading') {
      global.document.addEventListener('DOMContentLoaded', function () {
        enhanceAll(global.document);
      });
    } else {
      enhanceAll(global.document);
    }
  }
})(typeof window === 'undefined' ? this : window);
