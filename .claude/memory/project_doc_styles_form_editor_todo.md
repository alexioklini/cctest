---
name: project_doc_styles_form_editor_todo
description: "DONE (v9.150.0, 2026-06-16) — Dokument-Stile GUI is now a WYSIWYG/form editor (color pickers, font dropdowns, number inputs, live preview); replaced the raw YAML textarea. Storage stays YAML."
metadata: 
  node_type: memory
  type: project
  originSessionId: 99a1fa24-8a53-401a-80cc-9bc845d6506f
---

2026-06-16: shipped the Dokument-Stile feature (v9.148-9.149.1) — editable
per-format style presets (fonts/colors/layout) that write_document +
render_diagram apply deterministically. Storage = YAML files in
agents/main/skills/doc-styles/<name>.yaml.

**DONE in v9.150.0** (the previously-deferred WYSIWYG editor): the editor is now
structured form fields (settings_general_tabs.js: `_docStyleRenderEditor` +
`_DOC_STYLE_FIELDS` spec mirroring `_DEFAULT_DOC_STYLE`):
- colors = `<input type=color>` + a hex-text twin (either updates the other)
- fonts = `<input list=ds-fontlist>` datalist dropdown
- sizes/margin = number inputs; page_size + mermaid theme = `<select>`;
  heading_bold = checkbox
- sticky **live preview** renders a heading/body/table sample in the chosen
  fonts+colors (true WYSIWYG) + a collapsible "YAML anzeigen" mirror
- `_docStyleCollect()` reads fields back into a nested object keyed by dotted
  paths; `_docStyleToYaml()` is a small fixed-schema YAML emitter (storage stays
  YAML — what `_load_doc_style` in file_tools.py reads). Parse-roundtrip verified
  vs pyyaml: hex/colon strings quoted, numbers/bools bare.

Backend (handlers/admin_observability.py): `GET /v1/doc-styles?name=X` now also
returns `parsed` (`_load_doc_style(name)` — preset deep-merged over defaults,
full shape) and the list returns structured `defaults`; the form reads those.
Old raw `yaml` field kept. Verified live (admin/admin Playwright): form pre-fills
from corporate preset, color/font/size edits flow into preview + YAML, new-preset
save round-trips through the API.
