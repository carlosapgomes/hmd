/* HMD — Envio de relatório em lote com drag-and-drop e preview.
 *
 * Rework sobre o molde ats-web (static/js/upload.js), adaptado aos
 * fields/limites/semântica HMD (ui-parity-intake-detail, slice 001, D1):
 * fields `documents` (multi-PDF) e `attachments`, limites HMD de
 * config/settings/base.py (30 arquivos, 20 MB/arquivo, 100 MB/lote;
 * anexos 5 × 10 MB — o molde usa 600 MB/lote e 10 anexos, que NÃO
 * valem aqui) e a regra HMD preservada (anexos só com exatamente 1
 * relatório: com >1 arquivo o input de anexos fica `disabled` na
 * edição — ganha a classe visual e o hint aparece; com 0 ou 1 volta
 * a ficar habilitado). O submit reabilita o input antes de sincronizar,
 * para que anexos JÁ selecionados sejam submetidos mesmo com >1
 * relatório (o navegador não submete controles desabilitados) e o
 * servidor, autoridade, emita o erro não-bloqueante nomeado (R1).
 *
 * O servidor é a fonte única da validação: este script apenas reflete
 * os limites na tela (preview: lista, contagem e tamanho total) e
 * NUNCA exclui um arquivo selecionado — avisos são só informativos;
 * todos os selecionados (não-PDF, duplicados, acima dos limites) vão
 * para os inputs via DataTransfer para o servidor nomeá-los (R1) — e
 * nunca bloqueia o submit — em especial, o gate de confirmação de
 * anexos do ats-web NÃO é portado (R4): anexo submete sem confirmação
 * extra. Sem JS, o input nativo (visível, dentro da zona) submete o
 * mesmo lote (R2).
 *
 * Arquivos arrastados são os submetidos: cada mudança de estado
 * sincroniza os inputs via DataTransfer e o submit re-sincroniza.
 * No reenvio corrigido o input de documentos não tem `multiple` e o
 * lote aceito é de exatamente 1 PDF (detectado pela ausência do
 * atributo — o mesmo script serve os dois fluxos, R3).
 *
 * Seletores vêm de data-attributes do form (templates/intake/home.html
 * e corrected_resubmission.html), sem ids hardcoded aqui para os
 * inputs; nenhuma submissão é disparada pelo script e o input
 * desabilitado nunca tem seu valor apagado pelo JS.
 */
(function () {
  "use strict";

  const DISABLED_CLASS = "intake-attachments-disabled";

  // Limites HMD (config/settings/base.py) — devem espelhar o backend
  // nos AVISOS/preview; se o ambiente sobrescrever os settings, quem
  // manda é o servidor (nenhum limite aqui exclui arquivo do submit).
  const MAX_FILES = 30;
  const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20 MB por arquivo
  const MAX_BATCH_SIZE = 100 * 1024 * 1024; // 100 MB no lote
  const MAX_ATTACHMENTS = 5;
  const MAX_ATTACHMENT_FILE_SIZE = 10 * 1024 * 1024; // 10 MB por anexo
  const ATTACHMENT_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png"];

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  }

  function getTotalSize(files) {
    let total = 0;
    for (let i = 0; i < files.length; i++) {
      total += files[i].size || 0;
    }
    return total;
  }

  function init() {
    const form = document.querySelector("[data-intake-upload]");
    if (!form) {
      return;
    }
    const documents = document.getElementById(form.dataset.documentsField);
    const attachments = document.getElementById(form.dataset.attachmentsField);
    const hint = document.getElementById(form.dataset.attachmentsSingleHint);
    const group = attachments ? attachments.closest("[data-attachments-group]") : null;
    const uploadZone = document.getElementById("upload-zone");
    const uploadPreview = document.getElementById("upload-preview");
    const fileList = document.getElementById("file-list");
    const batchCount = document.getElementById("batch-count");
    const batchTotalSize = document.getElementById("batch-total-size");
    const uploadAlert = document.getElementById("upload-alert");
    const attachmentPreview = document.getElementById("attachment-preview");
    const attachmentList = document.getElementById("attachment-list");
    const attachmentCount = document.getElementById("attachment-count");
    const attachmentTotalSize = document.getElementById("attachment-total-size");
    if (!documents || !attachments || !hint || !group || !uploadZone) {
      return;
    }

    // Reenvio corrigido: input sem `multiple` ⇒ exatamente 1 PDF.
    const isMultiple = documents.hasAttribute("multiple");
    const maxFiles = isMultiple ? MAX_FILES : 1;

    // Estado do lote: TODOS os arquivos selecionados (preview + submit).
    // O widget nunca descarta: arquivos problemáticos entram no estado
    // com apenas um aviso informativo; quem decide é o servidor (R1).
    let selectedFiles = [];
    let selectedAttachments = [];

    function showAlert(type, messages) {
      if (!uploadAlert) {
        return;
      }
      uploadAlert.className = "alert alert-" + type + " mb-3";
      uploadAlert.replaceChildren();
      const list = Array.isArray(messages) ? messages : [messages];
      for (let i = 0; i < list.length; i++) {
        if (i > 0) {
          uploadAlert.appendChild(document.createElement("br"));
        }
        uploadAlert.appendChild(document.createTextNode(list[i]));
      }
      uploadAlert.classList.remove("d-none");
    }

    function clearAlert() {
      if (!uploadAlert) {
        return;
      }
      uploadAlert.classList.add("d-none");
      uploadAlert.replaceChildren();
    }

    // Avisos informativos (nunca descartam): a autoridade é o
    // servidor — o arquivo entra no lote de qualquer forma; o aviso
    // apenas antecipa o erro que o servidor provavelmente nomeia.
    function warnFileIssues(file, existing) {
      const warnings = [];
      const fileName = file.name || "";
      const fileSize = file.size || 0;
      if (!fileName.toLowerCase().endsWith(".pdf")) {
        warnings.push(
          '"' + fileName + '" não parece um PDF — o servidor valida o conteúdo.',
        );
      }
      if (fileSize > MAX_FILE_SIZE) {
        warnings.push(
          '"' +
            fileName +
            '" acima do limite de ' +
            formatSize(MAX_FILE_SIZE) +
            " (" +
            formatSize(fileSize) +
            ") — o servidor valida o lote ao enviar.",
        );
      }
      for (let i = 0; i < existing.length; i++) {
        if (existing[i].name === fileName && existing[i].size === fileSize) {
          warnings.push(
            '"' + fileName + '" já está na seleção — o servidor processa arquivo a arquivo.',
          );
          break;
        }
      }
      return warnings;
    }

    function warnAttachmentIssues(file, existing) {
      const warnings = [];
      const fileName = file.name || "";
      const fileSize = file.size || 0;
      const ext = "." + ((fileName.toLowerCase().split(".").pop()) || "");
      if (ATTACHMENT_EXTENSIONS.indexOf(ext) === -1) {
        warnings.push(
          '"' + fileName + '" não parece um anexo aceito — o servidor valida o conteúdo.',
        );
      }
      if (fileSize > MAX_ATTACHMENT_FILE_SIZE) {
        warnings.push(
          '"' +
            fileName +
            '" acima do limite de ' +
            formatSize(MAX_ATTACHMENT_FILE_SIZE) +
            " (" +
            formatSize(fileSize) +
            ") — o servidor valida o envio ao enviar.",
        );
      }
      for (let i = 0; i < existing.length; i++) {
        if (existing[i].name === fileName && existing[i].size === fileSize) {
          warnings.push(
            '"' + fileName + '" já está na seleção — o servidor processa arquivo a arquivo.',
          );
          break;
        }
      }
      return warnings;
    }

    // Arrastados são os submetidos: reflete o estado nos inputs via
    // DataTransfer a cada mudança (e de novo no submit, por segurança).
    function syncFiles() {
      const dataTransfer = new DataTransfer();
      for (let i = 0; i < selectedFiles.length; i++) {
        dataTransfer.items.add(selectedFiles[i]);
      }
      documents.files = dataTransfer.files;
      const attachmentDataTransfer = new DataTransfer();
      for (let j = 0; j < selectedAttachments.length; j++) {
        attachmentDataTransfer.items.add(selectedAttachments[j]);
      }
      attachments.files = attachmentDataTransfer.files;
    }

    // Regra HMD preservada: anexos só com exatamente 1 relatório.
    // O `disabled` é da edição (não deixa anexar com >1 relatório);
    // no submit o input é reabilitado para que anexos JÁ selecionados
    // não sejam omitidos pelo navegador (P1-1, fix-2) — o servidor,
    // autoridade, cria os casos sem anexos e nomeia o erro (R1).
    function syncAttachmentsGroup() {
      const multipleReports = selectedFiles.length > 1;
      attachments.disabled = multipleReports;
      attachments.setAttribute("aria-disabled", multipleReports ? "true" : "false");
      group.classList.toggle(DISABLED_CLASS, multipleReports);
      hint.classList.toggle("d-none", !multipleReports);
      hint.hidden = !multipleReports;
    }

    function renderFileList() {
      if (!fileList) {
        return;
      }
      fileList.replaceChildren();
      for (let i = 0; i < selectedFiles.length; i++) {
        const file = selectedFiles[i];
        const li = document.createElement("li");
        li.className = "list-group-item d-flex justify-content-between align-items-center py-2";
        li.setAttribute("role", "listitem");

        const infoSpan = document.createElement("span");
        infoSpan.className = "d-flex align-items-center gap-2";

        const iconSpan = document.createElement("span");
        iconSpan.textContent = "📄";
        iconSpan.style.fontSize = "1.2rem";
        iconSpan.setAttribute("aria-hidden", "true");

        const nameSpan = document.createElement("span");
        nameSpan.className = "fw-semibold";
        nameSpan.textContent = file.name;

        const sizeSpan = document.createElement("span");
        sizeSpan.className = "text-muted small";
        sizeSpan.textContent = formatSize(file.size);

        infoSpan.appendChild(iconSpan);
        infoSpan.appendChild(nameSpan);
        infoSpan.appendChild(sizeSpan);

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "btn btn-sm btn-outline-danger btn-remove-file";
        removeBtn.textContent = "Remover";
        removeBtn.setAttribute("aria-label", "Remover " + file.name);
        removeBtn.setAttribute("data-index", String(i));
        (function (idx) {
          removeBtn.addEventListener("click", function () {
            removeFile(idx);
          });
        })(i);

        li.appendChild(infoSpan);
        li.appendChild(removeBtn);
        fileList.appendChild(li);
      }
    }

    function renderAttachmentList() {
      if (!attachmentList) {
        return;
      }
      attachmentList.replaceChildren();
      for (let i = 0; i < selectedAttachments.length; i++) {
        const file = selectedAttachments[i];
        const li = document.createElement("li");
        li.className = "list-group-item d-flex justify-content-between align-items-center py-2";
        li.setAttribute("role", "listitem");

        const infoSpan = document.createElement("span");
        infoSpan.className = "d-flex align-items-center gap-2";

        const iconSpan = document.createElement("span");
        iconSpan.textContent = "📎";
        iconSpan.style.fontSize = "1.2rem";
        iconSpan.setAttribute("aria-hidden", "true");

        const nameSpan = document.createElement("span");
        nameSpan.className = "fw-semibold";
        nameSpan.textContent = file.name;

        const sizeSpan = document.createElement("span");
        sizeSpan.className = "text-muted small";
        sizeSpan.textContent = formatSize(file.size);

        infoSpan.appendChild(iconSpan);
        infoSpan.appendChild(nameSpan);
        infoSpan.appendChild(sizeSpan);

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "btn btn-sm btn-outline-danger btn-remove-attachment";
        removeBtn.textContent = "Remover";
        removeBtn.setAttribute("aria-label", "Remover anexo " + file.name);
        removeBtn.setAttribute("data-attachment-index", String(i));
        (function (idx) {
          removeBtn.addEventListener("click", function () {
            removeAttachment(idx);
          });
        })(i);

        li.appendChild(infoSpan);
        li.appendChild(removeBtn);
        attachmentList.appendChild(li);
      }
    }

    function updateUI() {
      const zoneText = uploadZone.querySelector(".upload-zone__text");
      const zoneHint = uploadZone.querySelector(".upload-zone__hint");
      if (selectedFiles.length === 0) {
        if (uploadPreview) {
          uploadPreview.classList.add("d-none");
        }
        if (zoneText) {
          zoneText.textContent = isMultiple
            ? "Arraste os PDFs aqui ou clique para selecionar"
            : "Arraste o PDF aqui ou clique para selecionar";
        }
        if (zoneHint) {
          zoneHint.textContent = isMultiple
            ? "Apenas arquivos PDF — até 20 MB cada"
            : "Apenas 1 arquivo PDF — até 20 MB";
        }
      } else {
        if (uploadPreview) {
          uploadPreview.classList.remove("d-none");
        }
        if (batchCount) {
          batchCount.textContent = selectedFiles.length + " arquivo(s) selecionado(s)";
        }
        if (batchTotalSize) {
          batchTotalSize.textContent = formatSize(getTotalSize(selectedFiles));
        }
        renderFileList();
        if (zoneText) {
          zoneText.textContent =
            selectedFiles.length + " arquivo(s) selecionado(s) — clique para adicionar mais";
        }
        if (zoneHint) {
          if (selectedFiles.length > maxFiles) {
            zoneHint.textContent =
              selectedFiles.length +
              " arquivo(s) acima do limite de " +
              maxFiles +
              " (" +
              formatSize(MAX_BATCH_SIZE) +
              " total) — o servidor valida o lote inteiro ao enviar";
          } else {
            zoneHint.textContent = isMultiple
              ? "Ainda cabem " +
                (maxFiles - selectedFiles.length) +
                " arquivo(s) (máx. " +
                maxFiles +
                ", " +
                formatSize(MAX_BATCH_SIZE) +
                " total)"
              : "Apenas 1 PDF por envio — limite de " + formatSize(MAX_FILE_SIZE) + " por arquivo";
          }
        }
      }
      syncAttachmentsGroup();
      syncFiles();
    }

    function updateAttachmentUI() {
      if (!attachmentPreview) {
        return;
      }
      if (selectedAttachments.length === 0) {
        attachmentPreview.classList.add("d-none");
        return;
      }
      attachmentPreview.classList.remove("d-none");
      if (attachmentCount) {
        attachmentCount.textContent = selectedAttachments.length + " anexo(s)";
      }
      if (attachmentTotalSize) {
        attachmentTotalSize.textContent = formatSize(getTotalSize(selectedAttachments));
      }
      renderAttachmentList();
    }

    function removeFile(index) {
      if (index >= 0 && index < selectedFiles.length) {
        selectedFiles.splice(index, 1);
        updateUI();
        clearAlert();
      }
    }

    function removeAttachment(index) {
      if (index >= 0 && index < selectedAttachments.length) {
        selectedAttachments.splice(index, 1);
        updateAttachmentUI();
        syncFiles();
        clearAlert();
      }
    }

    function handleFiles(fileListRaw) {
      const filesArray = Array.prototype.slice.call(fileListRaw);
      if (filesArray.length === 0) {
        return;
      }
      documents.value = "";

      // A autoridade é o servidor (R1): TODOS os novos arquivos entram
      // no estado (preview + submit) — avisos são só informativos, sem
      // nenhum descarte/retorno antecipado por contagem ou tamanho.
      const warnings = [];
      for (let i = 0; i < filesArray.length; i++) {
        warnings.push(...warnFileIssues(filesArray[i], selectedFiles));
      }
      selectedFiles = selectedFiles.concat(filesArray);
      if (selectedFiles.length > maxFiles) {
        warnings.push(
          isMultiple
            ? "Máximo de " +
                maxFiles +
                " arquivos por lote — o servidor valida o lote inteiro ao enviar."
            : "Selecione exatamente 1 PDF do relatório corrigido — o servidor valida ao enviar.",
        );
      }
      if (getTotalSize(selectedFiles) > MAX_BATCH_SIZE) {
        warnings.push(
          "Tamanho total do lote acima do limite de " +
            formatSize(MAX_BATCH_SIZE) +
            " — o servidor valida o lote ao enviar.",
        );
      }
      updateUI();
      if (warnings.length > 0) {
        showAlert("warning", warnings);
      } else {
        clearAlert();
      }
    }

    function handleAttachmentFiles(fileListRaw) {
      const filesArray = Array.prototype.slice.call(fileListRaw);
      if (filesArray.length === 0) {
        return;
      }
      attachments.value = "";

      // Idem: todos os anexos selecionados entram no estado — um anexo
      // inválido com 1 PDF aborta no servidor (specs/attachments);
      // filtrá-lo aqui permitiria a criação que o servidor proíbe.
      const warnings = [];
      for (let i = 0; i < filesArray.length; i++) {
        warnings.push(...warnAttachmentIssues(filesArray[i], selectedAttachments));
      }
      selectedAttachments = selectedAttachments.concat(filesArray);
      if (selectedAttachments.length > MAX_ATTACHMENTS) {
        warnings.push(
          "Máximo de " +
            MAX_ATTACHMENTS +
            " anexos — o servidor valida o envio ao enviar.",
        );
      }
      updateAttachmentUI();
      syncFiles();
      if (warnings.length > 0) {
        showAlert("warning", warnings);
      } else {
        clearAlert();
      }
    }

    // ── Zona: clique (fora do próprio input) abre o seletor nativo ──
    uploadZone.addEventListener("click", function (event) {
      if (event.target.closest("input, button, a")) {
        return;
      }
      documents.click();
    });

    // ── Zona: drag-and-drop ──────────────────────────────────────────
    uploadZone.addEventListener("dragover", function (event) {
      event.preventDefault();
      uploadZone.classList.add("drag-over");
    });

    uploadZone.addEventListener("dragleave", function () {
      uploadZone.classList.remove("drag-over");
    });

    uploadZone.addEventListener("drop", function (event) {
      event.preventDefault();
      uploadZone.classList.remove("drag-over");
      if (event.dataTransfer.files.length > 0) {
        handleFiles(event.dataTransfer.files);
      }
    });

    documents.addEventListener("change", function () {
      if (documents.files.length > 0) {
        handleFiles(documents.files);
      }
    });

    attachments.addEventListener("change", function () {
      if (attachments.files.length > 0) {
        handleAttachmentFiles(attachments.files);
      }
    });

    // Submit: reabilita os anexos já selecionados (o navegador não
    // submete controles `disabled` — sem isso o servidor nunca veria
    // os anexos com >1 relatório e não poderia emitir o erro
    // não-bloqueante nomeado) e sincroniza os arrastados nos inputs —
    // nunca bloqueia (sem gate de confirmação; o servidor é a
    // autoridade, R1/R4).
    form.addEventListener("submit", function () {
      attachments.disabled = false;
      syncFiles();
    });

    updateUI();
    updateAttachmentUI();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
