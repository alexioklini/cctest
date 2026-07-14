"""M11 (G14) — neutral attachment filenames.

The attachment PATH is exempt from PII scanning (see chat._split_attachment_notice)
because pseudonymising it would hand the model a fake path and break read_document.
The L-catalog therefore filed "the clear name is in the path" as an unavoidable
residual leak. It is not unavoidable: that framing assumes the file on disk must
carry the user's filename. Brain writes that file itself — the name is ours.

So the name is neutralised AT THE SOURCE (`att_01.pdf`) and the original is emitted
as scanned CONTENT in the typed half, where the scanner and the pii_decisions ledger
see it. The path stays REAL, so read_document is byte-for-byte unaffected — there is
no fake path, and nothing can break.

Why it matters, from the corpus:
  * risikoanalysen — every session's filename names subject AND purpose
    (`Geldwäsche Risikoanalyse M&P AM_2025.xlsx`): the analysis subject was de-facto
    de-anonymised to the cloud provider no matter how well the content was protected.
  * ko-kunden      — `CF_-_…_STARK_Bonnie_M_Mrs._107625_…`: every list_directory
    effectively shipped the customer list as paths.
  * `Alcuatmisi02026!.txt` (4a6b889aee66) — an attachment whose NAME is a password,
    riding into the wire unscanned via the exemption.

Bare test interpreter — no server, no network.
"""

import os
import re
import unittest
from unittest import mock


class TestNeutralNaming(unittest.TestCase):
    """The naming rule itself: deterministic, extension-preserving, gated on the
    SCANNER (not on an active mapping — the file is written at upload time, before
    the mapping is minted; gating on the mapping would be a chicken-and-egg race)."""

    @staticmethod
    def _neutralise(disk_files, scanner_enabled):
        """Mirrors the logic in handlers/chat.py (the naming rule under test)."""
        name_map, saved = {}, []
        for i, f in enumerate(disk_files, start=1):
            fname = f.get("name", "file")
            safe = fname.replace("/", "_").replace("\\", "_")
            if scanner_enabled:
                ext = os.path.splitext(safe)[1].lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext or ""):
                    ext = ""
                neutral = f"att_{i:02d}{ext}"
                name_map[neutral] = fname
                safe = neutral
            saved.append(safe)
        return saved, name_map

    def test_clear_name_never_reaches_the_path(self):
        files = [{"name": "CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf"},
                 {"name": "Geldwäsche Risikoanalyse M&P AM_2025.xlsx"}]
        saved, nmap = self._neutralise(files, scanner_enabled=True)
        self.assertEqual(saved, ["att_01.pdf", "att_02.xlsx"])
        for p in saved:
            self.assertNotIn("STARK", p)
            self.assertNotIn("Bonnie", p)
            self.assertNotIn("Risikoanalyse", p)
        # …and the original is retained for the (scanned) content block.
        self.assertEqual(nmap["att_01.pdf"],
                         "CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf")

    def test_password_in_filename_is_neutralised(self):
        """`Alcuatmisi02026!.txt` — the filename IS a secret (4a6b889aee66)."""
        saved, nmap = self._neutralise(
            [{"name": "Alcuatmisi02026!.txt"}], scanner_enabled=True)
        self.assertEqual(saved, ["att_01.txt"])
        self.assertNotIn("Alcuatmisi", saved[0])
        # The secret still goes through the SCANNER (as content), not around it.
        self.assertEqual(nmap["att_01.txt"], "Alcuatmisi02026!.txt")

    def test_extension_is_preserved_so_read_document_still_dispatches(self):
        """read_document routes on the extension — the whole point of keeping a
        REAL path is that nothing downstream changes."""
        cases = [("Akte.pdf", ".pdf"), ("liste.XLSX", ".xlsx"),
                 ("scan.JPG", ".jpg"), ("notiz.docx", ".docx"),
                 ("daten.csv", ".csv")]
        for fname, ext in cases:
            saved, _ = self._neutralise([{"name": fname}], scanner_enabled=True)
            self.assertTrue(saved[0].endswith(ext),
                            f"{fname} → {saved[0]} lost its extension")

    def test_extensionless_file_still_works(self):
        saved, nmap = self._neutralise([{"name": "README"}], scanner_enabled=True)
        self.assertEqual(saved, ["att_01"])
        self.assertEqual(nmap["att_01"], "README")

    def test_names_are_unique_per_turn(self):
        """Two files with the SAME original name must not collide on disk."""
        saved, _ = self._neutralise(
            [{"name": "pass.jpg"}, {"name": "pass.jpg"}], scanner_enabled=True)
        self.assertEqual(len(set(saved)), 2, "neutral names collided")

    def test_scanner_off_keeps_the_old_behaviour_exactly(self):
        files = [{"name": "CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf"}]
        saved, nmap = self._neutralise(files, scanner_enabled=False)
        self.assertEqual(saved, ["CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf"])
        self.assertEqual(nmap, {}, "no mapping block when the scanner is off")

    def test_path_separators_are_still_stripped(self):
        """The original safety property (no traversal) must survive."""
        saved, _ = self._neutralise(
            [{"name": "../../etc/passwd"}], scanner_enabled=True)
        self.assertEqual(saved, ["att_01"])
        self.assertNotIn("/", saved[0])
        self.assertNotIn("..", saved[0])


class TestOriginalNameIsScannedNotExempt(unittest.TestCase):
    """The other half of M11: the original name must land in the SCANNED (typed)
    half, not in the exempt notice half — otherwise we've only moved the leak."""

    def test_name_block_lands_in_the_typed_half(self):
        from handlers.chat import _split_attachment_notice

        typed_in = "Prüfe die Akte."
        name_block = ("\n\n[Originaldateinamen der Anhänge "
                      "(Datei auf der Platte = Originalname):]\n"
                      "  att_01.pdf = CF_-_STARK_Bonnie_M_Mrs._107625_Scan.pdf")
        notice = ("\n\n[User attached files saved to disk:]\n"
                  "  - /tmp/brain-attachments/s1/att_01.pdf")

        typed, notice_half = _split_attachment_notice(typed_in + name_block + notice)

        # The original name is in the SCANNED half…
        self.assertIn("STARK_Bonnie", typed)
        # …and NOT in the exempt half.
        self.assertNotIn("STARK_Bonnie", notice_half)
        # The exempt half still carries the (now PII-free) path.
        self.assertIn("att_01.pdf", notice_half)

    def test_the_exempt_notice_is_now_pii_free(self):
        from handlers.chat import _split_attachment_notice
        notice = ("\n\n[User attached files saved to disk:]\n"
                  "  - /tmp/brain-attachments/s1/att_01.pdf")
        _typed, notice_half = _split_attachment_notice("Prüfe." + notice)
        # Whatever the exemption now protects, it is no longer a person's name.
        for token in ("STARK", "Bonnie", "107625", "Risikoanalyse"):
            self.assertNotIn(token, notice_half)


if __name__ == "__main__":
    unittest.main()
