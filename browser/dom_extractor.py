"""
DOM extractor for browser automation.

Injects a JS snippet into the live page that:
1. Walks the DOM for interactable elements (links, buttons, inputs, ARIA roles).
2. Filters to visible, in-viewport-ish elements.
3. Tags each surviving element with a stable attribute (data-agent-id="N").
4. Returns a metadata list the agent can reason over.

Resolution at action time is done via the tag attribute
(`[data-agent-id="N"]`), which survives until the next navigation / re-render.
Because the agent re-snapshots every step, stale tags are never reused.
"""

from typing import Any

# Attribute used to tag interactable elements so actions can resolve them.
AGENT_ID_ATTR = "data-agent-id"

# JavaScript executed via page.evaluate(). Returns a list of dicts, one per
# interactable element, and tags each element with data-agent-id.
EXTRACT_INTERACTABLES_JS = r"""
() => {
  const INTERACTABLE_SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[role=checkbox]', '[role=radio]', '[role=switch]', '[role=combobox]',
    '[role=option]', '[role=searchbox]', '[role=textbox]',
    '[onclick]', '[tabindex]:not([tabindex="-1"])', '[contenteditable="true"]'
  ].join(',');

  const ATTR = 'data-agent-id';

  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') {
      return false;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) return false;
    // Must intersect the viewport (allow some scroll slack).
    if (rect.bottom < 0 || rect.top > (window.innerHeight || 0) + rect.height) return false;
    return true;
  };

  // Strip a URL/path down to a semantic-ish token: "/assets/icon-search.svg?v=2" -> "icon-search".
  const basename = (src) => {
    if (!src) return '';
    try {
      let s = src.split(/[?#]/)[0];
      s = s.substring(s.lastIndexOf('/') + 1);
      s = s.replace(/\.(svg|png|jpe?g|gif|webp|ico)$/i, '');
      s = s.replace(/[-_]/g, ' ').trim();
      return s;
    } catch (e) { return ''; }
  };

  // Pick class tokens that look semantic (icon-search, fa-trash, btn-edit), drop layout noise.
  const iconClass = (el) => {
    if (!el.classList || !el.classList.length) return '';
    const tokens = Array.from(el.classList).filter(c =>
      /(icon|fa|glyphicon|material-icons|ico|btn)[-_]?[a-z]/i.test(c) &&
      !/^(btn|icon|fa|ico)$/i.test(c)
    );
    if (!tokens.length) return '';
    return tokens.map(t => t.replace(/^(icon|fa|fas|far|fab|glyphicon|ico|btn)[-_]/i, '').replace(/[-_]/g, ' ').trim())
                 .filter(Boolean)
                 .join(' ');
  };

  const accessibleName = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim();
    // aria-labelledby -> resolve referenced element text.
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const ref = document.getElementById(labelledby.split(/\s+/)[0]);
      const refText = ref && (ref.innerText || ref.textContent || '').replace(/\s+/g, ' ').trim();
      if (refText) return refText.slice(0, 120);
    }
    const title = el.getAttribute('title');
    if (title && title.trim()) return title.trim();
    const ph = el.getAttribute('placeholder');
    if (ph && ph.trim()) return ph.trim();
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (text) return text.slice(0, 120);
    const val = el.getAttribute('value');
    if (val && val.trim()) return val.trim();

    // Icon-only buttons: derive a label from the image/icon itself.
    // Self <img> first, then a descendant <img>.
    const img = el.tagName === 'IMG' ? el : (el.querySelector ? el.querySelector('img') : null);
    if (img) {
      const ialt = (img.getAttribute('alt') || '').trim();
      if (ialt) return ialt;
      const ititle = (img.getAttribute('title') || '').trim();
      if (ititle) return ititle;
      const fromSrc = basename(img.getAttribute('src'));
      if (fromSrc) return fromSrc;
    }
    // <svg><use href="#icon-search"> or <use xlink:href=...>.
    const use = el.querySelector ? el.querySelector('use') : null;
    if (use) {
      const href = use.getAttribute('href') || use.getAttribute('xlink:href') || '';
      const fromUse = basename(href.replace(/^#/, ''));
      if (fromUse) return fromUse;
    }
    // Font-icon / utility classes (icon-search, fa-trash).
    const fromClass = iconClass(el);
    if (fromClass) return fromClass;
    // Stable hooks as last resort so identical icons still differ.
    const testid = el.getAttribute('data-testid');
    if (testid && testid.trim()) return testid.trim();
    const nameAttr = el.getAttribute('name');
    if (nameAttr && nameAttr.trim()) return nameAttr.trim();
    return '';
  };

  const cssPath = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let sel = node.tagName.toLowerCase();
      if (node.classList && node.classList.length) {
        sel += '.' + Array.from(node.classList).slice(0, 2).map(c => CSS.escape(c)).join('.');
      }
      const parent = node.parentNode;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sibs.length > 1) sel += `:nth-of-type(${sibs.indexOf(node) + 1})`;
      }
      parts.unshift(sel);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };

  const nodes = Array.from(document.querySelectorAll(INTERACTABLE_SELECTOR));
  const results = [];
  let idx = 0;
  for (const el of nodes) {
    if (!isVisible(el)) {
      el.removeAttribute(ATTR);
      continue;
    }
    el.setAttribute(ATTR, String(idx));
    const rect = el.getBoundingClientRect();
    results.push({
      agent_id: idx,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      name: accessibleName(el),
      css: cssPath(el),
      id: el.id || '',
      testid: el.getAttribute('data-testid') || '',
      bbox: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
    });
    idx += 1;
  }
  return results;
}
"""


def format_elements_for_llm(elements: list[dict[str, Any]], max_elements: int = 120) -> str:
    """
    Render the extracted element list into a compact, LLM-friendly text block.

    Example line:
        [12] button "提交"
        [13] input(text) placeholder/name="搜索"

    Args:
        elements: Output of the extraction JS (list of element dicts).
        max_elements: Cap to keep the prompt bounded.

    Returns:
        Newline-joined element listing.
    """
    lines: list[str] = []
    for el in elements[:max_elements]:
        tag = el.get("tag", "?")
        etype = el.get("type", "")
        tag_label = f"{tag}({etype})" if etype else tag
        name = el.get("name", "") or "<no-name>"
        role = el.get("role", "")
        role_label = f" role={role}" if role else ""
        lines.append(f'[{el.get("agent_id")}] {tag_label}{role_label} "{name}"')
    if len(elements) > max_elements:
        lines.append(f"... ({len(elements) - max_elements} more elements hidden)")
    return "\n".join(lines)
