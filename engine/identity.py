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


# ---------------------------------------------------------------------------
# M4 — Organisations-Entitäten (PII_PARITY_WAVE2_HANDOVER.md §M4 / G2)
#
# Dieselbe Denkfigur wie die Personen-Schicht oben, aber mit einer ANDEREN
# Normalform — und deshalb bewusst eigene Funktionen statt eines `kind`-
# Parameters durch die Personen-Pfade: eine Firma hat keinen Vor-/Nachnamen,
# keine Initialen, keine MRZ-Form.
#
# Kalibriert am ECHTEN Scanner-Output über das Golden-Material (bcad56fa99f8,
# 748f92cfeacf, 65b4aefeed11, 32e257377809 — offline gegen _pii_scan_text
# gemessen, nicht geraten). Drei Befunde, die die Form dieser Schicht
# diktieren — die NER-Spanne ist NICHT der Firmenname:
#
#   'Wiener Privatbank SE'                    → Span 'Wiener Privatbank'   (Rechtsform fehlt)
#   'Matejka & Partner Asset Management GmbH' → 'Matejka & Partner' + 'Asset Management GmbH' (ZWEI Spans)
#   'ABACO OVERSEAS HOLDINGS INC.'            → 'ABACO' + 'OVERSEAS HOLDINGS INC'
#   '3SI Holding'                             → Span trägt Müll-Präfix ('ENTWURF JA 3SI Holding')
#
# Daraus folgt: (1) die Rechtsform darf NIE Teil des Entitäts-Schlüssels sein,
# (2) Spans müssen von Müll-Präfixen befreit werden, (3) zwei Spans, von denen
# einer ein FRAGMENT des anderen ist, müssen attachen können.
# ---------------------------------------------------------------------------

# Rechtsformen — beim Normalisieren IMMER abgeschnitten (sie sind kein Teil der
# Identität: 'Wiener Privatbank SE' und 'Wiener Privatbank' sind dieselbe Firma).
#
# NUR ECHTE RECHTSFORM-SUFFIXE. Nicht hier hinein gehören namenstragende
# Wörter wie 'Holding', 'Group', 'Trust', 'Partner', 'Capital', 'Invest' —
# sie sind Teil des NAMENS und unterscheiden Schwestergesellschaften
# voneinander ('3SI Holding' vs '3SI Partner GmbH' vs '3SI Invest GmbH' sind
# DREI Firmen). Sie zu strippen kollabierte alle drei auf den Stamm ['3si']
# und verschmölze sie zu einer Entität — exakt der Homonym-Schaden aus G12,
# nur selbstgemacht. (Erste Fassung dieser Liste tat genau das; am echten
# Material gemessen und korrigiert.)
_ORG_LEGAL_FORMS = {
    # DE/AT/CH
    "gmbh", "mbh", "gesmbh", "ag", "kg", "kgaa", "ohg", "ug", "eg", "e.v.",
    "se", "cokg",
    # EN
    "ltd", "limited", "inc", "incorporated", "corp", "corporation", "llc",
    "llp", "lp", "plc",
    # weitere
    "sa", "sas", "sarl", "srl", "spa", "bv", "nv", "ab", "oy", "aps",
    "a.s.", "ooo", "oao", "zao", "pte", "pty",
}

# Füllwörter, die NER-Spans vorn/hinten ankleben ('ENTWURF JA 3SI Holding',
# 'Compare Abaco Overseas Holdings Inc and the', 'THE LEBC STAR TRUST').
_ORG_NOISE_TOKENS = {
    "the", "die", "der", "das", "den", "dem", "des", "a", "an",
    "compare", "and", "und", "vs", "versus", "entwurf", "ja", "nein",
    "firma", "company", "darlehen", "verlustverrechnung", "siehe", "vgl",
    "betreff", "re", "fwd", "top", "neu", "alt", "non", "registered",
}

# Ein Org-Stamm-Token: Buchstaben/Ziffern (3SI, K5!), Punkt/Apostroph/Bindestrich
# innen. Ziffern werden — anders als bei name_tokens — NICHT verworfen.
_ORG_TOKEN_OK_RE = re.compile(r"^[a-z0-9ä-öø-ÿß][a-z0-9ä-öø-ÿß'\.\-&]{0,29}$")


def org_tokens(s: str) -> list[str]:
    """Zerlegt eine Firmen-Oberflächenform in Stamm-Tokens: lowercase,
    Interpunktion/Separatoren weg, Rechtsformen und Füllwörter raus,
    ZIFFERN-Tokens BLEIBEN ('3SI', 'K5' sind Teil des Namens — genau hier
    unterscheidet sich die Org- von der Personen-Normalform).

    `Wiener Privatbank SE`      → ['wiener', 'privatbank']
    `WIENER PRIVATBANK`         → ['wiener', 'privatbank']   (gleiche Entität)
    `K5 Beteiligungs GmbH.`     → ['k5', 'beteiligungs']     (Satzpunkt weg)
    `Matejka & Partner`         → ['matejka']                ('partner' = Rechtsform)
    `ENTWURF JA 3SI Holding`    → ['3si']                    (Müll-Präfix weg)
    """
    if not s:
        return []
    s = s.lower()
    s = s.replace("&", " ")
    s = re.sub(r"[<>_,;/\\|\(\)\[\]\"]+", " ", s)
    # URL-Slug-Trenner: der Bindestrich ZWISCHEN Wörtern trennt Tokens
    # ('wiener-privatbank' → 'wiener privatbank'). Ohne das wird der Slug eine
    # EIGENE Entität — und der Slug ist genau die Form, in der der Klarname
    # real ins Netz leakte (bizapedia.com/people/bonnie-stark.html).
    s = re.sub(r"(?<=[a-z0-9])-(?=[a-zà-öø-ÿß])", " ", s)
    toks: list[str] = []
    for t in re.split(r"\s+", s):
        t = t.strip(".- '\"")
        if not t:
            continue
        if t in _ORG_LEGAL_FORMS or t in _ORG_NOISE_TOKENS:
            continue
        if not _ORG_TOKEN_OK_RE.match(t):
            continue
        toks.append(t)
    return toks


def org_legal_form(s: str) -> str:
    """Die Rechtsform-Oberfläche am Ende der Form ('SE', 'GmbH', 'INC.') —
    verbatim inkl. Case/Punkt, sonst ''. Wird beim Rendern wieder angehängt."""
    if not s:
        return ""
    m = re.search(r"([A-Za-zÀ-ÿ\.&]+)\s*\.?\s*$", s.strip())
    if not m:
        return ""
    tail = m.group(1)
    if tail.lower().strip(".") in _ORG_LEGAL_FORMS:
        return tail
    return ""


def org_acronym(toks: list[str]) -> str:
    """Akronym aus den Stamm-ANFANGSBUCHSTABEN ('abaco overseas holdings' →
    'AOH' — im Golden-Material real als Kurzform belegt).

    Erst ab 3 Tokens: zweibuchstabige Akronyme ('WP') sind in Prosa zu
    FP-trächtig, um sie als forward/reverse-Paar zu registrieren — und ein
    Paar, das in Prosa matcht, schreibt Fließtext kaputt.

    BEWUSSTE GRENZE (am Material gemessen, nicht geraten): Kurzformen, die
    eine INTRA-Wort-Zerlegung eines Kompositums sind, erreicht diese Regel
    NICHT — 'Wiener Privatbank' → 'WP', die im Korpus gebrauchte Kurzform ist
    aber 'WPB' (= W-iener P-rivat-B-ank). Dafür bräuchte es einen deutschen
    Kompositum-Splitter. Der Preis dafür wäre unvertretbar: die häufigsten
    ALLCAPS-Kürzel des Golden-Materials sind HTML, USA, LEI, ROE, EBIT, EK —
    eine aggressivere Akronym-Regel würde die als Firmen faken und den
    Fließtext zerstören. 'WPB' bleibt daher ein dokumentierter Rest-Leak
    (Handover §3), KEIN stiller Fehler."""
    core = [t for t in toks if t and t[0].isalpha()]
    if len(core) < 3:
        return ""
    ac = "".join(t[0] for t in core).upper()
    return ac if len(ac) >= 3 else ""


# Generische Firmen-/Konzern-Wörter: als EINZIGES Stamm-Token tragen sie keine
# Identität ('Trust', 'Holding', 'Gesellschaft') — der spaCy-ORG-Tagger wirft
# solche Spans (am echten Material gemessen: 'Trust' aus 'verwaltet den Trust',
# 'Schwestern' aus 'sind Schwestern'). Eine Entität daraus zu bauen faked
# gewöhnliche Substantive im Fließtext und zerstört den Text.
_ORG_GENERIC_SOLO = {
    "holding", "holdings", "group", "gruppe", "trust", "partner", "partners",
    "capital", "invest", "management", "services", "beteiligung",
    "beteiligungs", "gesellschaft", "gesellschaften", "konzern", "firma",
    "unternehmen", "tochter", "tochterunternehmen", "mutter", "schwestern",
    "schwester", "stiftung", "verein", "bank", "fonds", "fund",
}

# Behörden, Register, Sanktions-/Prüflisten und Normen — sie sind das
# PRÜFWERKZEUG, nie das Prüfsubjekt. Der spaCy-ORG-Tagger wirft sie als Firmen
# aus (gemessen: 'OFAC-SDN-Liste' → organisation). Sie zu faken ist kein Leak,
# aber eine QUALITÄTS-Regression: das Modell verlöre den Namen der Liste, gegen
# die es gerade abgleichen soll ('In der Oscorp Corp steht …' — real erzeugt).
# Solange die `organisation`-Regel auf ignore steht (Default), fällt das nie
# auf — erst eine global aktivierte organisation-Regel anonymisiert Firmen
# und macht den Fehler sichtbar.
_ORG_PUBLIC_BODY_TOKENS = {
    "ofac", "sdn", "interpol", "europol", "un", "uno", "eu", "ec", "bafin",
    "fma", "finma", "sec", "fbi", "bka", "lka", "fatf", "gafi", "wko",
    "kommission", "ministerium", "behörde", "behoerde", "amt", "bundesamt",
    "finanzamt", "gericht", "staatsanwaltschaft", "notariat", "firmenbuch",
    "handelsregister", "companies", "house", "sanktionsliste", "sanktionslisten",
    "iso", "icao", "swift", "iban", "lei", "kyc", "aml", "dsgvo", "gdpr",
}


def org_is_public_body(toks: list[str]) -> bool:
    """True, wenn der Stamm eine Behörde/ein Register/eine Prüfliste bezeichnet
    (≥1 eindeutiges Behörden-Token). Solche Namen werden NICHT pseudonymisiert:
    sie sind öffentlich, nicht schutzwürdig — und ohne sie kann das Modell den
    Abgleich nicht mehr benennen, gegen den es prüft."""
    return any(t in _ORG_PUBLIC_BODY_TOKENS for t in toks)


def org_structure(form: str) -> tuple[list[str], str]:
    """Firmen-Oberflächenform → (stamm_tokens, rechtsform_oberfläche).

    Ein EINZELNES generisches Konzernwort ist kein Firmenname → leerer Stamm
    (der Aufrufer fällt dann auf den simplen String-Fake zurück, statt eine
    Entität mit sinnlosen Varianten anzulegen). Mehrtoken-Formen, die ein
    solches Wort ENTHALTEN, bleiben unberührt ('Intertrust Group' ist eine
    Firma, 'Group' allein nicht).

    Behörden/Register/Prüflisten (OFAC, Firmenbuch, Companies House) ergeben
    ebenfalls einen leeren Stamm — sie sind das Prüfwerkzeug, nicht das
    Prüfsubjekt, und dürfen nicht gefakt werden."""
    toks = org_tokens(form)
    if len(toks) == 1 and toks[0] in _ORG_GENERIC_SOLO:
        return [], ""
    if org_is_public_body(toks):
        return [], ""
    return toks, org_legal_form(form)


def org_attach(form: str, stem: list[str]) -> bool:
    """True, wenn `form` plausibel DIESELBE Organisation bezeichnet wie der
    Entitäts-Stamm `stem` (lowercase-Tokens).

    Bewusst STRIKT: nur Token-Gleichheit (nach Normalisierung) — KEIN
    Substring-/Präfix-Merge. Das ist der Kern von G2: `Wiener Privatbank` und
    `Wiener Privatbank Immobilien` sind VERWANDT, aber DISTINKT (Mutter vs.
    Tochter). Ein Präfix-Merge würde die Konzernstruktur löschen — genau der
    Schaden, den M4 verhindern soll. Die Verwandtschaft wird separat über
    `org_shares_stem` modelliert und im Fake GESPIEGELT.

    Fuzzy ist hier bewusst AUS: Firmennamen sind keine OCR-Namen; ein
    False-Merge zweier echter Firmen erzeugt Gift-Evidenz in einem
    regulatorischen Bericht (drei reale 'Atlantic Trading' → ein Fake, G12)."""
    toks = org_tokens(form)
    return bool(toks) and toks == list(stem)


def org_shares_stem(a: list[str], b: list[str]) -> bool:
    """True, wenn zwei Org-Stämme in einer Mutter-/Tochter-Beziehung stehen
    könnten: der kürzere ist ein echtes PRÄFIX des längeren und trägt ≥1
    substanzielles Token ('wiener privatbank' ⊂ 'wiener privatbank
    immobilien'). Das Namens-Enthaltensein IST die Konzern-Beziehung — im
    Fake-Raum muss sie gespiegelt werden, sonst ist die Struktur unsichtbar."""
    if not a or not b or a == b:
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) >= 1 and long_[:len(short)] == short


def org_render_variant(original: str, stem: list[str], fake_stem: list[str]) -> str:
    """Rendert den Fake-Firmennamen in DERSELBEN Oberflächenform wie
    `original`: Stamm-Token-weise ersetzt, Rechtsform/Interpunktion/Füllwörter
    verbatim, Case pro Token übernommen (ALLCAPS-Registry-Form bleibt ALLCAPS
    — das ist die Form, in der Sanktionslisten führen)."""
    if not stem or not fake_stem:
        return original
    pairs = {}
    for i, t in enumerate(stem):
        if i < len(fake_stem):
            pairs[t] = fake_stem[i]
    out = []
    for part in re.split(r"([^A-Za-z0-9À-ÖØ-öø-ÿß&\.\-']+)", original):
        key = part.lower().strip(".- '\"")
        rep = pairs.get(key)
        if rep is None:
            out.append(part)
            continue
        # Case des Originals übernehmen.
        if part.isupper() and len(part) > 1:
            rep = rep.upper()
        elif part[:1].isupper():
            rep = rep[:1].upper() + rep[1:]
        else:
            rep = rep.lower()
        # Führende/schließende Interpunktion des Original-Parts erhalten.
        lead = part[:len(part) - len(part.lstrip(".- '\""))]
        trail = part[len(part.rstrip(".- '\"")):]
        out.append(lead + rep + trail)
    return "".join(out)


def org_variant_pairs(stem: list[str], fake_stem: list[str],
                      legal_forms: list[str] | None = None
                      ) -> list[tuple[str, str]]:
    """Erwartbare Firmen-Oberflächenformen als (real, fake)-PAARE — dieselbe
    Invariante wie `standard_variant_pairs` bei Personen: die Paare werden als
    ECHTE forward/reverse-Einträge registriert, wodurch der L3a-Args-Deanon UND
    das Web-Egress-Gate org-fähig werden, ohne dass dort Code angefasst wird
    (Handover §M4).

    Deckt die am echten Material gemessenen Formen ab: Title-Case, ALLCAPS
    (Registry-/Sanktionslisten führen so!), URL-Slug, plus jede im Text real
    gesehene Rechtsform-Variante. Das AKRONYM wird mitregistriert, weil der
    Scanner es nicht erkennt (`WPB` steht in `_ORG_LEGAL_ABBR`) — nur so wird
    die Kurzform überhaupt gefasst."""
    if not stem or not fake_stem:
        return []
    pairs: list[tuple[str, str]] = []

    def _add(real: str, fake: str):
        if real and fake and real != fake and (real, fake) not in pairs:
            pairs.append((real, fake))

    real_title = " ".join(t.title() for t in stem)
    fake_title = " ".join(f.title() for f in fake_stem)
    _add(real_title, fake_title)
    _add(real_title.upper(), fake_title.upper())
    for sep in ("-", "_"):
        _add(sep.join(t.lower() for t in stem),
             sep.join(f.lower() for f in fake_stem))
    for lf in (legal_forms or []):
        if not lf:
            continue
        _add(f"{real_title} {lf}", f"{fake_title} {lf}")
        _add(f"{real_title.upper()} {lf.upper()}", f"{fake_title.upper()} {lf.upper()}")
    real_ac, fake_ac = org_acronym(stem), org_acronym(fake_stem)
    if real_ac and fake_ac:
        _add(real_ac, fake_ac)
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
