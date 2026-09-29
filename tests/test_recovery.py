import argparse
import shutil
import unittest
from datetime import date
from pathlib import Path

from main import infer_search_range, parse_iso_date, publish_latest, resolve_dates


class RecoveryDateTests(unittest.TestCase):
    def test_strict_iso_parsing_and_inference(self):
        self.assertEqual(parse_iso_date("2025-05-19"), date(2025, 5, 19))
        for invalid in ("2025-5-19", "05-19-2025", "2025-02-29"):
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_iso_date(invalid)
        self.assertEqual(infer_search_range(date(2025, 5, 19)), (date(2025, 5, 15), date(2025, 5, 16)))
        self.assertEqual(infer_search_range(date(2025, 5, 20)), (date(2025, 5, 16), date(2025, 5, 19)))
        self.assertEqual(infer_search_range(date(2025, 5, 21)), (date(2025, 5, 19), date(2025, 5, 20)))
        self.assertEqual(infer_search_range(date(2025, 5, 22)), (date(2025, 5, 20), date(2025, 5, 21)))
        self.assertEqual(infer_search_range(date(2025, 5, 23)), (date(2025, 5, 21), date(2025, 5, 22)))

    def test_override_and_date_rejections(self):
        today = date(2025, 5, 23)
        with self.assertRaisesRegex(ValueError, "supplied together"):
            resolve_dates(date(2025, 5, 22), date(2025, 5, 20), None, today)
        with self.assertRaisesRegex(ValueError, "cannot be after"):
            resolve_dates(date(2025, 5, 22), date(2025, 5, 21), date(2025, 5, 20), today)
        with self.assertRaisesRegex(ValueError, "future"):
            resolve_dates(date(2025, 5, 24), date(2025, 5, 20), date(2025, 5, 21), today)
        with self.assertRaisesRegex(ValueError, "weekend"):
            resolve_dates(date(2025, 5, 17), None, None, today)
        self.assertEqual(
            resolve_dates(date(2025, 5, 17), date(2025, 5, 15), date(2025, 5, 16), today),
            (date(2025, 5, 15), date(2025, 5, 16)),
        )

    def test_latest_is_only_written_when_requested(self):
        scratch = Path("tests/.scratch-output")
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir()
        dated = scratch / "dated.md"
        latest = scratch / "output.md"
        dated.write_text("recovered")
        latest.write_text("daily")
        publish_latest(dated, scratch, False)
        self.assertEqual(latest.read_text(), "daily")
        publish_latest(dated, scratch, True)
        self.assertEqual(latest.read_text(), "recovered")
        shutil.rmtree(scratch)


if __name__ == "__main__":
    unittest.main()
