import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from repoaccord.cli import main


class TestCli(unittest.TestCase):
    def test_plan_fixtures_match_exact_sorted_json(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        expected = {
            "plan-act.json": (
                0,
                '{"actions": [{"base": "main", "body_template": "## Summary\\nAdds report.'
                '\\n\\n## Testing\\nNot run.", "head": "feat/report", "placeholder": '
                '"#<issue number>", "title": "Add report", "type": "CreatePullRequest"}], '
                '"decision": "act", "notes": [], "reason": "request is authorized and valid"}\n',
            ),
            "plan-refuse.json": (
                1,
                '{"actions": [], "decision": "refuse", "notes": [], "reason": '
                '"permission could not be verified"}\n',
            ),
        }
        for filename, (expected_code, expected_output) in expected.items():
            with self.subTest(filename=filename):
                output = io.StringIO()
                error = io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    code = main(["plan", "--input", str(fixtures / filename)])
                self.assertEqual(code, expected_code)
                self.assertEqual(output.getvalue(), expected_output)
                self.assertEqual(error.getvalue(), "")
                self.assertEqual(json.loads(output.getvalue()), json.loads(expected_output))

    def test_invalid_input_returns_two_without_json_output(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(["plan", "--input", "this-file-does-not-exist.json"])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("invalid plan input", error.getvalue())


if __name__ == "__main__":
    unittest.main()
