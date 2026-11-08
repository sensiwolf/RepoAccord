import unittest

from repoaccord.errors import MessageError
from repoaccord.message import IssueDraft, parse_message


class TestMessage(unittest.TestCase):
    def test_missing_pr_block(self) -> None:
        with self.assertRaisesRegex(MessageError, "PR"):
            parse_message("Subject", ())
        with self.assertRaisesRegex(MessageError, "PR"):
            parse_message("=== PR ===\nbody", ())

    def test_valid_pr_only_and_issue(self) -> None:
        parsed = parse_message("Subject\n\n=== PR ===\nBody", ())
        self.assertEqual(parsed.title, "Subject")
        self.assertIsNone(parsed.issue)
        self.assertEqual(parsed.pr_body, "Body")
        parsed_issue = parse_message(
            "Subject\n=== ISSUE ===\nIssue title\nIssue body\n=== PR ===\nPR body",
            (),
        )
        self.assertEqual(parsed_issue.issue, IssueDraft("Issue title", "Issue body"))

    def test_both_block_orders(self) -> None:
        for text in (
            "Subject\n=== ISSUE ===\nIssue\nBody\n=== PR ===\nPR",
            "Subject\n=== PR ===\nPR\n=== ISSUE ===\nIssue\nBody",
        ):
            with self.subTest(text=text):
                parsed = parse_message(text, ())
                self.assertEqual(parsed.issue, IssueDraft("Issue", "Body"))
                self.assertEqual(parsed.pr_body, "PR")

    def test_duplicate_markers(self) -> None:
        for text in (
            "S\n=== PR ===\na\n=== PR ===\nb",
            "S\n=== ISSUE ===\na\n=== ISSUE ===\nb\n=== PR ===\nc",
        ):
            with self.subTest(text=text), self.assertRaisesRegex(MessageError, "once"):
                parse_message(text, ())

    def test_text_outside_block(self) -> None:
        for text in (
            "S\nnot a block\n=== PR ===\nbody",
            "S\n=== PR ===\nbody\ntrailing",
        ):
            with self.subTest(text=text):
                if text.startswith("S\nnot"):
                    with self.assertRaisesRegex(MessageError, "outside"):
                        parse_message(text, ())
                else:
                    parsed = parse_message(text, ())
                    self.assertEqual(parsed.pr_body, "body\ntrailing")

    def test_crlf_normalization(self) -> None:
        parsed = parse_message("S\r\n=== PR ===\r\nline one\r\nline two", ())
        self.assertEqual(parsed.pr_body, "line one\nline two")

    def test_forbidden_trailers_at_line_start(self) -> None:
        for phrase in ("Co-authored-by", "co-authored-by", "GENERATED WITH"):
            text = f"Subject\n=== PR ===\n  {phrase}: name"
            with self.subTest(phrase=phrase), self.assertRaisesRegex(MessageError, "forbidden"):
                parse_message(text, ("Co-authored-by", "Generated with"))

    def test_forbidden_phrase_mid_line_is_allowed(self) -> None:
        parsed = parse_message(
            "Subject\n=== PR ===\nThis mentions Co-authored-by but is not a trailer",
            ("Co-authored-by",),
        )
        self.assertIn("Co-authored-by", parsed.pr_body)

    def test_subject_and_issue_title_lengths(self) -> None:
        self.assertEqual(len(parse_message("s" * 256 + "\n=== PR ===\nx", ()).title), 256)
        for text in (
            "s" * 257 + "\n=== PR ===\nx",
            " \n=== PR ===\nx",
            "s\n=== ISSUE ===\n" + "i" * 257 + "\n=== PR ===\nx",
        ):
            with self.subTest(length=len(text)), self.assertRaises(MessageError):
                parse_message(text, ())

    def test_body_length_limits(self) -> None:
        parsed = parse_message("S\n=== PR ===\n" + "x" * 65_000, ())
        self.assertEqual(len(parsed.pr_body), 65_000)
        for text in (
            "S\n=== PR ===\n" + "x" * 65_001,
            "S\n=== ISSUE ===\nI\n" + "x" * 65_001 + "\n=== PR ===\np",
        ):
            with self.subTest(length=len(text)), self.assertRaisesRegex(MessageError, "65000"):
                parse_message(text, ())

    def test_empty_pr_and_issue_title(self) -> None:
        for text in (
            "S\n=== PR ===\n \n",
            "S\n=== ISSUE ===\n \n=== PR ===\np",
        ):
            with self.subTest(text=text), self.assertRaises(MessageError):
                parse_message(text, ())

    def test_issue_body_may_be_empty(self) -> None:
        parsed = parse_message("S\n=== ISSUE ===\nTitle\n=== PR ===\nBody", ())
        self.assertEqual(parsed.issue, IssueDraft("Title", ""))

    def test_untrusted_text_is_preserved_as_data(self) -> None:
        subject = 'A ${{ github.event }} `code` $(command) "quoted"'
        body = 'Literal ${{ github.event }} `code` $(command) "quoted"'
        parsed = parse_message(f"{subject}\n=== PR ===\n{body}", ())
        self.assertEqual(parsed.title, subject)
        self.assertEqual(parsed.pr_body, body)
