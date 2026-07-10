# Output Presets — grounded one-click outputs over a project's sources.
#
# Each preset = a canned grounded prompt + an output title template. The
# generation pipeline (handlers/projects.py:_handle_project_generate) retrieves
# project sources via tool_mempalace_query (project-scoped), then runs ONE
# background_call(purpose="transform", project=<name>) with the preset prompt
# plus the research-mode citation discipline so the output is grounded + cited.
#
# Prompts live here (code module) for v1 — admin-tunable storage (config.json
# section) is a deferred open-item (OUTPUT_PRESETS_DETAILED_SPEC §8.1). Keep the
# prompt text plain so a later move to config is a copy, not a rewrite.

# length option → concrete section/word guidance baked into the prompt.
_LENGTH_GUIDANCE = {
    "short": "Keep it concise — the essentials only, roughly 1–2 sections or ~300 words.",
    "std": "Aim for a thorough but readable length — roughly 3–6 sections or ~800 words.",
    "long": "Be comprehensive — cover every distinct point in the sources, ~1500+ words.",
}

# Shared discipline appended to every preset prompt. Mirrors research-mode
# Topic B (REFUSAL/PRECISION/CITATION) so outputs are grounded and cite their
# sources verbatim, and never invent content absent from the retrieved sources.
_GROUNDING_DISCIPLINE = (
    "GROUNDING RULES (mandatory):\n"
    "- Use ONLY the information in the retrieved sources below. Do not add outside "
    "knowledge, and do not invent facts, numbers, names, or dates.\n"
    "- Cite every non-trivial claim verbatim in the form "
    "[Quelle: <source_file> — \"<exact quoted snippet>\"]. The quote must appear "
    "verbatim in the sources.\n"
    "- If the sources do not cover something the section calls for, say so plainly "
    "rather than filling the gap. An honest \"not covered in the sources\" is correct; "
    "a fabricated answer is a failure.\n"
    "- Write in the language of the sources."
)


# kind → {icon, label, title_prefix, instructions}.
# `instructions` is the body of the user-turn prompt; the pipeline prepends the
# focus/length options and appends the grounding discipline + retrieved sources.
PRESETS = {
    "study_guide": {
        "icon": "📖",
        "label": "Study Guide",
        "title_prefix": "Study Guide",
        "instructions": (
            "Produce a STUDY GUIDE from the project's sources. Structure it as:\n"
            "## Key Concepts — the central ideas, each explained in 1–3 sentences.\n"
            "## Key Terms & Definitions — a glossary of the important terms, each "
            "defined from the sources.\n"
            "## Review Questions — 5–10 questions a reader should be able to answer "
            "after studying the sources, with brief grounded answers.\n"
            "Every concept, definition, and answer must be cited to the sources."
        ),
    },
    "briefing": {
        "icon": "📋",
        "label": "Briefing Doc",
        "title_prefix": "Briefing",
        "instructions": (
            "Produce a BRIEFING DOCUMENT from the project's sources. Structure it as:\n"
            "## Executive Summary — 2–4 sentences capturing the essence.\n"
            "## Key Points — the most important findings/statements, as a cited list.\n"
            "## Implications — what these points mean / what follows from them, "
            "grounded in the sources.\n"
            "Keep it factual and decision-useful; cite throughout."
        ),
    },
    "faq": {
        "icon": "❓",
        "label": "FAQ",
        "title_prefix": "FAQ",
        "instructions": (
            "Produce a FREQUENTLY ASKED QUESTIONS document from the project's "
            "sources. Write grounded question/answer pairs covering what a reader "
            "would most want to know. Each answer must be drawn from and cited to "
            "the sources. Format each as a '### <question>' heading followed by the "
            "answer. Only include questions the sources actually answer."
        ),
    },
    "timeline": {
        "icon": "🕒",
        "label": "Timeline",
        "title_prefix": "Timeline",
        "instructions": (
            "Produce a CHRONOLOGICAL TIMELINE of the dated events described in the "
            "project's sources. List each event as '- **<date>** — <what happened> "
            "[Quelle: ...]', ordered earliest to latest. Use only dates and events "
            "explicitly stated in the sources.\n"
            "IMPORTANT: if the sources contain no datable events, OMIT — do NOT "
            "invent dates. In that case output a single line stating that no datable "
            "events were found in the sources."
        ),
    },
}


# ── Custom presets (v9.302.0) ───────────────────────────────────────────────
# User-defined presets ("Transformations") live in config.json → studio_presets
# (boot-copied into server_config by server.py main(), live-mirrored by the CRUD
# handlers in handlers/projects.py). Each entry:
#   {id, label, title_prefix?, instructions, per_source?, owner_user_id, created_at}
# Their generation kind is "custom:<id>". per_source=True runs the preset once
# PER project source (one wiki page per document) instead of over the corpus.
CUSTOM_KIND_PREFIX = "custom:"


def custom_presets() -> list:
    """The raw custom-preset list from the live server config ([] outside a server).

    Read via brain._server_config() so a Service-Modelle-style live mirror (not a
    stale boot copy) is honoured — same pattern as the studio_model knob."""
    import brain as _brain
    try:
        lst = _brain._server_config().get("studio_presets") or []
    except Exception:
        return []
    return [p for p in lst
            if isinstance(p, dict) and p.get("id") and (p.get("instructions") or "").strip()]


def resolve_preset(kind: str) -> dict | None:
    """kind → normalised preset dict {icon,label,title_prefix,instructions,
    per_source,custom[,id]} — or None for an unknown kind. Builtins never set
    per_source; custom kinds are 'custom:<id>'."""
    if kind in PRESETS:
        return {**PRESETS[kind], "per_source": False, "custom": False}
    if isinstance(kind, str) and kind.startswith(CUSTOM_KIND_PREFIX):
        cid = kind[len(CUSTOM_KIND_PREFIX):]
        for p in custom_presets():
            if p.get("id") == cid:
                label = str(p.get("label") or "Eigene Vorlage")
                return {
                    "icon": "custom",
                    "label": label,
                    "title_prefix": str(p.get("title_prefix") or "").strip() or label,
                    "instructions": str(p.get("instructions") or ""),
                    "per_source": bool(p.get("per_source")),
                    "custom": True,
                    "id": cid,
                }
    return None


def is_valid_kind(kind: str) -> bool:
    return resolve_preset(kind) is not None


def build_prompt(kind: str, sources_text: str, *, focus: str = "", length: str = "std",
                 source_label: str = "RETRIEVED PROJECT SOURCES") -> str:
    """Assemble the full user-turn prompt for a preset generation.

    `sources_text` is the pre-retrieved, source-tagged corpus the generator
    must ground on (the pipeline gathers it via tool_mempalace_query so the
    one-shot transform call needs no further retrieval round). Per-source runs
    pass a single document + source_label="SOURCE DOCUMENT"."""
    preset = resolve_preset(kind)
    if preset is None:
        raise KeyError(f"unknown preset kind: {kind}")
    length_guidance = _LENGTH_GUIDANCE.get(length, _LENGTH_GUIDANCE["std"])
    parts = [preset["instructions"], "", length_guidance]
    if (focus or "").strip():
        parts.append("")
        parts.append(f"FOCUS: emphasise the following within the above — {focus.strip()}")
    parts.append("")
    parts.append(_GROUNDING_DISCIPLINE)
    parts.append("")
    parts.append(f"=== {source_label} ===")
    parts.append(sources_text)
    return "\n".join(parts)
