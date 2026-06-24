"""Detector #1: Presidio (DE pack + NER).

Presidio AnalyzerEngine with a German NLP engine. By default we use spaCy
de_core_news_lg as Presidio's NER, plus all predefined recognizers filtered to
de/en. Optionally (env PII_PRESIDIO_NER=gliner) we replace the NER layer with
GLiNER spans wired in as an EntityRecognizer, to test "Presidio + GLiNER".

Note on languages: Presidio recognizers are language-tagged. We register the
analyzer for "de" and rely on the predefined recognizers that support it plus
the global ones (email, IBAN, credit card, IP, crypto, phone).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from common import Finding, PRESIDIO_MAP  # noqa: E402

_ANALYZER = None
_NER_MODE = os.environ.get("PII_PRESIDIO_NER", "spacy")  # "spacy" | "gliner"
_SPACY_MODEL = os.environ.get("PII_PRESIDIO_SPACY", "de_core_news_lg")


def available() -> tuple[bool, str]:
    try:
        import presidio_analyzer  # noqa: F401
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _build_analyzer():
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    nlp_conf = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "de", "model_name": _SPACY_MODEL}],
    }
    provider = NlpEngineProvider(nlp_configuration=nlp_conf)
    nlp_engine = provider.create_engine()

    registry = RecognizerRegistry(supported_languages=["de"])
    registry.load_predefined_recognizers(languages=["de"], nlp_engine=nlp_engine)

    analyzer = AnalyzerEngine(
        nlp_engine=nlp_engine,
        registry=registry,
        supported_languages=["de"],
    )

    if _NER_MODE == "gliner":
        # Replace spaCy-derived PERSON/LOC/ORG with GLiNER spans by registering
        # a custom recognizer that defers to our gliner_adapter.
        _register_gliner(analyzer)
    return analyzer


def _register_gliner(analyzer):
    from presidio_analyzer import EntityRecognizer, RecognizerResult
    import gliner_adapter

    CANON_TO_PRESIDIO = {"name": "PERSON", "address": "LOCATION", "organisation": "ORGANIZATION"}

    class GlinerRecognizer(EntityRecognizer):
        def load(self):
            return None

        def analyze(self, text, entities, nlp_artifacts=None):
            results = []
            for f in gliner_adapter.detect_ner_only(text):
                ent = CANON_TO_PRESIDIO.get(f.type)
                if not ent:
                    continue
                idx = text.find(f.value)
                if idx < 0:
                    continue
                results.append(RecognizerResult(
                    entity_type=ent, start=idx, end=idx + len(f.value), score=0.85))
            return results

    # Drop spaCy's NER-backed recognizers so GLiNER owns those entities.
    analyzer.registry.remove_recognizer("SpacyRecognizer")
    analyzer.registry.add_recognizer(GlinerRecognizer(
        supported_entities=["PERSON", "LOCATION", "ORGANIZATION"],
        supported_language="de", name="GlinerRecognizer"))


def _load():
    global _ANALYZER
    if _ANALYZER is None:
        _ANALYZER = _build_analyzer()
    return _ANALYZER


def detect(text: str) -> list[Finding]:
    analyzer = _load()
    out: list[Finding] = []
    results = analyzer.analyze(text=text, language="de")
    for r in results:
        canon = PRESIDIO_MAP.get(r.entity_type, "other")
        val = text[r.start:r.end].strip()
        if val:
            out.append(Finding(value=val, type=canon))
    return out
