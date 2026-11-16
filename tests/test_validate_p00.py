from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.validate_p00 import ValidationError, validate_repository


class ValidateP00Tests(unittest.TestCase):
    def test_reports_missing_package_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValidationError) as raised:
            validate_repository(Path(directory))

        self.assertIn("missing required file", str(raised.exception))

    def test_accepts_the_repository_walking_skeleton(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]

        report = validate_repository(repository_root)

        self.assertEqual(report.errors, ())
        self.assertGreaterEqual(report.checked_files, 20)


if __name__ == "__main__":
    unittest.main()
