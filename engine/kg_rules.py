"""kg_rules.py — rule-based (non-LLM) KG triple extraction.

A local, zero-LLM alternative to the LLM extractor in `kg_extract.py`, intended
for wiki / MemPalace memory data (people, projects, organisations, places) where
the content is biographical/relational rather than normative. Selected via
`config.json → mempalace.kg.method = "rules"` (default `"llm"`).

Approach — "generic SVO + NER":
  - spaCy German NER (PER / LOC / ORG) supplies the entity nodes.
  - Within each sentence, entity pairs are joined by a small German + English
    RELATIONAL-CUE lexicon (works_at / located_in / member_of / …). The cue
    word between two entities picks the predicate.
  - A few high-precision regex relations (born/founded dates, "X ist Y").

This emits OPEN, lowercase-English snake_case predicates (the `generic`
profile) — it deliberately does NOT attempt the 12 normative predicates
(requires/forbids/…), which need semantic judgement a rule extractor can't do
well. The output triple shape matches the LLM path exactly:
  {"subject", "predicate", "object", "confidence", "span"}
so the existing `_write_triples_to_kg` / config / chunking all apply unchanged.

Own spaCy pipeline: the GDPR scanner's pipeline (engine.pii_ner) loads with the
parser/tagger DISABLED for speed, so it can't segment sentences. Rather than
mutate that shared, perf-tuned pipeline, this module lazily loads its OWN
pipeline (NER + a cheap rule-based `sentencizer`, still no parser). If spaCy or
the model is unavailable it degrades to a regex sentence split + regex entities
so the path never hard-fails (it just yields fewer/cruder triples).
"""
from __future__ import annotations

import re
import threading

# de_core_news_md — same model the GDPR NER scanner uses; reuse the package
# name so a machine that has one has the other.
_MODEL_ID = "de_core_news_md"
_MAX_CHARS = 50_000  # mirror pii_ner._MAX_SCAN_CHARS — cap per-chunk work

_NLP = None              # cached own-pipeline (NER + sentencizer) or False if unavailable
_NLP_LOCK = threading.Lock()

# spaCy entity label → our coarse node type. Same map as pii_ner._LABEL_MAP.
_LABEL_TYPE = {"PER": "person", "LOC": "place", "ORG": "org"}

# Relational cue lexicon: a surface phrase appearing BETWEEN two entities in a
# sentence picks the predicate. German + English, lowercased, matched on word
# boundaries. Order matters only for display; first cue found in the gap wins.
# Predicates stay lowercase-English snake_case so triples join across languages.
_CUES = [
    ("works_at",   ["arbeitet bei", "arbeitet für", "angestellt bei", "works at",
                     "works for", "employed at", "employed by", "tätig bei"]),
    ("member_of",  ["mitglied von", "mitglied bei", "gehört zu", "member of",
                    "part of", "teil von"]),
    ("founded",    ["gründete", "gegründet von", "founded", "founder of",
                    "gründer von", "gründerin von"]),
    ("leads",      ["leitet", "geführt von", "leiter von", "leiterin von",
                    "leads", "heads", "director of", "ceo von", "ceo of",
                    "chef von", "vorstand von"]),
    ("located_in", ["sitz in", "ansässig in", "located in", "based in",
                    "headquartered in", "standort", "niederlassung in"]),
    ("lives_in",   ["wohnt in", "lebt in", "lives in", "resides in"]),
    ("born_in",    ["geboren in", "born in"]),
    ("works_on",   ["arbeitet an", "works on", "verantwortlich für",
                    "responsible for", "zuständig für"]),
    ("collaborates_with", ["zusammen mit", "kooperiert mit", "collaborates with",
                           "partners with", "gemeinsam mit"]),
    ("knows",      ["kennt", "knows", "bekannt mit"]),
]
# Pre-compile a single alternation per predicate (longest phrase first so
# "arbeitet für" wins over a bare "für" if we ever add one).
_CUE_RES = [
    (pred, re.compile(
        r"(?<!\w)(?:" + "|".join(
            re.escape(c) for c in sorted(phrases, key=len, reverse=True)
        ) + r")(?!\w)", re.IGNORECASE))
    for pred, phrases in _CUES
]

# High-precision standalone regexes (subject from a preceding entity, object
# from the captured group). Kept few — each must be defensible on its own.
_YEAR = r"(1[5-9]\d{2}|20\d{2})"
# Bidirectional: the year may sit before OR after the cue word ("1998 gegründet"
# and "gegründet 1998" both occur). Two patterns per relation.
_DATE_RES = []
for _pred, _cue in [("founded_in", r"gegründet"), ("founded_in", r"founded"),
                    ("born_in", r"geboren"), ("born_in", r"born")]:
    _DATE_RES.append((_pred, re.compile(r"\b" + _cue + r"\b[^.]{0,40}?\b" + _YEAR + r"\b", re.IGNORECASE)))
    _DATE_RES.append((_pred, re.compile(r"\b" + _YEAR + r"\b[^.]{0,40}?\b" + _cue + r"\b", re.IGNORECASE)))

# Crude entity fallback when spaCy is unavailable: Capitalised multi-word runs
# (handles German nouns + proper names). Lower recall + more noise than NER.
_CAP_RUN = re.compile(r"\b([A-ZÄÖÜ][\wäöüß\-]+(?:\s+[A-ZÄÖÜ][\wäöüß\-]+){0,3})\b")
_STOP_CAP = {
    "Der", "Die", "Das", "Ein", "Eine", "Und", "Oder", "Aber", "Im", "In",
    "Am", "An", "Auf", "Für", "Mit", "Von", "Zu", "The", "A", "An", "And",
    "Or", "But", "In", "On", "At", "For", "With", "Of", "To",
}


def _get_nlp():
    """Lazily load this module's own spaCy pipeline (NER + sentencizer, no
    parser). Returns the pipeline or False if spaCy/model is unavailable.
    Cached; thread-safe."""
    global _NLP
    if _NLP is not None:
        return _NLP
    with _NLP_LOCK:
        if _NLP is not None:
            return _NLP
        try:
            import spacy
            nlp = spacy.load(
                _MODEL_ID,
                # Keep NER; drop the heavy components. Add a cheap rule-based
                # sentence splitter so doc.sents works without the parser.
                disable=["parser", "tagger", "lemmatizer", "attribute_ruler"],
            )
            if "senter" not in nlp.pipe_names and "sentencizer" not in nlp.pipe_names:
                nlp.add_pipe("sentencizer")
            _NLP = nlp
        except Exception:
            _NLP = False
    return _NLP


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])")


def _regex_sentences(text: str):
    """Fallback sentence segmentation when spaCy is unavailable."""
    for s in _SENT_SPLIT.split(text):
        s = s.strip()
        if s:
            yield s


def _regex_entities(sent: str):
    """Fallback entity detection: capitalised runs. Returns
    [(text, start, end, type)] with type='entity' (unknown class)."""
    out = []
    for m in _CAP_RUN.finditer(sent):
        val = m.group(1).strip()
        first = val.split()[0]
        if first in _STOP_CAP or len(val) < 3:
            continue
        out.append((val, m.start(1), m.end(1), "entity"))
    return out


def _clip_span(s: str, limit: int = 240) -> str:
    s = " ".join(s.split())
    return s[:limit]


# Predicates whose subject should be a PERSON when one is available in the
# sentence — avoids the "Berlin member_of Greenpeace" mis-attribution where the
# cue sits between a non-person and the object but the real subject is the
# person earlier in the sentence ("Max wohnt in Berlin und ist Mitglied von …").
_PERSON_SUBJECT_PREDS = {
    "works_at", "member_of", "lives_in", "born_in", "leads", "founded",
    "works_on", "knows",
}


def _pair_triples(sent: str, ents: list, seen: set, out: list, max_triples: int):
    """For each adjacent entity pair in `sent`, if a relational cue sits in the
    gap between them, emit a triple. `ents` = [(text,start,end,type)] sorted by
    start. Mutates `out` and `seen`."""
    # First person entity in the sentence (subject candidate for bio predicates).
    first_person = next((e for e in ents if e[3] == "person"), None)
    for i in range(len(ents) - 1):
        if len(out) >= max_triples:
            return
        subj, s_s, s_e, s_type = ents[i]
        obj, o_s, o_e, _ = ents[i + 1]
        gap = sent[s_e:o_s]
        if not gap.strip() or len(gap) > 80:
            continue
        for pred, rx in _CUE_RES:
            if not rx.search(gap):
                continue
            # Re-anchor the subject to the sentence's person for bio predicates
            # when the adjacent subject isn't itself a person.
            this_subj, span_start = subj, s_s
            if (pred in _PERSON_SUBJECT_PREDS and s_type != "person"
                    and first_person and first_person[1] < o_s):
                this_subj, span_start = first_person[0], first_person[1]
            if this_subj == obj:
                break
            key = (this_subj.lower(), pred, obj.lower())
            if key in seen:
                break
            seen.add(key)
            out.append({
                "subject": this_subj, "predicate": pred, "object": obj,
                "confidence": 0.6,
                "span": _clip_span(sent[span_start:o_e]),
            })
            break


def _date_triples(sent: str, ents: list, seen: set, out: list, max_triples: int):
    """born_in / founded_in: attach a year to the first entity in the sentence."""
    if not ents:
        return
    subj = ents[0][0]
    for pred, rx in _DATE_RES:
        if len(out) >= max_triples:
            return
        m = rx.search(sent)
        if not m:
            continue
        year = m.group(1)
        key = (subj.lower(), pred, year)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "subject": subj, "predicate": pred, "object": year,
            "confidence": 0.7, "span": _clip_span(m.group(0)),
        })


def extract_triples_rule_based(
    content: str,
    *,
    max_triples: int = 12,
    min_confidence: float = 0.5,
) -> tuple[list[dict], str | None]:
    """Rule-based replacement for `extract_triples_from_drawer`'s LLM call.

    Returns (triples, error_msg or None) in the SAME shape as the LLM path:
    each triple is {subject, predicate, object, confidence, span}; predicates
    are lowercase-English snake_case; subjects/objects stay in source language.
    Drops triples below `min_confidence`; caps at `max_triples`. Never raises —
    on any internal failure it returns ([], "<reason>") so the caller marks the
    chunk done rather than retry-looping.
    """
    if not content or not content.strip():
        return [], None
    text = content[:_MAX_CHARS]

    out: list[dict] = []
    seen: set = set()
    try:
        nlp = _get_nlp()
        if nlp:
            doc = nlp(text)
            for sent in doc.sents:
                if len(out) >= max_triples:
                    break
                s_text = sent.text
                # Entities in this sentence, offsets relative to the sentence.
                base = sent.start_char
                ents = [
                    (e.text.strip(), e.start_char - base, e.end_char - base,
                     _LABEL_TYPE.get(e.label_, ""))
                    for e in sent.ents
                    if e.label_ in _LABEL_TYPE and len(e.text.strip()) >= 3
                ]
                ents.sort(key=lambda t: t[1])
                _pair_triples(s_text, ents, seen, out, max_triples)
                _date_triples(s_text, ents, seen, out, max_triples)
        else:
            # spaCy unavailable — regex fallback.
            for s_text in _regex_sentences(text):
                if len(out) >= max_triples:
                    break
                ents = _regex_entities(s_text)
                _pair_triples(s_text, ents, seen, out, max_triples)
                _date_triples(s_text, ents, seen, out, max_triples)
    except Exception as e:
        return [], f"rule_error: {type(e).__name__}: {e}"

    # Confidence filter (rule confidences are fixed per relation kind; this lets
    # a high min_confidence config still suppress the weaker pair relations).
    triples = [t for t in out if t["confidence"] >= min_confidence][:max_triples]
    return triples, None
