import unittest

from eba_pipeline.crawler.discovery import is_current_applicable_candidate


class CurrentApplicableDiscoveryTests(unittest.TestCase):
    def test_accepts_final_guidelines(self) -> None:
        self.assertTrue(
            is_current_applicable_candidate(
                "Final Guidelines on the management of ESG risks",
                "https://www.eba.europa.eu/sites/default/files/2025-01/final-guidelines.pdf",
                "guidelines",
            )
        )

    def test_accepts_consolidated_guidelines_even_when_amending_appears(self) -> None:
        self.assertTrue(
            is_current_applicable_candidate(
                "Consolidated version of EBA amending Guidelines on ICT and security risk management",
                "https://www.eba.europa.eu/sites/default/files/2026-05/consolidated-guidelines.pdf",
                "guidelines",
            )
        )

    def test_rejects_consultations_drafts_and_amending_only_documents(self) -> None:
        cases = [
            ("Consultation Paper on Guidelines on internal governance", "guidelines"),
            ("Draft RTS on own funds requirements", "rts"),
            ("Guidelines amending Guidelines on equivalence of confidentiality regimes", "guidelines"),
            ("Annex 2 Instructions", "its"),
            ("ESAs Guidelines on templates for explanations and opinions", "guidelines"),
            ("Final Report on Guidelines", "consultation-paper"),
        ]
        for title, document_type in cases:
            with self.subTest(title=title, document_type=document_type):
                self.assertFalse(
                    is_current_applicable_candidate(
                        title,
                        "https://www.eba.europa.eu/sites/default/files/example.pdf",
                        document_type,
                    )
                )

    def test_accepts_final_draft_rts(self) -> None:
        cases = [
            "Final report on draft RTS on IRB material model changes",
            "Final draft RTS on Structural FX",
            "Final draft Regulatory Technical Standards on cooperation and colleges",
        ]
        for title in cases:
            with self.subTest(title=title):
                self.assertTrue(
                    is_current_applicable_candidate(
                        title,
                        "https://www.eba.europa.eu/sites/default/files/example.pdf",
                        "rts",
                    )
                )


if __name__ == "__main__":
    unittest.main()
