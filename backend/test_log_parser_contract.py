import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(BACKEND_ROOT))

from app.services.log_parser_service import (
    mti_label,
    parse_log_transactions,
    transaction_status,
)
from app.services.log_analysis_agent_service import (
    build_pdf_length_documentation_findings,
    build_field_extraction_response,
    display_options_for_question,
    requested_extraction_fields,
)


class LogParserContractTests(unittest.TestCase):
    def test_requested_field_extraction_detects_generic_field_number(self):
        self.assertEqual(
            requested_extraction_fields(
                "quelles sont les fields 039 trouves dans cette trace ?"
            ),
            ["039"],
        )
        self.assertEqual(
            requested_extraction_fields("que signifie le field 039 ?"),
            [],
        )

    def test_field_extraction_response_returns_observed_values_not_documentation(self):
        transactions = parse_log_transactions(
            "\n".join(
                [
                    "1301 1 2 3|5| Start DumpVisa()",
                    "1301 1 2 3|5| - M.T.I      : [0110]",
                    "1301 1 2 3|5| - FLD (037) : (012) : [601318105410]",
                    "1301 1 2 3|5| - FLD (039) : (002) : [55]",
                    "1301 1 2 3|5| End DumpVisa(OK)",
                ]
            ),
            "trace.txt",
        )

        response = build_field_extraction_response(
            transactions=transactions,
            fields=["039"],
            display_options=display_options_for_question(
                "quelles sont les fields 039 trouves dans cette trace ?"
            ),
        )

        rows = response["sections"][0]["blocks"][0]["rows"]
        self.assertEqual(rows[0]["field"], "039")
        self.assertEqual(rows[0]["value"], "55")
        self.assertEqual(response["references"], [])
        self.assertEqual(response["transactions"], [])

    def test_fixed_length_check_uses_declared_trace_length_and_preserves_spaces(self):
        transactions = parse_log_transactions(
            "\n".join(
                [
                    "1301 1 2 3|5| Start DumpVisa()",
                    "1301 1 2 3|5| - M.T.I      : [0100]",
                    "1301 1 2 3|5| - FLD (043) : (040) : [APPLE.COM/BILL    CORK       IRL]",
                    "1301 1 2 3|5| End DumpVisa(OK)",
                ]
            ),
            "trace.txt",
        )

        self.assertEqual(transactions[0]["field_lengths"]["043"], 40)

        findings = build_pdf_length_documentation_findings(
            transaction=transactions[0],
            field_length_rules={
                "043": {
                    "expected": 40,
                    "expected_condition": "Longueur fixe 40 ANS",
                    "source_reference": {
                        "source": "doc.pdf",
                        "page": 204,
                    },
                    "rule_text": "Field 043 has fixed length 40 ANS.",
                }
            },
        )

        self.assertEqual(findings, [])

    def test_mti_0200_uses_authorization_request_label(self):
        self.assertEqual(mti_label("0200"), "Authorization Request")

    def test_mti_0210_uses_authorization_response_label(self):
        self.assertEqual(mti_label("0210"), "Authorization Response")

    def test_network_management_mtis_are_not_returned_as_transactions(self):
        text = "\n".join(
            [
                "2014 00000001 6| Start DumpVisa()",
                "2014 00000002 6| - M.T.I : [0800]",
                "2014 00000003 6| - FLD (037) (012) [460400004152]",
                "2014 00000004 6| End DumpVisa(OK)",
                "2014 00000005 6| Start DumpVisa()",
                "2014 00000006 6| - M.T.I : [0810]",
                "2014 00000007 6| - FLD (037) (012) [460400004152]",
                "2014 00000008 6| - FLD (039) (002) [00]",
                "2014 00000009 6| End DumpVisa(OK)",
                "2014 00000010 6| Start DumpVisa()",
                "2014 00000011 6| - M.T.I : [0100]",
                "2014 00000012 6| - FLD (037) (012) [432915275372]",
                "2014 00000013 6| End DumpVisa(OK)",
            ]
        )

        transactions = parse_log_transactions(text, "trace.txt")

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["mti"], "0100")
        self.assertEqual(transactions[0]["fields"]["037"], "432915275372")

    def test_response_block_enriches_matching_request_by_rrn(self):
        text = "\n".join(
            [
                "2014 00000001 6| Start DumpVisa()",
                "2014 00000002 6| - M.T.I : [0100]",
                "2014 00000003 6| - FLD (003) (006) [009000]",
                "2014 00000004 6| - FLD (037) (012) [432915275372]",
                "2014 00000005 6| Start GetService()",
                "2014 00000006 6| End GetService(OK)",
                "2014 00000007 6| End DumpVisa(OK)",
                "2014 00000008 6| Start DumpVisa()",
                "2014 00000009 6| - M.T.I : [1110]",
                "2014 00000010 6| - FLD (037) (012) [432915275372]",
                "2014 00000011 6| - FLD (039) (003) [116]",
                "2014 00000012 6| End DumpVisa(OK)",
            ]
        )

        transactions = parse_log_transactions(text, "trace.txt")

        self.assertEqual(transactions[0]["mti"], "0100")
        self.assertEqual(transactions[0]["response_mti"], "1110")
        self.assertEqual(transactions[0]["fields"]["039"], "116")
        self.assertEqual(transactions[0]["status"], "FAILED")
        self.assertEqual(
            transactions[1]["merged_into_transaction_id"],
            transactions[0]["transaction_id"],
        )

    def test_1110_response_enriches_matching_1100_request_by_rrn(self):
        text = "\n".join(
            [
                "1301 18581070 00112610 00112635|4| Start DumpVisa()",
                "1301 18581070 00112610 00112635|4| - M.T.I      : [1100]",
                "1301 18581070 00112610 00112635|4| - FLD (002) : (016) : [5556010000838579]",
                "1301 18581070 00112610 00112635|4| - FLD (003) : (006) : [000000]",
                "1301 18581070 00112610 00112635|4| - FLD (037) : (012) : [601318093092]",
                "1301 18581070 00112610 00112635|4| End DumpVisa(OK)",
                "1301 18581080 00112610 00112635|4| Start DumpVisa()",
                "1301 18581080 00112610 00112635|4| - M.T.I      : [1110]",
                "1301 18581080 00112610 00112635|4| - FLD (037) : (012) : [601318093092]",
                "1301 18581080 00112610 00112635|4| - FLD (039) : (003) : [116]",
                "1301 18581080 00112610 00112635|4| End DumpVisa(OK)",
            ]
        )

        transactions = parse_log_transactions(text, "trace.txt")

        self.assertEqual(transactions[0]["mti"], "1100")
        self.assertEqual(transactions[0]["response_mti"], "1110")
        self.assertEqual(transactions[0]["fields"]["039"], "116")
        self.assertEqual(transactions[0]["status"], "FAILED")
        self.assertEqual(
            transactions[1]["merged_into_transaction_id"],
            transactions[0]["transaction_id"],
        )

    def test_related_requests_with_same_rrn_receive_later_response_code(self):
        text = "\n".join(
            [
                "1301 185809816 00112610 00112627|4| Start DumpVisa()",
                "1301 185809816 00112610 00112627|4| - M.T.I      : [0100]",
                "1301 185809816 00112610 00112627|4| - FLD (037) : (012) : [601318093092]",
                "1301 185809816 00112610 00112627|4| End DumpVisa(OK)",
                "1301 185809820 00112610 00112627|4| Start DumpVisa()",
                "1301 185809820 00112610 00112627|4| - M.T.I      : 1100",
                "1301 185809820 00112610 00112627|4| - FLD (037) : (012) : [601318093092]",
                "1301 185809820 00112610 00112627|4| Start GetOriginalAuthData()",
                "1301 185809820 00112610 00112627|4| End GetOriginalAuthData(NOK, -1)",
                "1301 185809820 00112610 00112627|4| End DumpVisa(OK)",
                "1301 185809984 00112610 00112627|4| Start DumpVisa()",
                "1301 185809984 00112610 00112627|4| - M.T.I      : 1100",
                "1301 185809985 00112610 00112627|4| - FLD (037) : (012) : [601318093092]",
                "1301 185809985 00112610 00112627|4| End DumpVisa(OK)",
                "1301 185810808 00112610 00112635|4| Start DumpVisa()",
                "1301 185810808 00112610 00112635|4| - M.T.I      : 1110",
                "1301 185810808 00112610 00112635|4| - FLD (037) : (012) : [601318093092]",
                "1301 185810808 00112610 00112635|4| - FLD (039) : (003) : [116]",
                "1301 185810808 00112610 00112635|4| End DumpVisa(OK)",
                "1301 185810870 00112610 00112635|4| Start DumpVisa()",
                "1301 185810870 00112610 00112635|4| - M.T.I      : [0110]",
                "1301 185810870 00112610 00112635|4| - FLD (037)   (012)    [601318093092]",
                "1301 185810871 00112610 00112635|4| - FLD (039)   (002)    [51]",
                "1301 185810871 00112610 00112635|4| End DumpVisa(OK)",
            ]
        )

        transactions = parse_log_transactions(text, "trace.txt")

        self.assertEqual(transactions[1]["mti"], "1100")
        self.assertEqual(transactions[1]["fields"]["039"], "51")
        self.assertEqual(transactions[1]["response_mti"], "0110")
        self.assertTrue(transactions[1]["related_response_backfilled"])

    def test_parse_0200_transaction_keeps_same_request_logic(self):
        text = "\n".join(
            [
                "2014 00000001 6| Start DumpVisa()",
                "2014 00000002 6| - M.T.I : [0200]",
                "2014 00000003 6| - FLD (002) (016) [4058361234567713]",
                "2014 00000004 6| - FLD (003) (006) [000000]",
                "2014 00000005 6| - FLD (037) (012) [432915275372]",
                "2014 00000006 6| End DumpVisa(OK)",
            ]
        )

        transactions = parse_log_transactions(text, "trace.txt")

        self.assertEqual(transactions[0]["mti"], "0200")
        self.assertEqual(transactions[0]["message_type"], "Authorization Request")
        self.assertEqual(transactions[0]["fields"]["039"], None)
        self.assertEqual(
            transaction_status(transactions[0]["fields"], transactions[0]["log_story"]),
            "SUCCESS",
        )

    def test_parse_postilion_trc068_transaction_format(self):
        text = "\n".join(
            [
                "0508 122728806 00001 48234696 03599|5| Start DumpPostilion()",
                "0508 122728807 00001 48234696 03599|4| - M.T.I      : [0200]",
                "0508 122728807 00001 48234696 03599|4| - FLD (002) : (016) : [4058361234567713]",
                "0508 122728807 00001 48234696 03599|4| - FLD (003) : (006) : [003000]",
                "0508 122728807 00001 48234696 03599|4| - FLD (037) : (012) : [001185898676]",
                "0508 122728808 00001 48234696 03599|5| End   DumpPostilion()",
            ]
        )

        transactions = parse_log_transactions(text, "POST_POS_1.TRC068")

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["mti"], "0200")
        self.assertEqual(transactions[0]["message_type"], "Authorization Request")
        self.assertEqual(transactions[0]["fields"]["003"], "003000")
        self.assertEqual(transactions[0]["fields"]["037"], "001185898676")
        self.assertEqual(transactions[0]["fields"]["039"], None)
        self.assertEqual(transactions[0]["status"], "SUCCESS")

    def test_parse_dump_iso_response_code_000_as_success(self):
        text = "\n".join(
            [
                "0508 122718646 00001 48234696 03599|5| Start DumpIso()",
                "0508 122718646 00001 48234696 03599|4| - M.T.I      : 1210",
                "0508 122718647 00001 48234696 03599|4| - FLD (002) : (016) : [******]",
                "0508 122718647 00001 48234696 03599|4| - FLD (003) : (006) : [003000]",
                "0508 122718647 00001 48234696 03599|4| - FLD (037) : (012) : [001185898559]",
                "0508 122718647 00001 48234696 03599|4| - FLD (039) : (003) : [000]",
                "0508 122718648 00001 48234696 03599|5| End   DumpIso()",
            ]
        )

        transactions = parse_log_transactions(text, "POST_POS_1.TRC068")

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["mti"], "1210")
        self.assertEqual(transactions[0]["fields"]["039"], "000")
        self.assertEqual(transactions[0]["status"], "SUCCESS")

    def test_parser_keeps_additional_iso_fields_for_compliance_checks(self):
        text = "\n".join(
            [
                "2014 00000001 6| Start DumpVisa()",
                "2014 00000002 6| - M.T.I : [0110]",
                "2014 00000003 6| - FLD (037) (012) [432915275372]",
                "2014 00000004 6| - FLD (039) (002) [00]",
                "2014 00000005 6| - FLD (044) (006) [123456]",
                "2014 00000006 6| End DumpVisa(OK)",
            ]
        )

        transactions = parse_log_transactions(text, "trace.txt")

        self.assertEqual(transactions[0]["fields"]["044"], "123456")

    def test_function_return_zero_with_empty_second_arg_is_ok(self):
        text = "\n".join(
            [
                "2014 00000001 6| Start DumpVisa()",
                "2014 00000002 6| - M.T.I : [0100]",
                "2014 00000003 6| Start InsertionAuthoActivity ()",
                "2014 00000004 6| End InsertionAuthoActivity (0, )",
                "2014 00000005 6| End DumpVisa(OK)",
            ]
        )

        transactions = parse_log_transactions(text, "trace.txt")
        log_story = transactions[0]["log_story"]

        insertion_step = next(
            item
            for item in log_story
            if item["function_name"] == "InsertionAuthoActivity"
        )

        self.assertEqual(insertion_step["status"], "OK")
        self.assertEqual(transactions[0]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
