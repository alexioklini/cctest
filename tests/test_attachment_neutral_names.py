"""Attachment filenames are pseudonymised like any other PII — not disk-renamed.

History: M11 (G14) originally neutralised the name AT THE SOURCE by writing the
file to disk as `att_01.pdf`. That protected the PATH but left the original name
riding into the wire verbatim through the `name_block` preamble
(`att_01.pdf = CF_-_STARK_Bonnie_…`) — so the rename bought almost nothing while
still forcing a fake path on disk. Replaced (this change) with the obvious
symmetric design: the file keeps its REAL name on disk, and the filename is
pseudonymised in the wire exactly like body text —

  * detected: the basename is emitted into the SCANNED typed half (name_block),
    so its name spans are found, decided in the dialog, and minted;
  * anonymised: `pseudonymizer.pseudonymize_filename` rewrites the basename with
    the mapping's OPAQUE tokens (path-safe — substring-restorable through the
    underscores that the body sweeps deliberately skip);
  * restored: the read_document args-deanon seam (read_document ∈
    GDPR_ARGS_DEANON_TOOLS) turns the token back into the real stem before
    dispatch, and the reply-deanon shows the human the real name.

So: real name on disk (read_document byte-for-byte unaffected), fake name toward
the cloud, real name back to the human — the same contract as body-text PII.

Why it matters, from the corpus:
  * risikoanalysen — `Geldwäsche Risikoanalyse M&P AM_2025.xlsx`: the analysis
    subject was de-facto de-anonymised to the cloud provider via the path.
  * ko-kunden      — `CF_-_…_STARK_Bonnie_M_Mrs._107625_…`: every list_directory
    effectively shipped the customer list as paths.

Bare test interpreter — no server, no network.
"""

import os
import unittest

import pseudonymizer as ps


class TestPseudonymizeFilename(unittest.TestCase):
    """The load-bearing new primitive: pseudonymize_filename must be path-safe
    (opaque tokens, extension + non-PII tokens preserved) and round-trip exactly
    through deanonymize_text (the mechanism the read_document args-deanon uses)."""

    def _seed_person(self, decision="Bonnie Stark"):
        m = ps.new_mapping()
        ps.seed_from_decision(m, "name", decision)
        return m

    def test_underscore_joined_name_is_tokenised_both_parts(self):
        m = self._seed_person()
        fake = ps.pseudonymize_filename(
            "CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf", mapping=m)
        # Surname AND given (whose shape-fake is short, so NOT registered as a
        # standalone body token) must both be gone — the entity layer, not the
        # forward table, is the source of eligible tokens.
        self.assertNotIn("STARK", fake)
        self.assertNotIn("Bonnie", fake)
        # Opaque tokens, path-safe.
        self.assertIn("<NAME_", fake)
        # Extension + non-PII tokens preserved.
        self.assertTrue(fake.endswith(".pdf"))
        self.assertIn("107625", fake)
        self.assertIn("Scan", fake)
        self.assertIn("CF", fake)

    def test_roundtrip_restores_the_exact_path(self):
        """This is the read_document contract: a wire path with the fake
        basename must deanonymise back byte-for-byte to the real path."""
        m = self._seed_person()
        base = "STARK_Bonnie_M.pdf"
        fake = ps.pseudonymize_filename(base, mapping=m)
        wire = f"/tmp/brain-attachments/s1/{fake}"
        restored, n = ps.deanonymize_text(wire, mapping=m)
        self.assertEqual(restored, f"/tmp/brain-attachments/s1/{base}")
        self.assertEqual(n, 2)

    def test_casing_is_preserved_per_surface(self):
        m = self._seed_person()
        # Two files, different casing of the same name → each restores to its own.
        f_upper = ps.pseudonymize_filename("STARK_report.pdf", mapping=m)
        f_title = ps.pseudonymize_filename("Stark_report.pdf", mapping=m)
        self.assertEqual(ps.deanonymize_text(f_upper, mapping=m)[0], "STARK_report.pdf")
        self.assertEqual(ps.deanonymize_text(f_title, mapping=m)[0], "Stark_report.pdf")

    def test_non_pii_filename_passes_through_verbatim(self):
        m = self._seed_person()
        for plain in ("JuliusBaer_Code-of-Ethics.pdf", "Quartalsbericht_2024.pdf",
                      "README", "notes.txt"):
            self.assertEqual(ps.pseudonymize_filename(plain, mapping=m), plain)

    def test_no_entities_is_a_noop(self):
        m = ps.new_mapping()
        self.assertEqual(
            ps.pseudonymize_filename("STARK_Bonnie.pdf", mapping=m),
            "STARK_Bonnie.pdf")

    def test_body_text_rendering_is_untouched(self):
        """The filename pass must NOT overwrite forward[] — body text must keep
        rendering the nice shape-fake, never the opaque filename token."""
        m = self._seed_person()
        ps.pseudonymize_filename("STARK_Bonnie.pdf", mapping=m)  # mint filename tokens
        body, _ = ps.apply_known_values(
            "Frau Bonnie Stark. STARK ist der Nachname.", mapping=m,
            categories=None)
        self.assertNotIn("<NAME_", body)          # opaque token did not bleed in
        self.assertNotIn("Bonnie", body)          # name still anonymised
        self.assertNotIn("Stark", body)

    def test_idempotent_stable_tokens(self):
        m = self._seed_person()
        a = ps.pseudonymize_filename("STARK_Bonnie_M.pdf", mapping=m)
        b = ps.pseudonymize_filename("STARK_Bonnie_M.pdf", mapping=m)
        self.assertEqual(a, b)

    def test_alias_key_never_leaks_into_output(self):
        m = self._seed_person()
        fake = ps.pseudonymize_filename("STARK_Bonnie.pdf", mapping=m)
        self.assertNotIn("\x00file:", fake)

    def test_organisation_filename(self):
        m = ps.new_mapping()
        ps.seed_from_decision(m, "organisation", "Wiener Privatbank SE")
        fake = ps.pseudonymize_filename("Wiener_Privatbank_Bericht_2026.pdf", mapping=m)
        self.assertNotIn("Wiener", fake)
        self.assertNotIn("Privatbank", fake)
        self.assertEqual(
            ps.deanonymize_text(f"/x/{fake}", mapping=m)[0],
            "/x/Wiener_Privatbank_Bericht_2026.pdf")

    def test_password_as_filename(self):
        """`Alcuatmisi02026!.txt` (4a6b889aee66) — the filename IS a secret, and
        it is not a name entity, so it is caught by the substring pass over
        non-name confirmed values (OPAQUE fake → path-safe roundtrip)."""
        m = ps.new_mapping()
        m.record("Alcuatmisi02026", m.next_token("password"), "password")
        fake = ps.pseudonymize_filename("Alcuatmisi02026!.txt", mapping=m)
        self.assertNotIn("Alcuatmisi02026", fake)
        self.assertTrue(fake.endswith(".txt"))
        self.assertEqual(
            ps.deanonymize_text(f"/x/{fake}", mapping=m)[0],
            "/x/Alcuatmisi02026!.txt")


class TestNameBlockLandsInScannedHalf(unittest.TestCase):
    """The filename must land in the SCANNED (typed) half so the scanner detects
    its name spans — not in the exempt path notice, which would only move the leak.
    """

    def test_name_block_is_split_into_the_typed_half(self):
        from handlers.chat import _split_attachment_notice, _NAME_BLOCK_MARKER

        typed_in = "Prüfe die Akte."
        name_block = (_NAME_BLOCK_MARKER + "\n"
                      "  CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf")
        notice = ("\n\n[User attached files saved to disk:]\n"
                  "  - /tmp/brain-attachments/s1/CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf")

        typed, notice_half = _split_attachment_notice(typed_in + name_block + notice)

        # The filename is in the SCANNED half — this is what makes the scanner
        # detect + decide the name so the worker can mint its fake.
        self.assertIn("STARK_Bonnie", typed)
        # The notice half is the exempt PATH region. It still carries the real
        # basename at THIS stage (split ≠ pseudonymise); the worker rewrites
        # those path lines with pseudonymize_filename right after, and the
        # args-deanon restores them before read_document. What matters here is
        # only that the split put the name_block on the scannable side.
        self.assertIn("[User attached files saved to disk", notice_half)
        self.assertNotIn("[Dateinamen der Anhänge", notice_half)


if __name__ == "__main__":
    unittest.main()
