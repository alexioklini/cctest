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


# ---------------------------------------------------------------------------
# L2 — Entitäts-Schicht (PII_ANALYSIS_PARITY_HANDOVER.md §L2a)
#
# Die Pseudonymisierung hebt Namen von String- auf Entitäts-Ebene: EINE
# Fake-Identität pro Person, jede Oberflächenform mappt auf die FORMGLEICHE
# Fake-Variante. Die Funktionen hier sind pure Analyse/Rendering-Logik;
# das Mapping-Wiring (Registrierung in forward/reverse, Persistenz) lebt in
# pseudonymizer.py.
# ---------------------------------------------------------------------------

# Garble-Rescue-Schwellen (Tier 3 in entity_attach): bewusst unter der
# names_match-Schwelle, aber nur wirksam wenn JEDES Token einen distinkten
# Entitäts-Partner findet UND mindestens eines exakt/nahe ist. Kalibriert am
# echten 10-JPG-Satz ('Bonnie MASE' 0.667 zu 'marie'; 'BONNT' 0.727 zu
# 'bonnie'); 'Anna Weber' vs 'Bonnie Stark' bleibt drunter (0.4).
GARBLE_FLOOR = 0.60
GARBLE_ANCHOR = 0.72

_NAME_CHAR_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+$")
_NAME_SPLIT_RE = re.compile(r"([^A-Za-zÀ-ÖØ-öø-ÿ]+)")


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def entity_attach(form: str, sur: str, givens: list[str]) -> bool:
    """True, wenn die Oberflächenform `form` plausibel zur Entität
    (sur, givens — lowercase-Tokens) gehört. Drei Stufen:
      1. names_match (Reihenfolge/Initialen/fuzzy ≥0.84),
      2. Initialen-tolerant: unzuordenbare EIN-Buchstaben-Tokens fallen weg
         (OCR liest 'M.' als 'N.' — eine Initiale ist nicht verifizierbar),
      3. Garble-Rescue: ≥2 volle Tokens, jedes fuzzy ≥GARBLE_FLOOR auf einen
         DISTINKTEN Entitäts-Token, mindestens eines exakt/≥GARBLE_ANCHOR.
    Konservativ: ein False-Merge zweier echter Personen wiegt schwerer als
    ein Miss (der Miss erzeugt nur eine zweite Fake-Entität)."""
    toks = name_tokens(form)
    if not toks:
        return False
    ent = [t for t in ([sur] + list(givens)) if t]
    canonical = " ".join([g for g in givens if g] + [sur])
    if names_match(form, canonical, fuzzy=True):
        return True
    full = [t for t in toks if len(t) > 1]
    if len(full) >= 2 and len(full) < len(toks):
        # Stufe 2: Initialen verworfen, Rest muss voll matchen.
        if names_match(" ".join(full), canonical, fuzzy=True):
            return True
    if len(full) >= 2:
        left = list(ent)
        anchored, ok = False, True
        for t in full:
            best, best_c = 0.0, None
            for c in left:
                if t == c:
                    r = 1.0
                elif len(t) >= 4 and len(c) >= 4:
                    r = _ratio(t, c)
                else:
                    r = 0.0
                if r > best:
                    best, best_c = r, c
            if best < GARBLE_FLOOR or best_c is None:
                ok = False
                break
            if best >= GARBLE_ANCHOR:
                anchored = True
            left.remove(best_c)
        if ok and anchored:
            return True
    return False


def guess_structure(form: str) -> tuple[str, list[str]]:
    """Zerlegt eine Namens-Oberflächenform in (surname, givens) — lowercase.
    Heuristik: Komma-Form ('Stark, Bonnie M') und MRZ-Form ('STARK<<BONNIE')
    tragen den Nachnamen VORN, sonst gilt das letzte Nicht-Initialen-Token
    als Nachname ('Bonnie M Stark')."""
    if "<<" in form:
        sur, giv = parse_mrz_name(form.split("<<<")[0])
        return sur.lower(), [t for t in name_tokens(giv)]
    toks = name_tokens(form)
    if not toks:
        return "", []
    if len(toks) == 1:
        return toks[0], []
    if "," in form:
        # Nachname(n) vor dem Komma; alles danach sind Vornamen.
        head = name_tokens(form.split(",", 1)[0])
        tail = name_tokens(form.split(",", 1)[1])
        if head and tail:
            return head[-1], tail
    # Nachname = letztes volles Token; einzelne Initiale am Ende zählt nicht.
    sur_idx = len(toks) - 1
    while sur_idx > 0 and len(toks[sur_idx]) == 1:
        sur_idx -= 1
    return toks[sur_idx], toks[:sur_idx] + toks[sur_idx + 1:]


def render_variant(original: str, sur: str, givens: list[str],
                   fake_sur: str, fake_givens: list[str],
                   *, learn: list | None = None) -> str:
    """Rendert die Fake-Identität in DERSELBEN Oberflächenform wie
    `original`: Token-weise Ersetzung, alle Separatoren (Komma, Punkt,
    Unterstrich, `<`), Ziffern-Tokens (Kundennummern) und Titel (Mrs., Dr.)
    bleiben verbatim; Case (ALLCAPS/Title/lower) und Initialen-Stil werden
    pro Token übernommen.

    `learn`: optionale Liste — unbekannte volle Namens-Tokens werden als
    (real_token,) appended, damit der Aufrufer die Entität erweitern und den
    Fake-Token dafür minten kann (zweiter Aufruf rendert sie dann formtreu).
    """
    ent_real = [sur] + list(givens)
    ent_fake = [fake_sur] + list(fake_givens)
    used: set[int] = set()

    def _fake_for(tok: str) -> str | None:
        # Exakt → Initial-Upgrade (Entität kannte nur 'm', Form bringt
        # 'marie') → fuzzy. Distinkt: jeder Entitäts-Slot nur einmal.
        best, best_i = 0.0, None
        for i, c in enumerate(ent_real):
            if i in used or not c:
                continue
            if tok == c:
                best, best_i = 1.0, i
                break
            if len(c) == 1 and tok.startswith(c):
                best, best_i = 0.9, i
                break
            if len(tok) >= 4 and len(c) >= 4:
                r = _ratio(tok, c)
                if r >= GARBLE_FLOOR and r > best:
                    best, best_i = r, i
        if best_i is None:
            return None
        used.add(best_i)
        return ent_fake[best_i]

    def _initial_for(tok: str) -> str:
        for i, c in enumerate(ent_real):
            if i in used or not c:
                continue
            if c.startswith(tok):
                used.add(i)
                return ent_fake[i][0]
        # OCR-Garble-Initiale ('N.' statt 'M.') → erste unverbrauchte
        # Vornamens-Initiale, sonst Nachnamen-Initiale.
        for i in range(1, len(ent_real)):
            if i not in used and ent_real[i]:
                used.add(i)
                return ent_fake[i][0]
        return ent_fake[0][0]

    out = []
    for part in _NAME_SPLIT_RE.split(original):
        if not part or not _NAME_CHAR_RE.match(part):
            out.append(part)
            continue
        low = part.lower()
        if low.rstrip(".") in _TITLES:
            out.append(part)
            continue
        if len(low) == 1 and out and out[-1].endswith(("'", "’")):
            # Genitiv-/Klitik-Suffix ("Bonnie Stark's"), KEIN Initial —
            # verbatim durchreichen. Ohne den Guard verbrauchte das lone `s`
            # den nächsten freien Vornamens-Slot und renderte
            # "Cameron Taylor'm" (im v9.341-Live-E2E-Ledger gemessen).
            out.append(part)
            continue
        if len(low) == 1:
            rep = _initial_for(low)
        else:
            rep = _fake_for(low)
            if rep is None:
                if learn is not None:
                    learn.append(low)
                out.append(part)
                continue
        if part.isupper() and len(part) > 1:
            rep = rep.upper()
        elif not part[0].isupper():
            rep = rep.lower()
        out.append(rep)
    return "".join(out)


def standard_variant_pairs(sur: str, givens: list[str],
                           fake_sur: str, fake_givens: list[str]
                           ) -> list[tuple[str, str]]:
    """Erwartbare Oberflächenformen-PAARE (real, fake) einer Person —
    dieselben Templates auf beiden Seiten, damit pseudonymizer sie als echte
    forward/reverse-Einträge registrieren kann (Handover §7.9: L3a-Args-Deanon
    und der Web-Egress-Gate arbeiten auf diesen Tabellen). Real-Tokens kommen
    lowercase, Fake-Tokens in Display-Case; nur Vornamens-Slots mit VOLLEM
    realen Token (keine Initialen) werden in Volltext-Templates verwendet."""
    giv_full = [(g, f) for g, f in zip(givens, fake_givens) if g and len(g) > 1]
    pairs: list[tuple[str, str]] = []

    def _add(real: str, fake: str):
        if real and fake and real != fake and (real, fake) not in pairs:
            pairs.append((real, fake))

    def _both(tmpl) -> None:
        _add(tmpl(sur.title(), [g.title() for g, _ in giv_full]),
             tmpl(fake_sur, [f for _, f in giv_full]))

    if giv_full:
        _both(lambda s, g: f"{g[0]} {s}")
        _both(lambda s, g: f"{s}, {g[0]}")
        _both(lambda s, g: f"{g[0][0]}. {s}")
        if len(giv_full) > 1:
            _both(lambda s, g: f"{g[0]} {' '.join(g[1:])} {s}")
            _both(lambda s, g: f"{s}, {g[0]} {' '.join(g[1:])}")
            _both(lambda s, g: f"{g[0]} {' '.join(x[0] + '.' for x in g[1:])} {s}")
            _both(lambda s, g: f"{g[0]} {' '.join(x[0] for x in g[1:])} {s}")
        # MRZ-Namensform (füllzeichen-frei — die 44er-Padding-Form baut
        # _fake_mrz) + ALLCAPS-Varianten.
        _both(lambda s, g: f"{s.upper()}<<{'<'.join(x.upper() for x in g)}")
        _both(lambda s, g: f"{g[0].upper()} {s.upper()}")
        _both(lambda s, g: f"{s.upper()}, {' '.join(x.upper() for x in g)}")
        if len(giv_full) > 1:
            # VIZ-Vornamenszeile ('BONNIE MARIE') + OCR-geklebt
            # ('BONNIEMARIE', am echten Material gemessen) — nur wenn die
            # geklebte Form lang genug ist, um distinktiv zu bleiben.
            _both(lambda s, g: " ".join(x.upper() for x in g))
            glued_real = "".join(g.upper() for g, _ in giv_full)
            glued_fake = "".join(f.upper() for _, f in giv_full)
            if len(glued_real) >= 8:
                _add(glued_real, glued_fake)
    _add(sur.title(), fake_sur)
    # Standalone ALLCAPS surname (VIZ surname line of a passport reads as a
    # bare 'STARK' line) — single tokens are invisible to the fuzzy entity
    # sweep, so the exact pair must exist; word-bounded sweep keeps
    # 'STARKSTROM' safe like the Title-case pair.
    _add(sur.upper(), fake_sur.upper())
    return pairs


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
