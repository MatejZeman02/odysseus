// Accessibility enhancements for keyboard + screen-reader users.
//
// Several primary controls in Odysseus are authored as click-only <div>s
// (most notably the whole sidebar navigation: New Chat, Search, Brain,
// Calendar, Compare, Cookbook, Deep Research, Gallery, Library, Notes,
// Tasks, Theme, plus the account row). <div>s are not in the tab order and
// are not announced as buttons, so keyboard and screen-reader users cannot
// reach or operate them.
//
// This module enhances those rows in place — making them focusable
// (tabindex=0), announcing them as buttons when it's safe to do so, and
// activating them with Enter / Space — without changing how they look or
// how they behave for mouse users. The visible focus ring already exists in
// style.css (`.list-item:focus-visible`); it simply never fired because the
// rows were never focusable.

(function () {
  'use strict';

  // Click-as-button rows we want reachable by keyboard.
  var ROW_SELECTOR = ['#sidebar .list-item', '#user-bar-profile'].join(',');

  // Native interactive descendants. If a row contains one of these we must
  // NOT give the row role="button" — a button inside a button is invalid
  // (axe "nested-interactive") and confuses screen readers. Such rows still
  // become focusable + Enter/Space-activatable, just without the role.
  var NESTED_INTERACTIVE =
    'a[href],button,input,select,textarea,[contenteditable="true"],[tabindex]:not([tabindex="-1"])';

  function enhanceRow(el) {
    if (!el || el.nodeType !== 1 || el.dataset.a11yEnhanced === '1') return;
    var tag = el.tagName;
    // Leave genuine native controls alone.
    if (tag === 'BUTTON' || tag === 'A' || tag === 'INPUT' ||
        tag === 'SELECT' || tag === 'TEXTAREA') return;

    el.dataset.a11yEnhanced = '1';
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    el.setAttribute('data-a11y-activatable', '1');

    if (!el.querySelector(NESTED_INTERACTIVE) && !el.hasAttribute('role')) {
      el.setAttribute('role', 'button');
    }

    // Guarantee an accessible name. Visible text normally supplies it; fall
    // back to the title attribute for icon-only rows.
    if (!el.getAttribute('aria-label') &&
        !(el.textContent || '').trim() &&
        el.getAttribute('title')) {
      el.setAttribute('aria-label', el.getAttribute('title'));
    }
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll(ROW_SELECTOR).forEach(enhanceRow);
  }

  // ---- Modal dialogs -----------------------------------------------------
  // Odysseus modals are plain <div class="modal-content"> boxes. Marking
  // them as ARIA dialogs lets screen readers announce them as dialogs and
  // exempts their content from the "all content in landmarks" rule. We also
  // normalize the modal title to heading level 2 (one below the page <h1>)
  // so heading order stays valid no matter which tag the markup uses.
  var titleSeq = 0;
  // Each modal "kind" is a container selector plus where to find its title
  // heading. Standard modals use .modal-content/.modal-header; the docked
  // Notes pane uses its own markup.
  var MODAL_KINDS = [
    {
      sel: '.modal-content',
      heading: '.modal-header h1, .modal-header h2, .modal-header h3, ' +
               '.modal-header h4, .modal-header h5, .modal-header h6'
    },
    { sel: '.notes-pane', heading: '.notes-pane-title' }
  ];
  var MODAL_SEL = MODAL_KINDS.map(function (k) { return k.sel; }).join(',');

  function enhanceModal(mc, headingSel) {
    if (!mc || mc.nodeType !== 1 || mc.dataset.a11yDialog === '1') return;
    mc.dataset.a11yDialog = '1';
    if (!mc.hasAttribute('role')) mc.setAttribute('role', 'dialog');
    if (!mc.hasAttribute('aria-modal')) mc.setAttribute('aria-modal', 'true');

    var heading = headingSel && mc.querySelector(headingSel);
    if (heading) {
      if (!heading.id) heading.id = 'a11y-modal-title-' + (++titleSeq);
      if (!mc.hasAttribute('aria-labelledby')) {
        mc.setAttribute('aria-labelledby', heading.id);
      }
      // Modal titles sit one level below the page <h1>; normalize so heading
      // order stays valid regardless of the tag the markup happens to use.
      if (!heading.hasAttribute('aria-level')) heading.setAttribute('aria-level', '2');
    }
  }

  function enhanceModals(root) {
    var scope = root || document;
    MODAL_KINDS.forEach(function (k) {
      scope.querySelectorAll(k.sel).forEach(function (mc) { enhanceModal(mc, k.heading); });
    });
  }

  // ---- Form identity and labels -----------------------------------------
  // Older settings markup uses <label> as a visual two-column heading even
  // when there is no form control to label, and several generated switches
  // have neither id nor name. Chromium reports both patterns in DevTools and
  // may make poor autofill choices. Normalize them without changing layout.
  var formFieldSeq = 0;
  var FORM_FIELD = 'input,select,textarea';

  function fieldLabelText(field) {
    var text = field.getAttribute('aria-label') || field.getAttribute('title') ||
      field.getAttribute('placeholder') || field.dataset.uiKey || field.dataset.privacyKey ||
      field.id || field.name || field.type || 'Form field';
    return String(text).replace(/^set[-_]/, '').replace(/[-_]+/g, ' ').trim() || 'Form field';
  }

  function ensureFieldIdentity(field) {
    if (!field || field.nodeType !== 1) return;
    if (!field.id && !field.name) {
      var base = field.dataset.uiKey || field.dataset.privacyKey || 'field';
      var candidate = 'a11y-' + String(base).replace(/[^a-zA-Z0-9_-]+/g, '-');
      while (document.getElementById(candidate)) candidate = 'a11y-field-' + (++formFieldSeq);
      field.id = candidate;
      field.name = candidate;
    } else if (!field.id) {
      var fromName = String(field.name).replace(/[^a-zA-Z0-9_-]+/g, '-');
      var id = fromName || 'a11y-field-' + (++formFieldSeq);
      while (document.getElementById(id)) id = 'a11y-field-' + (++formFieldSeq);
      field.id = id;
    } else if (!field.name) {
      field.name = field.id;
    }
  }

  function directLabelControl(label) {
    var parent = label.parentElement;
    if (!parent) return null;
    var fields = Array.prototype.filter.call(parent.querySelectorAll(FORM_FIELD), function (field) {
      return !field.closest('label') && !field.disabled && field.type !== 'hidden';
    });
    return fields.length ? fields[0] : null;
  }

  function replaceDecorativeLabel(label) {
    var span = document.createElement('span');
    Array.prototype.forEach.call(label.attributes, function (attr) {
      if (attr.name !== 'for') span.setAttribute(attr.name, attr.value);
    });
    span.innerHTML = label.innerHTML;
    label.replaceWith(span);
  }

  function enhanceFormSemantics(root) {
    var scope = root || document;
    var fields = [];
    if (scope.matches && scope.matches(FORM_FIELD)) fields.push(scope);
    if (scope.querySelectorAll) fields = fields.concat(Array.from(scope.querySelectorAll(FORM_FIELD)));
    fields.forEach(ensureFieldIdentity);

    var labels = [];
    if (scope.matches && scope.matches('label')) labels.push(scope);
    if (scope.querySelectorAll) labels = labels.concat(Array.from(scope.querySelectorAll('label')));
    labels.forEach(function (label) {
      if (label.querySelector(FORM_FIELD)) return;
      var targetId = label.getAttribute('for');
      if (targetId && document.querySelectorAll('#' + CSS.escape(targetId)).length === 1) return;
      var control = directLabelControl(label);
      if (!control) {
        replaceDecorativeLabel(label);
        return;
      }
      ensureFieldIdentity(control);
      label.htmlFor = control.id;
    });

    fields.forEach(function (field) {
      if (field.disabled || field.getAttribute('aria-hidden') === 'true') return;
      var labelled = !!field.closest('label') || !!field.getAttribute('aria-label') ||
        !!field.getAttribute('aria-labelledby') ||
        !!(field.id && document.querySelector('label[for="' + CSS.escape(field.id) + '"]'));
      if (!labelled) field.setAttribute('aria-label', fieldLabelText(field));
    });
  }

  function headingSelFor(el) {
    for (var i = 0; i < MODAL_KINDS.length; i++) {
      if (el.matches(MODAL_KINDS[i].sel)) return MODAL_KINDS[i].heading;
    }
    return null;
  }

  // Delegated keyboard activation. We only act when the focused element is
  // itself an enhanced row (keydown targets the focused element), so a press
  // on a nested native button is left to the browser's own handling.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    var el = e.target;
    if (!el || !el.matches || !el.matches('[data-a11y-activatable]')) return;
    e.preventDefault(); // Space would otherwise scroll the page
    el.click();
  });

  function init() {
    enhanceAll(document);
    enhanceModals(document);
    enhanceFormSemantics(document);

    // Sidebar content is re-rendered as the user navigates (session lists,
    // tool sub-rows, etc.). Watch for new rows and enhance them too.
    var sidebar = document.getElementById('sidebar');
    if (sidebar && 'MutationObserver' in window) {
      new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var added = muts[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var n = added[j];
            if (n.nodeType !== 1) continue;
            if (n.matches && n.matches(ROW_SELECTOR)) enhanceRow(n);
            if (n.querySelectorAll) enhanceAll(n);
          }
        }
      }).observe(sidebar, { childList: true, subtree: true });
    }

    // Some modals (Notes, Tasks, …) are injected at runtime, usually as
    // direct children of <body>. Catch those without paying for a deep
    // subtree observer over the whole document.
    if ('MutationObserver' in window) {
      new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var added = muts[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var n = added[j];
            if (n.nodeType !== 1) continue;
            if (n.matches && n.matches(MODAL_SEL)) enhanceModal(n, headingSelFor(n));
            if (n.querySelector && n.querySelector(MODAL_SEL)) enhanceModals(n);
            enhanceFormSemantics(n);
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
