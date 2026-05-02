# Extracted from claude_cli.py — PII/GDPR scanner, rate limiter, client model routing

import collections
import json
import logging
import os
import threading
import time

# --- PII / GDPR Scanner -------------------------------------------------------
# Regex-based detection of personal data in outgoing LLM payloads. Mirrors the
# browser-side PIIScanner in web/index.html. Server-side is a belt-and-suspenders
# layer: logs findings to audit.db and, if gdpr_scanner.server_block is true in
# config.json, raises before hitting the provider.

_PII_RULES: list[dict] = []


def _pii_rules() -> list[dict]:
    """Return compiled PII rules. Built lazily. Mirrors PIIScanner.rules in web/index.html."""
    global _PII_RULES
    if _PII_RULES:
        return _PII_RULES

    import re as _re

    def _digits(s): return "".join(c for c in s if c.isdigit())

    def _luhn_str(s: str) -> bool:
        d = _digits(s)
        if not d: return False
        total, alt = 0, False
        for c in reversed(d):
            n = int(c)
            if alt:
                n *= 2
                if n > 9: n -= 9
            total += n
            alt = not alt
        return total % 10 == 0

    def _cc_ok(m: str) -> bool:
        d = _digits(m)
        return 13 <= len(d) <= 19 and _luhn_str(d)

    def _iban_ok(s: str) -> bool:
        iban = "".join(s.split()).upper()
        if not 15 <= len(iban) <= 34: return False
        rearr = iban[4:] + iban[:4]
        num = ""
        for c in rearr:
            if "A" <= c <= "Z": num += str(ord(c) - 55)
            elif c.isdigit(): num += c
            else: return False
        rem = 0
        for d in num: rem = (rem * 10 + int(d)) % 97
        return rem == 1

    def _phone_ok(m: str) -> bool:
        d = _digits(m)
        return 8 <= len(d) <= 15

    def _ipv4_ok(m: str) -> bool:
        return not any(m.startswith(p) for p in ("0.", "127.", "255.", "169.254."))

    def _us_ssn_dashed_ok(m: str) -> bool:
        a, b, c = m.split("-")
        if a in ("000", "666") or a.startswith("9"): return False
        return b != "00" and c != "0000"

    def _us_ssn_ctx_ok(m: str) -> bool:
        g = _re.search(r"\d{9}", m)
        if not g: return False
        s = g.group(0)
        a, b, c = s[:3], s[3:5], s[5:]
        if a in ("000", "666") or a.startswith("9"): return False
        return b != "00" and c != "0000"

    def _at_svnr_ok(m: str) -> bool:
        if len(m) != 10 or not m.isdigit(): return False
        w = [3, 7, 9, 5, 8, 4, 2, 1, 6]
        d = [int(c) for c in m]
        vals = [d[0], d[1]] + d[3:]
        if sum(x * y for x, y in zip(vals, w)) % 11 != d[2]: return False
        dd, mm = int(m[4:6]), int(m[6:8])
        return 1 <= dd <= 31 and 1 <= mm <= 12

    def _fr_insee_ok(m: str) -> bool:
        clean = "".join(c if c.isdigit() else ("0" if c.upper() in "AB " else "") for c in m)
        if len(clean) != 15: return False
        body, key = clean[:13], int(clean[13:])
        return (97 - (int(body) % 97)) == key

    def _de_steuerid_ok(m: str) -> bool:
        g = _re.search(r"\d{11}", m)
        if not g: return False
        d = g.group(0)
        if d[0] == "0": return False
        counts: dict[str, int] = {}
        for c in d: counts[c] = counts.get(c, 0) + 1
        repeats = [n for n in counts.values() if n > 1]
        return len(repeats) == 1 and repeats[0] in (2, 3)

    def _dni_nie_ok(m: str) -> bool:
        s = m.upper()
        letters = "TRWAGMYFPDXBNJZSQVHLCKE"
        try:
            if s[0] in "XYZ":
                num = int(str("XYZ".index(s[0])) + s[1:-1])
            else:
                num = int(s[:-1])
        except ValueError:
            return False
        return letters[num % 23] == s[-1]

    # ── EU national IDs ──

    def _uk_nhs_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 10: return False
        s = sum(int(d[i]) * (10 - i) for i in range(9))
        chk = 11 - (s % 11)
        if chk == 11: chk = 0
        return chk != 10 and chk == int(d[9])

    def _nl_bsn_ok(m: str) -> bool:
        g = _re.search(r"\d{8,9}", m)
        if not g: return False
        d = g.group(0).rjust(9, "0")
        if int(d) == 0: return False
        w = [9,8,7,6,5,4,3,2,-1]
        return sum(int(d[i]) * w[i] for i in range(9)) % 11 == 0

    def _be_national_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 11: return False
        body9 = d[:9]; chk = int(d[9:])
        a = 97 - (int(body9) % 97)
        b = 97 - (int("2" + body9) % 97)
        return chk in (a, b)

    def _pl_pesel_ok(m: str) -> bool:
        w = [1,3,7,9,1,3,7,9,1,3]
        s = sum(int(m[i]) * w[i] for i in range(10))
        chk = (10 - (s % 10)) % 10
        if chk != int(m[10]): return False
        mm = int(m[2:4])
        return (1 <= mm <= 12) or (21 <= mm <= 32) or (41 <= mm <= 52) or (61 <= mm <= 72) or (81 <= mm <= 92)

    def _pt_nif_ok(m: str) -> bool:
        g = _re.search(r"\d{9}", m)
        if not g: return False
        d = g.group(0)
        if d[0] not in "123568 9": return False
        s = sum(int(d[i]) * (9 - i) for i in range(8))
        chk = 11 - (s % 11)
        if chk >= 10: chk = 0
        return chk == int(d[8])

    def _se_personnummer_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) not in (10, 12): return False
        short = d[2:] if len(d) == 12 else d
        s = 0
        for i in range(9):
            n = int(short[i]) * (2 if i % 2 == 0 else 1)
            if n > 9: n -= 9
            s += n
        chk = (10 - (s % 10)) % 10
        return chk == int(short[9])

    def _dk_cpr_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 10: return False
        dd, mm = int(d[:2]), int(d[2:4])
        return 1 <= dd <= 31 and 1 <= mm <= 12

    def _no_fnr_ok(m: str) -> bool:
        if len(m) != 11: return False
        d = [int(c) for c in m]
        w1 = [3,7,6,1,8,9,4,5,2]
        w2 = [5,4,3,2,7,6,5,4,3,2]
        s1 = sum(d[i] * w1[i] for i in range(9))
        k1 = 11 - (s1 % 11)
        if k1 == 11: k1 = 0
        if k1 == 10 or k1 != d[9]: return False
        s2 = sum(d[i] * w2[i] for i in range(10))
        k2 = 11 - (s2 % 11)
        if k2 == 11: k2 = 0
        return k2 != 10 and k2 == d[10]

    def _ch_ahv_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 13 or not d.startswith("756"): return False
        s = sum(int(d[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
        return (10 - (s % 10)) % 10 == int(d[12])

    def _cz_rc_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) not in (9, 10): return False
        if len(d) == 10:
            n = int(d)
            if n % 11 != 0 and not (n % 11 == 10 and d[9] == "0"): return False
        mm = int(d[2:4])
        real = mm - 50 if mm > 50 else mm
        return 1 <= real <= 12

    def _ro_cnp_ok(m: str) -> bool:
        if len(m) != 13: return False
        w = [2,7,9,1,4,6,3,5,8,2,7,9]
        s = sum(int(m[i]) * w[i] for i in range(12))
        chk = s % 11
        if chk == 10: chk = 1
        if chk != int(m[12]): return False
        mm, dd = int(m[3:5]), int(m[5:7])
        return 1 <= mm <= 12 and 1 <= dd <= 31

    def _hu_taj_ok(m: str) -> bool:
        g = _re.search(r"\d{3}[- ]?\d{3}[- ]?\d{3}", m)
        if not g: return False
        d = _digits(g.group(0))
        if len(d) != 9: return False
        s = sum(int(d[i]) * (3 if i % 2 == 0 else 7) for i in range(8))
        return (s % 10) == int(d[8])

    def _gr_amka_ok(m: str) -> bool:
        dd, mm = int(m[:2]), int(m[2:4])
        if dd < 1 or dd > 31 or mm < 1 or mm > 12: return False
        return _luhn_str(m)

    def _bg_egn_ok(m: str) -> bool:
        w = [2,4,8,5,10,9,7,3,6]
        s = sum(int(m[i]) * w[i] for i in range(9))
        chk = (s % 11) % 10
        if chk != int(m[9]): return False
        mm = int(m[2:4])
        real = mm - 40 if mm > 40 else (mm - 20 if mm > 20 else mm)
        return 1 <= real <= 12

    def _ie_pps_ok(m: str) -> bool:
        s = m.upper()
        if len(s) not in (8, 9): return False
        digits = s[:7]; check = s[7]
        letters = "WABCDEFGHIJKLMNOPQRSTUV"
        w = [8,7,6,5,4,3,2]
        total = sum(int(digits[i]) * w[i] for i in range(7))
        if len(s) == 9:
            extra = 0 if s[8] == "W" else (ord(s[8]) - 64)
            total += extra * 9
        return letters[total % 23] == check

    # ── Americas + APAC ──

    def _br_cpf_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 11 or d == d[0] * 11: return False
        def calc(end):
            s = sum(int(d[i]) * (end + 1 - i) for i in range(end))
            r = (s * 10) % 11
            return 0 if r == 10 else r
        return calc(9) == int(d[9]) and calc(10) == int(d[10])

    def _br_cnpj_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 14 or d == d[0] * 14: return False
        w1 = [5,4,3,2,9,8,7,6,5,4,3,2]
        w2 = [6,5,4,3,2,9,8,7,6,5,4,3,2]
        def calc(end, ws):
            s = sum(int(d[i]) * ws[i] for i in range(end))
            r = s % 11
            return 0 if r < 2 else 11 - r
        return calc(12, w1) == int(d[12]) and calc(13, w2) == int(d[13])

    def _ca_sin_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 9 or d[0] in ("0", "8"): return False
        return _luhn_str(d)

    def _in_aadhaar_ok(m: str) -> bool:
        # Verhoeff — m may include keyword prefix, extract 12 digits
        g = _re.search(r"[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}", m)
        if not g: return False
        d = _digits(g.group(0))
        if len(d) != 12: return False
        d2 = [
            [0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
            [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
            [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
            [9,8,7,6,5,4,3,2,1,0]]
        p = [
            [0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
            [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
            [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
        c = 0
        rev = [int(x) for x in reversed(d)]
        for i, x in enumerate(rev):
            c = d2[c][p[i % 8][x]]
        return c == 0

    def _jp_mynumber_ok(m: str) -> bool:
        w = [6,5,4,3,2,7,6,5,4,3,2]
        s = sum(int(m[i]) * w[i] for i in range(11))
        r = s % 11
        chk = 0 if r <= 1 else 11 - r
        return chk == int(m[11])

    def _kr_rrn_ok(m: str) -> bool:
        d = _digits(m)
        if len(d) != 13: return False
        w = [2,3,4,5,6,7,8,9,2,3,4,5]
        s = sum(int(d[i]) * w[i] for i in range(12))
        chk = (11 - (s % 11)) % 10
        if chk != int(d[12]): return False
        mm, dd = int(d[2:4]), int(d[4:6])
        return 1 <= mm <= 12 and 1 <= dd <= 31

    def _sg_nric_ok(m: str) -> bool:
        if len(m) != 9: return False
        first, digits, check = m[0], m[1:8], m[8]
        w = [2,7,6,5,4,3,2]
        s = sum(int(digits[i]) * w[i] for i in range(7))
        if first in ("T", "G"): s += 4
        if first == "M": s += 3
        r = s % 11
        tables = {
            "S": "JZIHGFEDCBA", "T": "JZIHGFEDCBA",
            "F": "XWUTRQPNMLK", "G": "XWUTRQPNMLK",
            "M": "KLJNPQRTUWX",
        }
        t = tables.get(first)
        return bool(t) and t[r] == check

    def _tw_nid_ok(m: str) -> bool:
        mp = {"A":10,"B":11,"C":12,"D":13,"E":14,"F":15,"G":16,"H":17,"I":34,"J":18,"K":19,"L":20,"M":21,"N":22,"O":35,"P":23,"Q":24,"R":25,"S":26,"T":27,"U":28,"V":29,"W":32,"X":30,"Y":31,"Z":33}
        pref = mp.get(m[0])
        if pref is None: return False
        first, second = pref // 10, pref % 10
        digits = [first, second] + [int(c) for c in m[1:]]
        w = [1,9,8,7,6,5,4,3,2,1,1]
        return sum(digits[i] * w[i] for i in range(len(digits))) % 10 == 0

    # ── Tier 2 validators ──

    def _basic_auth_ok(m: str) -> bool:
        return not _re.search(r"://[^:]*:(password|changeme|example|xxx+|\*+)@", m, _re.IGNORECASE)

    def _generic_secret_ok(m: str) -> bool:
        g = _re.search(r"[\"']([A-Za-z0-9+/=_\-]{20,})[\"']", m)
        if not g: return False
        v = g.group(1)
        if _re.fullmatch(r"(?:xxx+|\*+|changeme|example|placeholder|your[_-]?(?:key|token|secret))", v, _re.IGNORECASE):
            return False
        return len(set(v)) >= 6

    _PII_RULES = [
        # ── Tier 2: cloud secrets (distinct prefixes → high priority) ──
        {"id": "pem_private_key", "label": "Private key",
         "re": _re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]{1,10000}?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")},
        {"id": "aws_access_key", "label": "AWS access key ID",
         "re": _re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Z0-9])")},
        {"id": "aws_secret_key", "label": "AWS secret access key",
         "re": _re.compile(r"(?:aws_secret_access_key|aws[_-]?secret[_-]?access[_-]?key|aws[_-]?secret)[\s:=\"']*([A-Za-z0-9/+]{40})(?![A-Za-z0-9/+=])", _re.IGNORECASE)},
        {"id": "github_app_token", "label": "GitHub app token",
         "re": _re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")},
        {"id": "github_pat", "label": "GitHub personal access token",
         "re": _re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")},
        {"id": "slack_token", "label": "Slack token",
         "re": _re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,200}\b")},
        {"id": "slack_webhook", "label": "Slack webhook URL",
         "re": _re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+")},
        {"id": "google_api_key", "label": "Google API key",
         "re": _re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")},
        {"id": "google_oauth_client", "label": "Google OAuth client ID",
         "re": _re.compile(r"\b\d{12}-[a-z0-9]{32}\.apps\.googleusercontent\.com\b")},
        {"id": "stripe_live", "label": "Stripe live key",
         "re": _re.compile(r"\b(?:sk|rk|pk)_live_[0-9a-zA-Z]{24,99}\b")},
        {"id": "stripe_test", "label": "Stripe test key",
         "re": _re.compile(r"\b(?:sk|rk|pk)_test_[0-9a-zA-Z]{24,99}\b")},
        {"id": "openai_key", "label": "OpenAI API key",
         "re": _re.compile(r"\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20,}\b")},
        {"id": "anthropic_key", "label": "Anthropic API key",
         "re": _re.compile(r"\bsk-ant-[a-z0-9]{2,6}-[A-Za-z0-9_\-]{85,120}\b")},
        {"id": "twilio_sid", "label": "Twilio account SID",
         "re": _re.compile(r"\bAC[a-f0-9]{32}\b")},
        {"id": "sendgrid_key", "label": "SendGrid API key",
         "re": _re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")},
        {"id": "mailgun_key", "label": "Mailgun API key",
         "re": _re.compile(r"\bkey-[a-f0-9]{32}\b")},
        {"id": "jwt", "label": "JWT",
         "re": _re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")},
        {"id": "azure_storage_conn", "label": "Azure Storage connection string",
         "re": _re.compile(r"DefaultEndpointsProtocol=https;AccountName=[A-Za-z0-9]+;AccountKey=[A-Za-z0-9+/=]{80,};?(?:EndpointSuffix=[^;\s]+)?")},
        {"id": "azure_account_key", "label": "Azure account key",
         "re": _re.compile(r"(?:AccountKey|SharedAccessKey)=([A-Za-z0-9+/=]{40,100})(?=[;\"'\s]|$)")},
        {"id": "basic_auth_url", "label": "Credentials in URL",
         "re": _re.compile(r"\b(?:https?|ftp|ssh|git|postgres|postgresql|mysql|mongodb|redis)://[^\s:@/]+:[^\s@/]+@[A-Za-z0-9.\-]+"),
         "ok": _basic_auth_ok},
        {"id": "generic_secret_assignment", "label": "Hard-coded secret",
         "re": _re.compile(r"\b(?:api[_-]?key|secret|token|password|passwd|pwd|auth|bearer)[\s:=]{1,4}[\"']([A-Za-z0-9+/=_\-]{20,})[\"']", _re.IGNORECASE),
         "ok": _generic_secret_ok},

        # ── Context-gated first (keyword+digits beats bare-digits rules below) ──
        {"id": "de_steuerid", "label": "German Steuer-ID",
         "re": _re.compile(r"(?:\bSteuer[- ]?ID\b|Steueridentifikationsnummer|\bTIN\b)[^\d\n]{0,20}(\d{11})(?!\d)", _re.IGNORECASE),
         "ok": _de_steuerid_ok},

        # ── Standard identifiers ──
        {"id": "email", "label": "Email address",
         "re": _re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")},
        {"id": "iban", "label": "IBAN",
         "re": _re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9][ ]?){11,30}\b"),
         "ok": _iban_ok},
        {"id": "ipv4", "label": "IPv4 address",
         "re": _re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?!\d)"),
         "ok": _ipv4_ok},
        {"id": "ipv6", "label": "IPv6 address",
         "re": _re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b")},
        {"id": "us_ssn", "label": "US Social Security Number",
         "re": _re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
         "ok": _us_ssn_dashed_ok},
        {"id": "us_ssn_ctx", "label": "US Social Security Number",
         "re": _re.compile(r"(?:\bSSN\b|\bsocial\s+security\b)[^\w\n]{0,15}\d{9}(?!\d)", _re.IGNORECASE),
         "ok": _us_ssn_ctx_ok},

        # ── Tier 1 EU national IDs ──
        {"id": "uk_nino", "label": "UK National Insurance Number",
         "re": _re.compile(r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z][0-9]{6}[A-D]?\b")},
        {"id": "uk_nhs", "label": "UK NHS number",
         "re": _re.compile(r"(?<!\d)\d{3}[ -]?\d{3}[ -]?\d{4}(?!\d)"),
         "ok": _uk_nhs_ok},
        {"id": "nl_bsn", "label": "Dutch BSN",
         "re": _re.compile(r"(?:\bBSN\b|burgerservicenummer|sofinummer)[^\d\n]{0,15}(\d{8,9})(?!\d)", _re.IGNORECASE),
         "ok": _nl_bsn_ok},
        {"id": "be_national", "label": "Belgian national number",
         "re": _re.compile(r"(?<!\d)\d{2}[. ]?\d{2}[. ]?\d{2}[- ]?\d{3}[. ]?\d{2}(?!\d)"),
         "ok": _be_national_ok},
        {"id": "pl_pesel", "label": "Polish PESEL",
         "re": _re.compile(r"(?<!\d)\d{11}(?!\d)"),
         "ok": _pl_pesel_ok},
        {"id": "pt_nif", "label": "Portuguese NIF",
         "re": _re.compile(r"(?:\bNIF\b|número\s+fiscal|contribuinte)[^\d\n]{0,15}(\d{9})(?!\d)", _re.IGNORECASE),
         "ok": _pt_nif_ok},
        {"id": "se_personnummer", "label": "Swedish personnummer",
         "re": _re.compile(r"(?<!\d)(?:\d{2})?\d{6}[-+]?\d{4}(?!\d)"),
         "ok": _se_personnummer_ok},
        {"id": "dk_cpr", "label": "Danish CPR",
         "re": _re.compile(r"(?<!\d)\d{6}[- ]?\d{4}(?!\d)"),
         "ok": _dk_cpr_ok},
        {"id": "no_fnr", "label": "Norwegian fødselsnummer",
         "re": _re.compile(r"(?<!\d)\d{11}(?!\d)"),
         "ok": _no_fnr_ok},
        {"id": "ch_ahv", "label": "Swiss AHV (OASI)",
         "re": _re.compile(r"\b756[.\- ]?\d{4}[.\- ]?\d{4}[.\- ]?\d{2}\b"),
         "ok": _ch_ahv_ok},
        {"id": "cz_rc", "label": "Czech rodné číslo",
         "re": _re.compile(r"(?<!\d)\d{6}/?\d{3,4}(?!\d)"),
         "ok": _cz_rc_ok},
        {"id": "ro_cnp", "label": "Romanian CNP",
         "re": _re.compile(r"(?<!\d)\d{13}(?!\d)"),
         "ok": _ro_cnp_ok},
        {"id": "hu_taj", "label": "Hungarian TAJ",
         "re": _re.compile(r"(?:\bTAJ\b|társadalom|társadalombiztos)[^\d\n]{0,15}(\d{3}[- ]?\d{3}[- ]?\d{3})(?!\d)", _re.IGNORECASE),
         "ok": _hu_taj_ok},
        {"id": "gr_amka", "label": "Greek AMKA",
         "re": _re.compile(r"(?<!\d)\d{11}(?!\d)"),
         "ok": _gr_amka_ok},
        {"id": "bg_egn", "label": "Bulgarian EGN",
         "re": _re.compile(r"(?<!\d)\d{10}(?!\d)"),
         "ok": _bg_egn_ok},
        {"id": "ie_pps", "label": "Irish PPS",
         "re": _re.compile(r"\b\d{7}[A-W][A-IW]?\b"),
         "ok": _ie_pps_ok},

        # ── Tier 1 Americas + APAC ──
        {"id": "br_cpf", "label": "Brazilian CPF",
         "re": _re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
         "ok": _br_cpf_ok},
        {"id": "br_cnpj", "label": "Brazilian CNPJ",
         "re": _re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
         "ok": _br_cnpj_ok},
        {"id": "ca_sin", "label": "Canadian SIN",
         "re": _re.compile(r"(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}(?!\d)"),
         "ok": _ca_sin_ok},
        {"id": "mx_curp", "label": "Mexican CURP",
         "re": _re.compile(r"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", _re.IGNORECASE)},
        {"id": "ar_dni", "label": "Argentine DNI",
         "re": _re.compile(r"\bDNI[\s:]*\d{1,2}\.?\d{3}\.?\d{3}\b", _re.IGNORECASE)},
        {"id": "in_aadhaar", "label": "Indian Aadhaar",
         "re": _re.compile(r"(?:\baadhaar\b|\bUID\b|\bUIDAI\b)[^\d\n]{0,20}([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)", _re.IGNORECASE),
         "ok": _in_aadhaar_ok},
        {"id": "jp_mynumber", "label": "Japanese My Number",
         "re": _re.compile(r"(?<!\d)\d{12}(?!\d)"),
         "ok": _jp_mynumber_ok},
        {"id": "kr_rrn", "label": "Korean RRN",
         "re": _re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)"),
         "ok": _kr_rrn_ok},
        {"id": "sg_nric", "label": "Singapore NRIC/FIN",
         "re": _re.compile(r"\b[STFGM]\d{7}[A-Z]\b"),
         "ok": _sg_nric_ok},
        {"id": "tw_nid", "label": "Taiwan national ID",
         "re": _re.compile(r"\b[A-Z][12]\d{8}\b"),
         "ok": _tw_nid_ok},

        # ── Other checksum IDs ──
        {"id": "at_svnr", "label": "Austrian Sozialversicherungsnummer",
         "re": _re.compile(r"(?<!\d)\d{10}(?!\d)"),
         "ok": _at_svnr_ok},
        {"id": "fr_insee", "label": "French INSEE / NIR",
         "re": _re.compile(r"(?<!\d)[12]\d{2}(?:0[1-9]|1[0-2]|[2-9]\d)(?:\d{2}|\dA|\dB)\d{3}\d{3}[\s ]?\d{2}(?!\d)", _re.IGNORECASE),
         "ok": _fr_insee_ok},
        {"id": "es_dni_nie", "label": "Spanish DNI/NIE",
         "re": _re.compile(r"(?<![A-Z0-9])(?:[XYZ]?\d{7,8}[A-HJ-NP-TV-Z])(?![A-Z0-9])", _re.IGNORECASE),
         "ok": _dni_nie_ok},
        {"id": "it_codicefiscale", "label": "Italian Codice Fiscale",
         "re": _re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", _re.IGNORECASE)},

        # ── Credit card (after national IDs) ──
        {"id": "credit_card", "label": "Credit card number",
         "re": _re.compile(r"(?<![+\d])(?:\d[ -]?){13,19}(?!\d)"),
         "ok": _cc_ok},

        # ── Phone (after national IDs) ──
        {"id": "phone", "label": "Phone number",
         "re": _re.compile(r"(?:(?<![\w.])\+\d{1,3}[\s().-]?(?:\d[\s().-]?){7,14}\d|(?<!\d)\d{3}[\s.-]\d{3,4}[\s.-]\d{3,4}(?!\d))"),
         "ok": _phone_ok},

        # ── Context-gated heuristics ──
        {"id": "passport", "label": "Passport number",
         "re": _re.compile(r"passport[^\w\n]{0,20}([A-Z][0-9]{6,9}|[A-Z]{1,2}[0-9]{6,8})", _re.IGNORECASE)},
        {"id": "dob", "label": "Date of birth",
         "re": _re.compile(r"(?:\b(?:DOB|born|date\s+of\s+birth|geboren|geburtsdatum|né|née|nacido)\b[^\n]{0,20}?(?:\d{1,2}[\/.\- ]\d{1,2}[\/.\- ]\d{2,4}|\d{4}-\d{2}-\d{2}))", _re.IGNORECASE)},

        # ── Context-fallback: fire on keyword + number-shape even if checksum
        # fails. Runs LAST — strict checksum rules above still win first. ──
        {"id": "svnr_ctx", "label": "Social-insurance number (likely)",
         "re": _re.compile(r"(?:\bSVNR\b|\bSV[- ]?Nr\.?\b|\bSV[- ]?Nummer\b|Sozialversicherungsnummer|social[- ]?insurance|national[- ]?insurance|\bNIN\b)[^\d\n]{0,20}(\d[\d \-\/.]{7,19}\d)", _re.IGNORECASE)},
        {"id": "ssn_ctx_loose", "label": "Social Security Number (likely)",
         "re": _re.compile(r"(?:\bSSN\b|social[- ]?security[- ]?(?:number|no\.?|\#)?)[^\d\n]{0,15}(\d{3}[- ]?\d{2}[- ]?\d{4}|\d{9})", _re.IGNORECASE)},
        {"id": "tax_id_ctx", "label": "Tax identification number (likely)",
         "re": _re.compile(r"(?:\bTIN\b|tax[- ]?id(?:entification)?[- ]?(?:number|no\.?)?|Steuer[- ]?ID|Steuernummer|USt[- ]?ID|VAT[- ]?(?:number|no\.?))[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{6,18}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "insurance_number_ctx", "label": "Insurance number (likely)",
         "re": _re.compile(r"(?:insurance[- ]?number|insurance[- ]?no\.?|Versicherungsnummer|numéro[- ]?(?:de[- ]?)?sécurité[- ]?sociale|numero[- ]?(?:di[- ]?)?previdenza)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{6,19}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "id_card_ctx", "label": "ID / identity card number (likely)",
         "re": _re.compile(r"(?:\bID[- ]?(?:number|no\.?|card)\b|Personalausweis|carte[- ]?d['\s-]identit|documento[- ]?(?:de[- ]?)?identi[dt]ad|cédula)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{5,16}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "drivers_license_ctx", "label": "Driver's license number (likely)",
         "re": _re.compile(r"(?:driver'?s?[- ]?licen[sc]e|Führerschein|permis[- ]?de[- ]?conduire|carnet[- ]?de[- ]?conducir|patente)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{5,16}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "passport_ctx_loose", "label": "Passport number (likely)",
         "re": _re.compile(r"(?:passport|Reisepass|passeport|pasaporte|passaporto)[^\w\n]{0,20}([A-Z0-9][A-Z0-9\- ]{5,14}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "bank_account_ctx", "label": "Bank account number (likely)",
         "re": _re.compile(r"(?:\baccount[- ]?(?:number|no\.?|\#)\b|\bacct\.?[- ]?(?:no\.?|\#)?\b|\bIBAN\b|Kontonummer|numéro[- ]?de[- ]?compte|número[- ]?de[- ]?cuenta)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-/.]{7,30}[A-Z0-9])", _re.IGNORECASE)},
        {"id": "health_insurance_ctx", "label": "Health insurance number (likely)",
         "re": _re.compile(r"(?:health[- ]?insurance|Krankenversicherungsnummer|Krankenkasse|assurance[- ]?maladie|seguridad[- ]?social|Medicare|Medicaid|\bNHS[- ]?(?:number|no\.?)?|\bAMKA\b|\bTAJ\b)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-./]{5,19}[A-Z0-9])", _re.IGNORECASE)},
    ]
    return _PII_RULES


def _pii_scan_bare_identifiers(text: str) -> list[dict]:
    """Heuristic: flag pasted lists of bare numeric identifiers. Fires when the
    message is dominated (>=60%) by 9-14-digit ID-shaped lines with little prose.
    Catches the 'what is this number?' paste case where the value fails all
    strict checksums but is clearly identifier-shaped."""
    import re as _re
    if not text or not isinstance(text, str) or len(text) > 2000:
        return []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    id_like = [l for l in lines if _re.fullmatch(r"\d[\d .\-/]{7,18}\d", l)]
    threshold = max(1, (len(lines) * 6 + 9) // 10)  # ceil(len*0.6)
    if len(id_like) < threshold:
        return []
    findings: list[dict] = []
    for line in id_like:
        digits = _re.sub(r"\D", "", line)
        if not 9 <= len(digits) <= 14:
            continue
        idx = text.find(line)
        if idx < 0:
            continue
        findings.append({
            "rule_id": "bare_identifier",
            "label": "Numeric identifier (unverified)",
            "start": idx,
            "end": idx + len(line),
            "len": len(line),
        })
        if len(findings) >= 20:
            break
    return findings


def _pii_scan_text(text: str, max_findings: int = 100,
                   cfg: dict | None = None) -> list[dict]:
    """Scan text for PII. Returns list of {rule_id, label, start, end, category,
    action} with overlap-suppression across rules (first match wins).

    Applies per-category actions: rules with action='ignore' are skipped entirely;
    email findings matching `email_allowlist` are suppressed regardless of action.
    """
    if not text or not isinstance(text, str):
        return []
    if cfg is None:
        cfg = _get_gdpr_scanner_config()
    allowlist = cfg.get("email_allowlist") or []
    findings: list[dict] = []
    spans: list[tuple[int, int]] = []
    for rule in _pii_rules():
        rid = rule["id"]
        action = _pii_effective_action(rid, cfg)
        if action == "ignore":
            continue
        for m in rule["re"].finditer(text):
            match = m.group(0)
            ok = rule.get("ok")
            if ok and not ok(match):
                continue
            s, e = m.start(), m.end()
            if any(s < se and e > ss for ss, se in spans):
                continue
            # Email allowlist: if this email matches a trusted address/domain,
            # skip it silently (don't reserve the span so weaker rules could
            # theoretically reclaim it — but no other rule matches bare emails
            # anyway, and consuming the span would incorrectly mask findings).
            if rid == "email" and _pii_email_allowed(match, allowlist):
                continue
            spans.append((s, e))
            findings.append({
                "rule_id": rid, "label": rule["label"],
                "start": s, "end": e, "len": e - s,
                "category": PII_RULE_CATEGORIES.get(rid, "personal"),
                "action": action,
            })
            if len(findings) >= max_findings:
                return findings
    # Heuristic: bare-identifier fallback when the rule catalog didn't cover a
    # paste of ID-shaped numbers. Checksum-strict rules above still win first.
    bare_action = _pii_effective_action("bare_identifier", cfg)
    if bare_action != "ignore":
        for f in _pii_scan_bare_identifiers(text):
            if any(f["start"] < se and f["end"] > ss for ss, se in spans):
                continue
            spans.append((f["start"], f["end"]))
            f["category"] = "bare_id"
            f["action"] = bare_action
            findings.append(f)
            if len(findings) >= max_findings:
                break
    return findings


def _pii_scan_messages(messages: list[dict], max_findings: int = 100,
                       cfg: dict | None = None) -> list[dict]:
    """Scan outgoing LLM messages for PII. Only looks at user + system content
    (assistant content + tool results come from the model and aren't 'leaks'
    from the user's perspective — but we could extend if needed)."""
    if cfg is None:
        cfg = _get_gdpr_scanner_config()
    findings: list[dict] = []
    for idx, msg in enumerate(messages or []):
        role = msg.get("role")
        if role not in ("user", "system"):
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            for f in _pii_scan_text(content, max_findings=max_findings - len(findings), cfg=cfg):
                f["msg_index"] = idx
                findings.append(f)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    for f in _pii_scan_text(block.get("text", ""), max_findings=max_findings - len(findings), cfg=cfg):
                        f["msg_index"] = idx
                        findings.append(f)
        if len(findings) >= max_findings:
            break
    return findings


def _pii_summarize(findings: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["label"]] = counts.get(f["label"], 0) + 1
    return counts


def _pii_worst_action(findings: list[dict]) -> str:
    """Return the most-severe action present in findings: block > warn > ignore.
    Assumes findings carry an 'action' field (populated by _pii_scan_text).
    """
    worst = "ignore"
    for f in findings:
        a = f.get("action") or "warn"
        if a == "block":
            return "block"
        if a == "warn" and worst != "block":
            worst = "warn"
    return worst


# --- Rate Limiting ---

class RateLimiter:
    """Sliding-window rate limiter per agent. In-memory only (resets on restart)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._tokens: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._cost: dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def _prune(self, dq: collections.deque, cutoff: float):
        """Remove entries older than cutoff timestamp."""
        while dq and (dq[0] if isinstance(dq[0], (int, float)) else dq[0][0]) < cutoff:
            dq.popleft()

    def check(self, agent_id: str) -> tuple[bool, str, dict]:
        """Check if a request is allowed for this agent.

        Returns (allowed, reason, usage_info).
        Loads limits from the agent's agent.json rate_limits field.
        """
        limits = self._get_limits(agent_id)
        if not limits:
            return True, "", {}

        now = time.time()
        with self._lock:
            # Check requests/minute
            rpm_limit = limits.get("max_requests_per_minute")
            if rpm_limit:
                dq = self._requests[agent_id]
                self._prune(dq, now - 60)
                if len(dq) >= rpm_limit:
                    oldest = dq[0]
                    retry = 60 - (now - oldest)
                    return False, f"Rate limit: {rpm_limit} requests/minute exceeded. Retry in {int(retry)}s.", {
                        "dimension": "max_requests_per_minute", "current": len(dq), "limit": rpm_limit}

            # Check tokens/hour
            tph_limit = limits.get("max_tokens_per_hour")
            if tph_limit:
                dq = self._tokens[agent_id]
                self._prune(dq, now - 3600)
                total = sum(t[1] for t in dq)
                if total >= tph_limit:
                    return False, f"Rate limit: {tph_limit} tokens/hour exceeded.", {
                        "dimension": "max_tokens_per_hour", "current": total, "limit": tph_limit}

            # Check cost/day
            cpd_limit = limits.get("max_cost_per_day")
            if cpd_limit:
                dq = self._cost[agent_id]
                self._prune(dq, now - 86400)
                total = sum(t[1] for t in dq)
                if total >= cpd_limit:
                    return False, f"Rate limit: ${cpd_limit}/day cost limit exceeded.", {
                        "dimension": "max_cost_per_day", "current": total, "limit": cpd_limit}

            # Record the request timestamp
            self._requests[agent_id].append(now)

        return True, "", {}

    def record_usage(self, agent_id: str, tokens: int, cost: float):
        """Record token and cost usage after a successful response."""
        now = time.time()
        with self._lock:
            self._tokens[agent_id].append((now, tokens))
            self._cost[agent_id].append((now, cost))

    def get_status(self, agent_id: str | None = None) -> dict:
        """Get current usage vs limits for display."""
        result = {}
        agents_to_check = [agent_id] if agent_id else list(set(
            list(self._requests.keys()) + list(self._tokens.keys())))

        now = time.time()
        with self._lock:
            for aid in agents_to_check:
                limits = self._get_limits(aid)
                if not limits:
                    continue
                # Requests/minute
                dq_r = self._requests.get(aid, collections.deque())
                self._prune(dq_r, now - 60)
                rpm_limit = limits.get("max_requests_per_minute", 0)
                # Tokens/hour
                dq_t = self._tokens.get(aid, collections.deque())
                self._prune(dq_t, now - 3600)
                tph_total = sum(t[1] for t in dq_t)
                tph_limit = limits.get("max_tokens_per_hour", 0)
                # Cost/day
                dq_c = self._cost.get(aid, collections.deque())
                self._prune(dq_c, now - 86400)
                cpd_total = sum(t[1] for t in dq_c)
                cpd_limit = limits.get("max_cost_per_day", 0)

                result[aid] = {
                    "requests_per_minute": {"current": len(dq_r), "limit": rpm_limit},
                    "tokens_per_hour": {"current": tph_total, "limit": tph_limit},
                    "cost_per_day": {"current": round(cpd_total, 4), "limit": cpd_limit},
                }
        return result

    def _get_limits(self, agent_id: str) -> dict:
        """Load rate limits from agent.json."""
        try:
            agent_json = os.path.join(AGENTS_DIR, agent_id, "agent.json")
            if os.path.isfile(agent_json):
                with open(agent_json) as f:
                    cfg = json.load(f)
                return cfg.get("rate_limits", {})
        except (OSError, json.JSONDecodeError):
            pass
        return {}


_rate_limiter: RateLimiter | None = None


# ---------------------------------------------------------------------------
# GDPR / PII category maps (source of truth; mirrored in web/index.html)
# ---------------------------------------------------------------------------

PII_RULE_CATEGORIES: dict[str, str] = {
    # Tier 2 — cloud secrets / API keys / credentials. Always highest severity.
    "pem_private_key": "secrets", "aws_access_key": "secrets",
    "aws_secret_key": "secrets", "github_app_token": "secrets",
    "github_pat": "secrets", "slack_token": "secrets",
    "slack_webhook": "secrets", "google_api_key": "secrets",
    "google_oauth_client": "secrets", "stripe_live": "secrets",
    "stripe_test": "secrets", "openai_key": "secrets",
    "anthropic_key": "secrets", "twilio_sid": "secrets",
    "sendgrid_key": "secrets", "mailgun_key": "secrets",
    "jwt": "secrets", "azure_storage_conn": "secrets",
    "azure_account_key": "secrets", "basic_auth_url": "secrets",
    "generic_secret_assignment": "secrets",

    # Tier 1 — national IDs with checksum validation.
    "de_steuerid": "national_id", "uk_nino": "national_id",
    "uk_nhs": "national_id", "nl_bsn": "national_id",
    "be_national": "national_id", "pl_pesel": "national_id",
    "pt_nif": "national_id", "se_personnummer": "national_id",
    "dk_cpr": "national_id", "no_fnr": "national_id",
    "ch_ahv": "national_id", "cz_rc": "national_id",
    "ro_cnp": "national_id", "hu_taj": "national_id",
    "gr_amka": "national_id", "bg_egn": "national_id",
    "ie_pps": "national_id", "br_cpf": "national_id",
    "br_cnpj": "national_id", "ca_sin": "national_id",
    "mx_curp": "national_id", "ar_dni": "national_id",
    "in_aadhaar": "national_id", "jp_mynumber": "national_id",
    "kr_rrn": "national_id", "sg_nric": "national_id",
    "tw_nid": "national_id", "at_svnr": "national_id",
    "fr_insee": "national_id", "es_dni_nie": "national_id",
    "it_codicefiscale": "national_id", "us_ssn": "national_id",
    "us_ssn_ctx": "national_id",

    # Context-fallback — keyword + shape, no checksum. Softer category.
    "svnr_ctx": "national_id_ctx", "ssn_ctx_loose": "national_id_ctx",
    "tax_id_ctx": "national_id_ctx", "insurance_number_ctx": "national_id_ctx",
    "id_card_ctx": "national_id_ctx", "drivers_license_ctx": "national_id_ctx",
    "passport_ctx_loose": "national_id_ctx", "health_insurance_ctx": "national_id_ctx",

    # Financial
    "iban": "financial", "credit_card": "financial",
    "bank_account_ctx": "financial",

    # Contact info (emails + phone) — often intentional, allowlist-aware
    "email": "contact", "phone": "contact",

    # Network identifiers (often infrastructure, not personal data)
    "ipv4": "network", "ipv6": "network",

    # Biographical / personal-document identifiers
    "passport": "personal", "dob": "personal",

    # Heuristic fallback
    "bare_identifier": "bare_id",
}

# Default category actions. "block" means refuse (or swap to local) when
# server_block is true; downgraded to "warn" when server_block is false.
PII_DEFAULT_CATEGORY_ACTIONS: dict[str, str] = {
    "secrets":         "block",
    "national_id":     "warn",
    "national_id_ctx": "warn",
    "financial":       "warn",
    "contact":         "ignore",
    "network":         "ignore",
    "personal":        "warn",
    "bare_id":         "warn",
}

# ---------------------------------------------------------------------------
# GDPR scanner config + enforcement helpers
# ---------------------------------------------------------------------------

_gdpr_scanner_cache: dict | None = None
_gdpr_scanner_cache_time: float = 0.0


def _get_gdpr_scanner_config() -> dict:
    """Read gdpr_scanner config block from config.json. 30s cache.

    Shape:
      {"enabled": bool,                  # master on/off (default True)
       "server_log": bool,               # audit.db entries on findings (default True)
       "server_block": bool,             # master switch for "block" actions (default False)
                                         #   false → block actions downgrade to warn (back-compat)
                                         #   true  → block actions refuse unless model is local
       "default_local_fallback_model": str,
       "categories": {<cat>: {"action": "ignore|warn|block"}},
       "rule_overrides": {<rule_id>: "ignore|warn|block"},
       "email_allowlist": [str, ...]}    # full addresses or "@domain" patterns
    """
    global _gdpr_scanner_cache, _gdpr_scanner_cache_time
    now = time.time()
    if _gdpr_scanner_cache is not None and (now - _gdpr_scanner_cache_time) < 30:
        return _gdpr_scanner_cache
    cfg = {
        "enabled": True, "server_log": True, "server_block": False,
        "default_local_fallback_model": "",
        "categories": {cat: {"action": act} for cat, act in PII_DEFAULT_CATEGORY_ACTIONS.items()},
        "rule_overrides": {},
        "email_allowlist": [],
    }
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(cfg_path) as f:
            loaded = json.load(f).get("gdpr_scanner") or {}
        for k in ("enabled", "server_log", "server_block"):
            if k in loaded:
                cfg[k] = bool(loaded[k])
        if "default_local_fallback_model" in loaded:
            cfg["default_local_fallback_model"] = str(loaded["default_local_fallback_model"] or "")
        cats_in = loaded.get("categories") or {}
        if isinstance(cats_in, dict):
            for cat, entry in cats_in.items():
                if cat not in cfg["categories"]:
                    continue
                if isinstance(entry, dict) and entry.get("action") in ("ignore", "warn", "block"):
                    cfg["categories"][cat]["action"] = entry["action"]
                elif isinstance(entry, str) and entry in ("ignore", "warn", "block"):
                    cfg["categories"][cat]["action"] = entry
        ovr_in = loaded.get("rule_overrides") or {}
        if isinstance(ovr_in, dict):
            for rid, act in ovr_in.items():
                if act in ("ignore", "warn", "block") and rid in PII_RULE_CATEGORIES:
                    cfg["rule_overrides"][rid] = act
        al_in = loaded.get("email_allowlist") or []
        if isinstance(al_in, list):
            cfg["email_allowlist"] = [str(e).strip().lower() for e in al_in
                                      if isinstance(e, str) and e.strip()]
    except (OSError, json.JSONDecodeError):
        pass
    _gdpr_scanner_cache = cfg
    _gdpr_scanner_cache_time = now
    return cfg


def _pii_effective_action(rule_id: str, cfg: dict | None = None) -> str:
    """Return effective action for a rule_id — 'ignore', 'warn', or 'block'.

    Precedence: rule_overrides[rule_id] > categories[cat].action > default.
    When `server_block` is false, any 'block' is downgraded to 'warn' so the
    master switch stays meaningful.
    """
    if cfg is None:
        cfg = _get_gdpr_scanner_config()
    ovr = (cfg.get("rule_overrides") or {}).get(rule_id)
    if ovr in ("ignore", "warn", "block"):
        action = ovr
    else:
        cat = PII_RULE_CATEGORIES.get(rule_id, "personal")
        cat_cfg = (cfg.get("categories") or {}).get(cat) or {}
        action = cat_cfg.get("action") or PII_DEFAULT_CATEGORY_ACTIONS.get(cat, "warn")
    if action == "block" and not cfg.get("server_block", False):
        action = "warn"
    return action


def _pii_email_allowed(email: str, allowlist: list[str]) -> bool:
    """True if the email matches the allowlist. Entries starting with '@' are
    treated as domain patterns (any address at that domain). Case-insensitive."""
    if not email or not allowlist:
        return False
    e = email.strip().lower()
    for pat in allowlist:
        p = pat.strip().lower()
        if not p:
            continue
        if p.startswith("@"):
            if e.endswith(p):
                return True
        elif e == p:
            return True
    return False


def is_model_local(model_id: str) -> bool:
    """Return True if `model_id` resolves to a local-gateway provider."""
    if not model_id:
        return False
    try:
        prov = resolve_provider_for_model(model_id)
    except Exception:
        return False
    return _is_local_base_url(prov.get("base_url", "") if prov else "")


def _invalidate_gdpr_cache():
    """Called from server.py when gdpr_scanner config changes."""
    global _gdpr_scanner_cache, _gdpr_scanner_cache_time
    _gdpr_scanner_cache = None
    _gdpr_scanner_cache_time = 0.0


# --- Client-hosted local models manifest ----------------------------------
# Server declares GGUF model weights that clients (Electron desktop app) may
# download and run locally. Family string is the compat key — server-side oMLX
# model and client-side GGUF are "the same model" for routing purposes when
# their family matches, even if quant/format differ. See CLAUDE.md.

_client_models_cache = None
_client_models_cache_time = 0.0
_CLIENT_MODELS_TTL = 10.0


def _load_client_models() -> list:
    """Read config.json → client_models: [{id, family, gguf_path, sha256,
    size_bytes, auto_download}]. 10s cache. Returns [] on any error."""
    global _client_models_cache, _client_models_cache_time
    now = time.time()
    if _client_models_cache is not None and (now - _client_models_cache_time) < _CLIENT_MODELS_TTL:
        return _client_models_cache
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    entries = []
    try:
        with open(cfg_path) as f:
            raw = json.load(f).get("client_models", []) or []
        if isinstance(raw, list):
            entries = [e for e in raw if isinstance(e, dict) and e.get("id") and e.get("family")]
    except (OSError, json.JSONDecodeError):
        entries = []
    _client_models_cache = entries
    _client_models_cache_time = now
    return entries


def _invalidate_client_models_cache():
    """Called from server.py when client_models config changes."""
    global _client_models_cache, _client_models_cache_time
    _client_models_cache = None
    _client_models_cache_time = 0.0


def get_client_model(model_id: str) -> dict | None:
    """Return the client_models manifest entry matching `model_id` (by id),
    or None if the model isn't in the client-eligible list."""
    for entry in _load_client_models():
        if entry.get("id") == model_id:
            return entry
    return None


def get_client_model_by_family(family: str) -> dict | None:
    """Return the first manifest entry with the given family string, or None."""
    if not family:
        return None
    for entry in _load_client_models():
        if entry.get("family") == family:
            return entry
    return None


def is_model_client_executable(capabilities: dict | None, model_id: str) -> tuple[bool, str]:
    """Decide whether a request for `model_id` should be routed to client-
    hosted inference instead of running on the server, given the client-
    declared capabilities dict (from Session.client_capabilities).

    Returns (True, family) if:
      - capabilities.enabled is True
      - model_id has a manifest entry (i.e. is a client-eligible model)
      - the manifest entry's family appears in capabilities.families

    Returns (False, "") otherwise. The caller is expected to have verified
    the request is interactive (has event_callback) — background/scheduled
    requests never route to clients regardless of capabilities.
    """
    if not capabilities or not model_id:
        return False, ""
    if not capabilities.get("enabled"):
        return False, ""
    families = capabilities.get("families") or []
    if not families:
        return False, ""
    entry = get_client_model(model_id)
    if not entry:
        return False, ""
    family = entry.get("family", "")
    if family and family in families:
        return True, family
    return False, ""


class GDPRBlockedError(RuntimeError):
    """Raised by gdpr_pick_model_for_background when PII is detected, the
    server is configured in hard-block mode, and no safe local route exists.

    Callers catch this and decide what to skip (e.g. drop the background call,
    return a static summary, emit a delegate error). Not raised for the main
    chat path — that surface has its own RuntimeError branch with a different
    message aimed at the end user.
    """


def gdpr_pick_model_for_background(model: str, texts, purpose: str = "") -> str:
    """Decide which model to use for a background/worker LLM call.

    Behavior when the scanner is enabled AND `texts` contain PII:
      - current model is local           → return model unchanged
      - local fallback configured + OK   → swap to fallback (logged as pii_auto_fallback)
      - no usable fallback, block off    → return model unchanged (warn-only)
      - no usable fallback, block on     → raise GDPRBlockedError

    Every PII detection at this layer emits a `pii_detected` audit row with
    `source=background`, independent of whether the model is swapped.

    `texts` accepts str or iterable-of-str. Unexpected errors in scanning or
    config access fall open (return model) — never block a background call on
    scanner bugs.

    Used by: next-prompt suggestions, chat summary, memory classifier, worker
    tool-result summariser, _run_delegate (delegate tool + scheduler + agent
    tasks).
    """
    try:
        cfg = _get_gdpr_scanner_config()
    except Exception:
        return model
    if not cfg.get("enabled", True):
        return model

    # Normalise texts up-front so we can scan regardless of fallback state.
    if isinstance(texts, str):
        samples = [texts]
    else:
        try:
            samples = [t for t in texts if isinstance(t, str)]
        except TypeError:
            return model
    if not samples:
        return model

    # Scan. Fail open on scanner errors.
    try:
        findings = []
        for s in samples:
            if not s:
                continue
            findings.extend(_pii_scan_text(s, max_findings=5, cfg=cfg))
            if findings:
                break
    except Exception:
        return model
    if not findings:
        return model

    _agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    _agent_id = _agent.agent_id if _agent else "main"
    _sid = getattr(_thread_local, 'current_session_id', None) or ""
    _n = len(findings)
    _log_audit = bool(cfg.get("server_log", True) and _audit_log)
    # server_block is the master switch but the per-finding action is what
    # decides refusal — a warn-category finding never blocks even if the master
    # switch is on. _pii_worst_action returns "block" only if both are true.
    _worst_action = _pii_worst_action(findings)
    _server_block = (_worst_action == "block")

    # Always record the detection at the background layer, independent of any
    # swap decision. Best-effort.
    try:
        print(f"[gdpr] session={_sid} agent={_agent_id} purpose={purpose} "
              f"findings={_n} (background)", flush=True)
    except Exception:
        pass
    if _log_audit:
        try:
            _audit_log.log_action(
                agent=_agent_id,
                action_type="pii_detected",
                tool_name="gdpr_scanner",
                args_summary=f"{_n} findings",
                result_summary=f"purpose={purpose or '-'} model={model}",
                result_status="warning",
                session_id=_sid or None,
                source="background",
            )
        except Exception:
            pass

    # Already on a local model — nothing to reroute, nothing to block.
    try:
        model_is_local = is_model_local(model)
    except Exception:
        model_is_local = False
    if model_is_local:
        return model

    # Attempt the swap. fallback == model is treated as no-swap.
    fallback = (cfg.get("default_local_fallback_model") or "").strip()
    swap_ok = False
    if fallback and fallback != model:
        try:
            fcfg = (_models_config or {}).get(fallback) or {}
            if fcfg.get("enabled") and is_model_local(fallback):
                swap_ok = True
        except Exception:
            swap_ok = False

    if swap_ok:
        try:
            print(f"[gdpr] auto-fallback session={_sid} agent={_agent_id} "
                  f"purpose={purpose} {model} -> {fallback} ({_n} findings)", flush=True)
        except Exception:
            pass
        if _log_audit:
            try:
                _audit_log.log_action(
                    agent=_agent_id,
                    action_type="pii_auto_fallback",
                    tool_name="gdpr_scanner",
                    args_summary=f"{model} -> {fallback}",
                    result_summary=f"purpose={purpose or '-'} findings={_n}",
                    result_status="ok",
                    session_id=_sid or None,
                    source="background",
                )
            except Exception:
                pass
        return fallback

    # No swap possible and model is cloud.
    if _server_block:
        try:
            print(f"[gdpr] BLOCK session={_sid} agent={_agent_id} purpose={purpose} "
                  f"model={model} findings={_n} (no local fallback available)",
                  flush=True)
        except Exception:
            pass
        if _log_audit:
            try:
                _audit_log.log_action(
                    agent=_agent_id,
                    action_type="pii_blocked",
                    tool_name="gdpr_scanner",
                    args_summary=f"model={model}",
                    result_summary=f"purpose={purpose or '-'} findings={_n} "
                                   f"fallback={fallback or '-'}",
                    result_status="blocked",
                    session_id=_sid or None,
                    source="background",
                )
            except Exception:
                pass
        raise GDPRBlockedError(
            f"[GDPR block] Background call refused (purpose={purpose or '-'}): "
            f"{_n} personal-data finding(s) in payload and no usable local "
            f"fallback model is configured. Set "
            f"gdpr_scanner.default_local_fallback_model in Settings, or "
            f"disable server_block."
        )

    # Warn-only mode: leave the caller to use the cloud model.
    return model
