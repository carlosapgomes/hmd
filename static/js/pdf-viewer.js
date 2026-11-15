/**
 * PDF Viewer — Vanilla JS PDF.js viewer with lazy rendering.
 *
 * Dependencies: pdfjsLib (imported externally via module script).
 * No framework/bundler dependency.
 */

/**
 * Initialize the PDF viewer.
 *
 * @param {Object} options
 * @param {Object} options.pdfjsLib - pdf.js library object
 * @param {string} options.pdfUrl - URL of the PDF to load (protected route)
 * @param {string} options.fallbackUrl - Fallback URL to open the PDF directly
 * @param {HTMLElement} options.container - Element to render pages into
 * @param {HTMLElement} options.statusEl - Loading indicator element
 * @param {HTMLElement} options.errorEl - Error message element
 * @param {number} [options.scale=1.5] - Render scale
 */
export function initPdfViewer({
  pdfjsLib,
  pdfUrl,
  fallbackUrl,
  container,
  statusEl,
  errorEl,
  scale = 1.5,
}) {
  if (!pdfjsLib || !pdfUrl || !container) {
    console.error('PDF viewer: missing required options');
    return;
  }

  const loadingTask = pdfjsLib.getDocument(pdfUrl);

  loadingTask.promise
    .then(function (pdfDoc) {
      if (statusEl) {
        statusEl.classList.add('d-none'); // hide loading
      }

      const numPages = pdfDoc.numPages;

      // Create page placeholders
      for (let i = 1; i <= numPages; i++) {
        const pageEl = createPagePlaceholder(i);
        container.appendChild(pageEl);
      }

      // Lazy rendering with IntersectionObserver
      if (window.IntersectionObserver) {
        const observer = new IntersectionObserver(
          function (entries) {
            entries.forEach(function (entry) {
              if (entry.isIntersecting) {
                const pageNum = parseInt(entry.target.dataset.pageNum, 10);
                if (!entry.target.dataset.rendered) {
                  entry.target.dataset.rendered = '1';
                  renderPage(pdfDoc, pageNum, entry.target, scale);
                  observer.unobserve(entry.target);
                }
              }
            });
          },
          {
            rootMargin: '200px 0px',
            threshold: 0.01,
          }
        );

        const placeholders = container.querySelectorAll('.pdf-page-placeholder');
        placeholders.forEach(function (el) {
          observer.observe(el);
        });
      } else {
        // Fallback: render first page, then subsequent pages sequentially
        renderSequential(pdfDoc, container, 1, numPages, scale);
      }
    })
    .catch(function (err) {
      console.error('PDF.js failed to load document:', err);
      if (statusEl) {
        statusEl.classList.add('d-none');
      }
      if (errorEl) {
        errorEl.classList.remove('d-none');
        const link = errorEl.querySelector('.alert-link');
        if (link && fallbackUrl) {
          link.href = fallbackUrl;
        }
      }
    });
}

/**
 * Create a placeholder div for a PDF page.
 *
 * @param {number} pageNumber
 * @returns {HTMLElement}
 */
function createPagePlaceholder(pageNumber) {
  const div = document.createElement('div');
  div.className = 'pdf-page-placeholder';
  div.dataset.pageNum = String(pageNumber);
  div.style.cssText = 'min-height: 400px; margin: 0 auto 12px; position: relative;';

  const label = document.createElement('div');
  label.className = 'text-center text-muted small py-2';
  label.textContent = 'Página ' + pageNumber;
  div.appendChild(label);

  // Spinner placeholder while loading
  const spinner = document.createElement('div');
  spinner.className = 'text-center py-5';
  spinner.innerHTML = '<div class="spinner-border spinner-border-sm text-secondary" role="status">' +
    '<span class="visually-hidden">Renderizando...</span></div>';
  div.appendChild(spinner);

  return div;
}

/**
 * Calculate the page height as it will be displayed in the current container.
 * PDF.js renders at a high scale for sharpness, but CSS shrinks the canvas to
 * the mobile viewport. Using the raw viewport height here creates blank gaps.
 *
 * @param {Object} viewport
 * @param {HTMLElement} placeholderEl
 * @returns {number}
 */
function calculateDisplayHeight(viewport, placeholderEl) {
  const parent = placeholderEl.parentElement;
  const parentRect = parent ? parent.getBoundingClientRect() : null;
  const availableWidth =
    (parentRect && parentRect.width) ||
    (parent && parent.clientWidth) ||
    placeholderEl.clientWidth ||
    window.innerWidth ||
    viewport.width;

  if (!availableWidth || !viewport.width) {
    return Math.ceil(viewport.height);
  }

  return Math.ceil(viewport.height * (availableWidth / viewport.width));
}

/**
 * Render a single page into the placeholder element.
 *
 * @param {Object} pdfDoc - PDF.js document
 * @param {number} pageNum
 * @param {HTMLElement} placeholderEl
 * @param {number} scale
 */
function renderPage(pdfDoc, pageNum, placeholderEl, scale) {
  pdfDoc.getPage(pageNum).then(function (page) {
    const viewport = page.getViewport({ scale: scale });

    // Reserve only the visual height that the scaled canvas will occupy on screen.
    const displayHeight = calculateDisplayHeight(viewport, placeholderEl);
    placeholderEl.style.minHeight = displayHeight + 'px';

    // Create canvas
    const canvas = document.createElement('canvas');
    canvas.className = 'pdf-page-canvas d-block mx-auto';
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    canvas.style.cssText = 'width: 100%; height: auto; max-width: 100%;';

    const ctx = canvas.getContext('2d');
    const renderTask = page.render({
      canvasContext: ctx,
      viewport: viewport,
    });

    // Remove spinner once rendered
    renderTask.promise
      .then(function () {
        const spinner = placeholderEl.querySelector('.text-center.py-5');
        if (spinner) {
          spinner.remove();
        }
        // Move canvas to top
        const existingCanvas = placeholderEl.querySelector('canvas');
        if (!existingCanvas) {
          placeholderEl.insertBefore(canvas, placeholderEl.firstChild);
        }
        // Let the rendered canvas define the final page height; otherwise the
        // render-scale viewport height can leave large blank gaps on mobile.
        placeholderEl.style.minHeight = '';
      })
      .catch(function (err) {
        console.error('Error rendering page ' + pageNum + ':', err);
        const spinner = placeholderEl.querySelector('.text-center.py-5');
        if (spinner) {
          spinner.innerHTML = '<span class="text-danger">Erro ao renderizar página ' + pageNum + '</span>';
        }
      });
  }).catch(function (err) {
    console.error('Error getting page ' + pageNum + ':', err);
    const spinner = placeholderEl.querySelector('.text-center.py-5');
    if (spinner) {
      spinner.innerHTML = '<span class="text-danger">Erro ao carregar página ' + pageNum + '</span>';
    }
  });
}

/**
 * Sequential fallback renderer for browsers without IntersectionObserver.
 * Renders first page immediately, then processes the rest one by one.
 *
 * @param {Object} pdfDoc
 * @param {HTMLElement} container
 * @param {number} startPage
 * @param {number} endPage
 * @param {number} scale
 */
function renderSequential(pdfDoc, container, startPage, endPage, scale) {
  if (startPage > endPage) return;

  const placeholder = container.querySelector(
    '.pdf-page-placeholder[data-page-num="' + startPage + '"]'
  );

  if (placeholder) {
    renderPage(pdfDoc, startPage, placeholder, scale);
  }

  // Process next page after a small delay
  setTimeout(function () {
    renderSequential(pdfDoc, container, startPage + 1, endPage, scale);
  }, 50);
}
