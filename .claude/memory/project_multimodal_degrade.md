---
name: project_multimodal_degrade
description: "v9.291.0 — Bild-Anhang + nicht-multimodales Endmodell: universeller Wire-Degrade (Vision-Beschreibung) statt API-Error; _sanitize_multimodal_for_model am Choke-Point vor run_turn"
metadata:
  node_type: memory
  type: project
  originSessionId: 01778bad-d744-4aff-8af1-720179c3af78
---
Problem (User, generell nicht nur MoA): Bild-Anhang + das TATSÄCHLICH laufende Modell ist nicht multimodal → Provider-API-Error (400). Root Cause: image_url-vs-Disk-Routing (handlers/chat.py ~6451, gegen session.model beim SENDEN) wird EINMAL entschieden, nie gegen das finale Modell neu bewertet. Modell kann sich danach ändern: MoA-Executor-Swap (~4179), expliziter User/Reviewer-Override, Quota-/GDPR-Local-Swap, Auto-Route. require_mimes (9.289.2) schützte nur den AUTO-Pick (Pool schrumpfen) und ließ expliziten Override eines Text-only-Modells STILL fallen (`_ex in candidates`-Guard).

FIX v9.291.0 (User: 'Vision-Beschreibung injizieren' + 'universell am Wire-Choke-Point'):
- NEU `_sanitize_multimodal_for_model(messages, model)` (handlers/chat.py, vor _MOA_REF_SYSTEM) läuft am EINZIGEN Choke-Point: direkt vor sidecar_proxy.run_turn (nach ALLEN Swaps, session.model final). No-op (gleiche Objektidentität, byte-identischer Wire, Warm-Prefix unberührt) wenn model_supports_mimes(model, präsente Bild-MIMEs) True. Sonst: jeder image_url-Block → _describe_image_with_vision(b64,mime,name) (brain.py:2966, nutzt ctx.attachment_image_model = live mistral-medium-3.5) → Textbeschreibung ersetzt den Block (an Text-Block angehängt, sonst neuer; kollabiert zu String wenn nur noch Text). GILT für ALLE user-Messages im Wire (auch History-Bilder früherer Turns). Transient shallow-copy — Original-image_url bleibt in session.messages/DB → späteres fähiges Modell sieht echtes Bild.
- WICHTIG: read_document auf ein Bild liefert NUR Metadaten (file_tools.py:420, parse_image), NIE visuellen Inhalt → deshalb Vision-Beschreibung, nicht Disk-read, für Bilder.
- _run_plan_review_loop honoriert jetzt Override AUSSERHALB des MIME-Pools, wenn NICHT-MIME-Gates ok (enabled + 'chat' in caps + != planner + local-policy + ACL) → meta.executor_mime_degraded. Vorher still verworfen.

raw_formats-Fakten (config.json): deepseek-v4-flash/pro = [] (KEIN Bild), glm-5.2 = image+audio, kimi-k2.6 = image/*. E2E verifiziert: kimi-Session mit Bild (image_url, Antwort 'Rot') → Folge-Turn erzwungen auf deepseek-v4-flash → 'Das vorherige Bild war ein rotes Quadrat — rot' (deepseek bekam die Vision-Beschreibung, kein API-Error, History behielt image_url). Regression: kimi+Bild bleibt nativ (kein read_document). Text-only-Modell von Anfang an → Bild wird ohnehin disk-geroutet (send-time gegen session.model). GOTCHA Test: body.model setzt session.model VOR dem Routing → um den Bug zu reproduzieren muss der Swap NACH dem Routing passieren (History-Bild + Modellwechsel, oder MoA/Quota). Siehe [[project_moa_virtual_model]], [[project_moa_plan_delegation]].
