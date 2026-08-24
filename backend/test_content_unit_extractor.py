import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.content_unit_service import ContentUnitExtractor
from app.services.document_service import store_content_units_best_effort


DOCUMENT = {
    "_id": "doc-1",
    "conversation_id": "conversation-1",
    "agent": "documentation",
    "original_filename": "technical-spec.pdf",
}


def section(
    text,
    *,
    heading=None,
    field_number=None,
    content_type=None,
    page=12,
    section_index=1,
    chunk_index=0,
):
    item = {
        "_id": ObjectId(),
        "document_id": "doc-1",
        "section_index": section_index,
        "chunk_index": chunk_index,
        "text": text,
        "heading": heading,
        "page": page,
        "page_document": "2-3",
    }

    if field_number:
        item["field_number"] = field_number

    if content_type:
        item["content_type"] = content_type

    return item


class ContentUnitExtractorTests(unittest.TestCase):
    def content_types(self, units):
        return [unit["content_type"] for unit in units]

    def test_paragraph_simple_becomes_paragraph(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "This paragraph describes the authorization process.",
                )
            ],
            document=DOCUMENT,
        )

        self.assertIn("paragraph", self.content_types(units))

    def test_heading_context_is_preserved_without_being_the_only_signal(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "This field contains a response code used by the system.",
                    heading="Chapter 4 > 4.27 Field 39 Response Code",
                    field_number="039",
                )
            ],
            document=DOCUMENT,
        )

        paragraph_unit = next(
            unit
            for unit in units
            if unit["content_type"] == "field_description"
        )

        self.assertEqual(
            paragraph_unit["hierarchy"]["chapter"],
            "Chapter 4",
        )
        self.assertEqual(
            paragraph_unit["hierarchy"]["section"],
            "4.27",
        )
        self.assertEqual(
            paragraph_unit["entities"]["field_number"],
            "039",
        )

    def test_table_simple_creates_table_and_rows(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "Code | Meaning\n51 | Insufficient funds\n54 | Expired card",
                    heading="Response codes",
                    field_number="039",
                )
            ],
            document=DOCUMENT,
        )
        types = self.content_types(units)

        self.assertIn("table", types)
        self.assertEqual(types.count("table_row"), 2)

    def test_table_with_reliable_mapping_creates_code_mapping(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "Code | Meaning\n51 | Insufficient funds",
                    heading="Response codes",
                    field_number="039",
                )
            ],
            document=DOCUMENT,
        )
        mappings = [
            unit
            for unit in units
            if unit["content_type"] == "code_mapping"
        ]

        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["entities"]["code"], "51")
        self.assertEqual(mappings[0]["content"], "Insufficient funds")
        self.assertTrue(mappings[0]["parent_unit_id"])

    def test_ambiguous_table_does_not_create_code_mapping(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "Year | Total\n2024 | 30",
                    heading="Statistics",
                )
            ],
            document=DOCUMENT,
        )

        self.assertNotIn("code_mapping", self.content_types(units))
        self.assertIn("table_row", self.content_types(units))

    def test_hyphen_line_does_not_create_code_mapping(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "51 - Insufficient funds",
                    heading="Response codes",
                    field_number="039",
                )
            ],
            document=DOCUMENT,
        )

        self.assertNotIn("code_mapping", self.content_types(units))

    def test_explicit_colon_mapping_creates_code_mapping(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "51: Insufficient funds",
                    heading="Response codes",
                    field_number="039",
                )
            ],
            document=DOCUMENT,
        )
        mappings = [
            unit
            for unit in units
            if unit["content_type"] == "code_mapping"
        ]

        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["entities"]["code"], "51")

    def test_front_matter_does_not_extract_primary_field_number(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "4.27 Field 39 Response Code........................4-82",
                    heading="Table of Contents",
                    page=6,
                )
            ],
            document=DOCUMENT,
        )

        for unit in units:
            self.assertEqual(unit["document_zone"], "front_matter")
            self.assertNotIn("field_number", unit["entities"])
            self.assertEqual(unit["primary_entities"], {})

    def test_primary_and_referenced_entities_are_separated(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "Field 7 and Field 11 can be used with Field 32.",
                    heading="Chapter 1 > 1.1 Key Data Fields",
                )
            ],
            document=DOCUMENT,
        )
        content_unit = next(
            unit
            for unit in units
            if unit["content_type"] != "heading"
        )

        self.assertNotIn("field_number", content_unit["primary_entities"])
        self.assertEqual(
            content_unit["referenced_entities"]["field_numbers"],
            ["007", "011", "032"],
        )

    def test_structural_subsections_are_split(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "4.27.1 Attributes\nfixed length 2 AN\n"
                    "4.27.2 Description\nField 39 contains a response code.",
                    heading="Chapter 4 > 4.27 Field 39 Response Code",
                )
            ],
            document=DOCUMENT,
        )
        titles = [
            unit["hierarchy"]["title"]
            for unit in units
            if unit["content_type"] != "heading"
        ]

        self.assertIn("4.27.1 Attributes", titles)
        self.assertIn("4.27.2 Description", titles)

    def test_flattened_table_creates_rows_and_strict_mappings(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(
                    "Table 1 Test Codes\n"
                    "Code\n"
                    "Definition\n"
                    "00\n"
                    "Successful approval\n"
                    "01\n"
                    "Refer to issuer\n"
                    "51\n"
                    "Insufficient funds\n",
                    heading="Chapter 4 > 4.27 Field 39 Response Code",
                    field_number="039",
                )
            ],
            document=DOCUMENT,
        )
        types = self.content_types(units)
        mappings = [
            unit
            for unit in units
            if unit["content_type"] == "code_mapping"
        ]

        self.assertIn("table", types)
        self.assertEqual(types.count("table_row"), 3)
        self.assertEqual(
            [mapping["entities"]["code"] for mapping in mappings],
            ["00", "01", "51"],
        )

    def test_note_is_detected_from_content(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section("NOTE This value is not returned in responses.")
            ],
            document=DOCUMENT,
        )

        self.assertIn("note", self.content_types(units))

    def test_example_is_detected_from_content(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section("Example: 0100 authorization request.")
            ],
            document=DOCUMENT,
        )

        self.assertIn("example", self.content_types(units))

    def test_unidentifiable_content_becomes_unknown(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(":::: -----")
            ],
            document=DOCUMENT,
        )

        self.assertIn("unknown", self.content_types(units))

    def test_parsing_confidence_is_bounded(self):
        units = ContentUnitExtractor.extract(
            sections=[
                section(":::: -----")
            ],
            document=DOCUMENT,
        )

        for unit in units:
            self.assertGreaterEqual(unit["parsing_confidence"], 0)
            self.assertLessEqual(unit["parsing_confidence"], 1)


class FakeContentUnitsCollection:
    def __init__(self):
        self.deleted = 0
        self.inserted_batches = []

    async def delete_many(self, query):
        self.deleted += 1

    async def insert_many(self, documents):
        self.inserted_batches.append(documents)


class ContentUnitIngestionTests(unittest.TestCase):
    def test_extractor_error_does_not_escape_best_effort_storage(self):
        fake_collection = FakeContentUnitsCollection()

        async def run():
            with patch(
                "app.services.document_service.document_content_units_collection",
                fake_collection,
            ), patch(
                "app.services.document_service.ContentUnitExtractor.extract",
                side_effect=RuntimeError("boom"),
            ), patch(
                "app.services.document_service.logger.exception",
            ):
                return await store_content_units_best_effort(
                    document_id=ObjectId(),
                    section_documents=[section("text")],
                    document=DOCUMENT,
                    fallback_filename="technical-spec.pdf",
                )

        count = asyncio.run(run())

        self.assertEqual(count, 0)
        self.assertEqual(fake_collection.deleted, 2)
        self.assertEqual(fake_collection.inserted_batches, [])

    def test_double_ingestion_replaces_units_without_duplicates(self):
        fake_collection = FakeContentUnitsCollection()

        async def run():
            with patch(
                "app.services.document_service.document_content_units_collection",
                fake_collection,
            ):
                first = await store_content_units_best_effort(
                    document_id=ObjectId("64f000000000000000000001"),
                    section_documents=[section("Code | Meaning\n51 | Insufficient funds")],
                    document=DOCUMENT,
                    fallback_filename="technical-spec.pdf",
                )
                second = await store_content_units_best_effort(
                    document_id=ObjectId("64f000000000000000000001"),
                    section_documents=[section("Code | Meaning\n51 | Insufficient funds")],
                    document=DOCUMENT,
                    fallback_filename="technical-spec.pdf",
                )
                return first, second

        first, second = asyncio.run(run())

        self.assertEqual(first, second)
        self.assertEqual(fake_collection.deleted, 2)
        self.assertEqual(len(fake_collection.inserted_batches), 2)


if __name__ == "__main__":
    unittest.main()
