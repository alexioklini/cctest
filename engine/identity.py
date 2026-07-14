"""engine/identity.py — Namens-Normalisierung + Identitäts-Matching.

GEMEINSAMES Modul für L1 (doc_checks.identity_consistency) und die spätere
Entitäts-Schicht L2 (PII_ANALYSIS_PARITY_HANDOVER.md) — die Normalisierungs-
logik lebt von Anfang an hier, damit L2 sie WIEDERVERWENDET statt dupliziert.

Deckt die Oberflächenformen ab, in denen dieselbe Person in einem KYC-Dossier
auftaucht (die 8 Formen aus dem Failure-Katalog F1):
  `STARK, BONNIE MARIE` · `Bonnie M Stark` · `B. Stark` ·
  `STARK<<BONNIE<MARIE` (MRZ) · `STARK_Bonnie_M_Mrs._107625` (Dateiname) ·
  OCR-Garble (`Bonnie MASE`, `Bonnie N. Stark`) via konservativem Fuzzy-Match.

Pure stdlib (difflib) — kein Modell, kein Netz, kein brain-Import.
"""

from __future__ import annotations

import difflib
import re

# Anreden/Titel, die in Aktennamen und Dateinamen kleben ("Mrs.", "Dr.").
_TITLES = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "herr", "frau", "hr", "fr",
    "mag", "dipl", "ing", "med", "phd", "jr", "sr",
}

# Konservative Fuzzy-Schwelle für OCR-Garble (Levenshtein-artig via difflib).
# Bewusst hoch — ein False-Merge zweier ECHT verschiedener Personen wäre in
# einer Betrugsprüfung schlimmer als ein Miss. Am echten Material kalibrieren
# (Handover §7: "konservativ starten").
FUZZY_THRESHOLD = 0.84


def parse_mrz_name(field: str) -> tuple[str, str]:
    """MRZ-Namensfeld `NACHNAME<<VORNAME<MITTELNAME` → (surname, givens).
    Füllzeichen-tolerant (auch gekappte `<`-Läufe aus collapse_ocr_filler)."""
    field = (field or "").strip().strip("<")
    if "<<" in field:
        sur, _, giv = field.partition("<<")
    else:
        sur, giv = field, ""
    return (sur.replace("<", " ").strip(),
            giv.replace("<", " ").strip())


def name_tokens(s: str) -> list[str]:
    """Zerlegt eine beliebige Namens-Oberflächenform in normalisierte Tokens:
    lowercase, Titel raus, Trennzeichen (Komma/Punkt/Unterstrich/`<`) → Space,
    reine Ziffern-Tokens (Kundennummern in Dateinamen) raus."""
    if not s:
        return []
    s = s.lower()
    s = re.sub(r"[<_,;/\\|]+", " ", s)
    s = re.sub(r"\.\s*", ". ", s)  # "B.Stark" → "B. Stark"
    toks = []
    for t in re.split(r"\s+", s):
        t = t.strip(".- '\"")
        if not t or t.isdigit():
            continue
        if t in _TITLES:
            continue
        toks.append(t)
    return toks


def normalize_name(s: str) -> str:
    """Kanonische Vergleichsform: Tokens sortiert + gejoint (macht
    Reihenfolge-Varianten `Stark, Bonnie` ≡ `Bonnie Stark` gleich)."""
    return " ".join(sorted(name_tokens(s)))


def _tokens_match(a: list[str], b: list[str], *, fuzzy: bool) -> bool:
    """Kern-Matcher: jedes Token der KÜRZEREN Liste muss ein Token der
    längeren matchen — voll, als Initiale (`b` ≙ `bonnie`), oder (opt-in)
    fuzzy. Ein Initialen-Match allein trägt NICHT: die VOLL-Matches müssen
    die Nicht-Initialen-Tokens der kürzeren Form abdecken (Kappe 2) — sonst
    matcht `Max Stark` über die Einzel-Initiale `M` fälschlich
    `Bonnie M Stark` (nur Nachname + Initiale geteilt = andere Person)."""
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    long_left = list(long_)
    full_matches = 0
    for t in short:
        hit, is_full = None, False
        for cand in long_left:
            if t == cand:
                hit, is_full = cand, True
                break
            # Initiale: einbuchstabiges Token matcht Anfangsbuchstaben.
            if len(t) == 1 and cand.startswith(t):
                hit = cand
                break
            if len(cand) == 1 and t.startswith(cand):
                hit = cand
                break
            if fuzzy and len(t) >= 4 and len(cand) >= 4:
                if difflib.SequenceMatcher(None, t, cand).ratio() >= FUZZY_THRESHOLD:
                    hit, is_full = cand, True
                    break
        if hit is None:
            return False
        long_left.remove(hit)
        full_matches += int(is_full)
    required = min(2, sum(1 for t in short if len(t) > 1))
    return full_matches >= max(1, required)


def names_match(a: str, b: str, *, fuzzy: bool = False) -> bool:
    """True, wenn zwei Oberflächenformen plausibel dieselbe Person bezeichnen.
    Reihenfolge-agnostisch, Initialen-tolerant, optional OCR-Garble-fuzzy.
    Verlangt ≥2 Tokens auf mindestens einer Seite ODER exakte Gleichheit —
    ein einzelner Nachname allein matcht nicht (zu FP-trächtig)."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if min(len(ta), len(tb)) < 2:
        return False  # einzelner (Nach-)Name allein = zu FP-trächtig
    if _tokens_match(ta, tb, fuzzy=fuzzy):
        return True
    # Glued-Token-Fallback (nur fuzzy): OCR verliert die Trennzeichen und
    # klebt Vornamen zusammen ('BONNTIMARTIE' ≈ 'BONNIE MARIE'). Nur wenn
    # eine Seite ein auffällig LANGES Token trägt (≥10 — normale Namen sind
    # kürzer, das begrenzt den FP-Raum), vergleiche die sortiert-konkatenierten
    # Formen als Ganzes.
    if fuzzy and any(len(t) >= 10 for t in ta + tb):
        ca = "".join(sorted(t for t in ta if len(t) > 1))
        cb = "".join(sorted(t for t in tb if len(t) > 1))
        if len(ca) >= 8 and len(cb) >= 8:
            return difflib.SequenceMatcher(None, ca, cb).ratio() >= FUZZY_THRESHOLD
    return False


def match_score(a: str, b: str) -> float:
    """Grober Ähnlichkeits-Score 0..1 (token-sortiert, difflib) — für
    Diagnose/Ranking, NICHT als alleinige Match-Entscheidung nutzen."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def cluster_names(forms: list[str], *, fuzzy: bool = True) -> list[list[int]]:
    """Gruppiert Oberflächenformen (Index-Listen) nach mutmaßlicher Person.
    Greedy: jede Form geht in den ersten Cluster, dessen Repräsentanten sie
    matcht. Konservativ — bei Zweifel eigener Cluster."""
    clusters: list[list[int]] = []
    for i, form in enumerate(forms):
        placed = False
        for cl in clusters:
            if any(names_match(form, forms[j], fuzzy=fuzzy) for j in cl):
                cl.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
    return clusters
