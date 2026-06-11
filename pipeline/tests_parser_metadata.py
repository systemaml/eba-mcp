# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
import unittest
from collections.abc import Mapping
from typing import cast

from eba_pipeline.parser.metadata import (
    CHUNK_TYPE_VALUES,
    DOCUMENT_REGION_VALUES,
    ChunkType,
    DocumentRegion,
    build_toc_entries,
    make_parser_chunk,
)
from eba_pipeline.parser.paragraphize import (
    PageData,
    paragraphize_document,
    parent_section_ref,
    section_level,
)

EXPECTED_TOC_KEYS = {
    "document_version_id",
    "section_ref",
    "title",
    "level",
    "parent_section_ref",
    "page_start",
    "page_end",
    "sequence_start",
    "sequence_end",
    "confidence",
    "source",
}


def page(page_no: int, text: str) -> PageData:
    return cast(
        PageData,
        cast(
            object,
            {
                "page_no": page_no,
                "text": text,
                "extraction_method": "synthetic",
                "char_count": len(text),
            },
        ),
    )


class ParserMetadataScaffoldingTests(unittest.TestCase):
    def test_chunk_type_enum_values_have_synthetic_page_fixtures(self) -> None:
        fixtures = {
            ChunkType.PARAGRAPH: page(1, "1. Institutions shall identify ML/TF risk factors."),
            ChunkType.HEADING: page(2, "4. Customer due diligence"),
            ChunkType.TABLE: page(3, "Table 1: Risk category | Weight"),
            ChunkType.ANNEX: page(4, "Annex I\nRisk factor indicators"),
            ChunkType.FOOTNOTE: page(5, "1 EBA/GL/2021/02, paragraph 12."),
        }

        self.assertEqual(set(CHUNK_TYPE_VALUES), {item.value for item in ChunkType})
        for sequence_no, (chunk_type, fixture) in enumerate(fixtures.items()):
            chunk = make_parser_chunk(
                fixture,
                "EBA/GL/2022/05",
                sequence_no=sequence_no,
                chunk_type=chunk_type,
                document_region=DocumentRegion.BODY,
                paragraph_ref=str(sequence_no + 1),
            )
            self.assertEqual(chunk["chunk_type"], chunk_type.value)
            self.assertIn(f":{chunk_type.value[0]}:", cast(str, chunk["chunk_id"]))

    def test_document_region_enum_values_are_preserved_on_chunks(self) -> None:
        fixtures = {
            DocumentRegion.FRONT_MATTER: page(1, "EBA/GL/2022/05\nStatus: Final"),
            DocumentRegion.BODY: page(3, "1. These guidelines apply from 2023."),
            DocumentRegion.ANNEX: page(40, "Annex I\nTemplate"),
            DocumentRegion.BACK_MATTER: page(50, "Repeal\nThese guidelines replace earlier guidance."),
            DocumentRegion("consultation_feedback"): page(60, "Feedback on the public consultation"),
        }

        self.assertEqual(set(DOCUMENT_REGION_VALUES), {item.value for item in DocumentRegion})
        for sequence_no, (region, fixture) in enumerate(fixtures.items()):
            chunk = make_parser_chunk(
                fixture,
                "EBA/GL/2022/05",
                sequence_no=sequence_no,
                chunk_type=ChunkType.PARAGRAPH,
                document_region=region,
                paragraph_ref=str(sequence_no + 1),
            )
            self.assertEqual(chunk["document_region"], region.value)

    def test_section_helpers_encode_hierarchy(self) -> None:
        self.assertEqual(section_level("4.7.2"), 3)
        self.assertEqual(parent_section_ref("4.7.2"), "4.7")
        self.assertIsNone(parent_section_ref("4"))
        self.assertIsNone(section_level(None))

    def test_toc_schema_generation_from_synthetic_chunks(self) -> None:
        chunks = [
            make_parser_chunk(
                page(10, "4. Customer due diligence"),
                "EBA/GL/2022/05",
                sequence_no=0,
                chunk_type=ChunkType.HEADING,
                document_region=DocumentRegion.BODY,
                section_ref="4",
                section_title="4. Customer due diligence",
            ),
            make_parser_chunk(
                page(11, "4.7. Enhanced due diligence"),
                "EBA/GL/2022/05",
                sequence_no=1,
                chunk_type=ChunkType.HEADING,
                document_region=DocumentRegion.BODY,
                section_ref="4.7",
                section_title="4.7. Enhanced due diligence",
            ),
            make_parser_chunk(
                page(12, "4.7.1 Firms shall apply enhanced measures."),
                "EBA/GL/2022/05",
                sequence_no=2,
                chunk_type=ChunkType.PARAGRAPH,
                document_region=DocumentRegion.BODY,
                paragraph_ref="4.7.1",
                section_ref="4.7",
                section_title="4.7. Enhanced due diligence",
                metadata_confidence=0.9,
            ),
        ]

        toc = build_toc_entries(cast(list[Mapping[str, object]], chunks), document_version_id=42)

        self.assertEqual(len(toc), 2)
        self.assertEqual(set(toc[0].keys()), EXPECTED_TOC_KEYS)
        self.assertEqual(toc[0]["section_ref"], "4")
        self.assertEqual(toc[0]["level"], 1)
        self.assertIsNone(toc[0]["parent_section_ref"])
        self.assertEqual(toc[1]["section_ref"], "4.7")
        self.assertEqual(toc[1]["parent_section_ref"], "4")
        self.assertEqual(toc[1]["sequence_start"], 1)
        self.assertEqual(toc[1]["sequence_end"], 2)
        self.assertEqual(toc[1]["page_start"], 11)
        self.assertEqual(toc[1]["page_end"], 12)

    def test_gl_2022_05_hierarchy_transition_from_4_1_5_to_4_2(self) -> None:
        chunks = paragraphize_document(
            [
                page(
                    4,
                    "\n".join(
                        [
                            "4. General provisions",
                            "4.1 Business-wide risk assessment",
                            "4.1.5 Risk factors",
                            "Institutions should record the assessment under section 4.1.5.",
                            "4.2 Individual risk assessments",
                            (
                                "88. Institutions should identify the ML/TF risk associated with a business relationship, "
                                "occasional transaction, customer profile, product, delivery channel, and geographic exposure."
                            ),
                        ]
                    ),
                )
            ],
            "EBA/GL/2022/05",
        )

        section_4_2 = next(chunk for chunk in chunks if chunk["text"].startswith("4.2 "))
        self.assertEqual(section_4_2["section_ref"], "4.2")
        self.assertEqual(section_4_2["parent_section_ref"], "4")
        self.assertEqual(section_4_2["section_level"], 2)
        self.assertNotIn("4.1.5 Risk factors", section_4_2["section_path"])

        inherited_4_2 = next(
            chunk for chunk in chunks if str(chunk["text"]).startswith("88. Institutions should identify the ML/TF risk")
        )
        self.assertEqual(inherited_4_2["section_ref"], "4.2")
        self.assertEqual(inherited_4_2["parent_section_ref"], "4")
        self.assertNotIn("4.1.5 Risk factors", inherited_4_2["section_path"])

    def test_numbered_paragraph_under_section_is_not_promoted_to_heading(self) -> None:
        chunks = paragraphize_document(
            [
                page(
                    8,
                    "\n".join(
                        [
                            "4.1.5 Risk factors",
                            (
                                "87. Institutions should identify and assess the risk factors associated with customers, "
                                "countries, products, services, transactions, delivery channels, and business relationships "
                                "before applying mitigating measures."
                            ),
                        ]
                    ),
                )
            ],
            "EBA/GL/2022/05",
        )

        paragraph = next(chunk for chunk in chunks if chunk["paragraph_ref"] == "87")
        self.assertEqual(paragraph["chunk_type"], ChunkType.PARAGRAPH.value)
        self.assertEqual(paragraph["section_ref"], "4.1.5")
        self.assertEqual(paragraph["section_title"], "4.1.5 Risk factors")

        toc_refs = {
            entry["section_ref"]
            for entry in build_toc_entries(cast(list[Mapping[str, object]], chunks), document_version_id=42)
        }
        self.assertIn("4.1.5", toc_refs)
        self.assertNotIn("87", toc_refs)

    def test_front_matter_boilerplate_is_not_promoted_to_toc_heading(self) -> None:
        chunks = paragraphize_document(
            [
                page(
                    1,
                    "\n".join(
                        [
                            "Final report",
                            "EBA/GL/2022/05",
                            "These guidelines are issued pursuant to Article 16 of Regulation (EU) No 1093/2010.",
                        ]
                    ),
                ),
                page(
                    4,
                    "\n".join(
                        [
                            "1. Subject matter",
                            "1. These guidelines apply to institutions and competent authorities.",
                        ]
                    ),
                ),
            ],
            "EBA/GL/2022/05",
        )

        front_matter = next(chunk for chunk in chunks if "Final report" in str(chunk["text"]))
        self.assertEqual(front_matter["document_region"], DocumentRegion.FRONT_MATTER.value)
        self.assertEqual(front_matter["chunk_type"], ChunkType.FRONT_MATTER.value)
        self.assertIsNone(front_matter["section_ref"])

        toc_titles = {
            entry["title"] for entry in build_toc_entries(cast(list[Mapping[str, object]], chunks), document_version_id=42)
        }
        self.assertNotIn("Final report", toc_titles)
        self.assertIn("1. Subject matter", toc_titles)
