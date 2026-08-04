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


class LogParserContractTests(unittest.TestCase):
    def test_mti_0200_uses_authorization_request_label(self):
        self.assertEqual(mti_label("0200"), "Authorization Request")

    def test_mti_0210_uses_authorization_response_label(self):
        self.assertEqual(mti_label("0210"), "Authorization Response")

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
