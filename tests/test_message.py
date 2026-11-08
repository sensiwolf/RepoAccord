import unittest

from repoaccord.config import load_config
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
        self.assertEqual(parsed.trailers, ())
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

    def test_markers_accept_trailing_whitespace(self) -> None:
        parsed = parse_message(
            "  Subject title  \n=== ISSUE === \t\n Issue title \t\n"
            "Issue body\n=== PR ===   \n## Summary\nBody",
            (),
        )
        self.assertEqual(parsed.title, "Subject title")
        self.assertEqual(parsed.issue, IssueDraft("Issue title", "Issue body"))
        self.assertEqual(parsed.pr_body, "## Summary\nBody")

    def test_titles_are_stripped_before_return(self) -> None:
        parsed = parse_message(
            "  Subject  \n=== ISSUE ===\n  Issue title \n=== PR ===\nBody",
            (),
        )
        self.assertEqual(parsed.title, "Subject")
        self.assertEqual(parsed.issue, IssueDraft("Issue title", ""))

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

    def test_trailers_are_allowed_by_default_but_can_be_forbidden(self) -> None:
        default = load_config(None)
        parsed = parse_message(
            "Subject\n=== PR ===\nBody\n\nCo-authored-by: Test Person <test@example.invalid>",
            default.trailers.forbid,
        )
        self.assertEqual(parsed.trailers, ("Co-authored-by: Test Person <test@example.invalid>",))
        generated = parse_message(
            "Subject\n=== PR ===\nGenerated with: a tool",
            default.trailers.forbid,
        )
        self.assertEqual(generated.pr_body, "Generated with: a tool")
        for phrase in ("Co-authored-by", "Generated with"):
            config = load_config(f'trailers:\n  forbid: ["{phrase}"]')
            with self.subTest(phrase=phrase), self.assertRaisesRegex(MessageError, "forbidden"):
                parse_message(
                    f"Subject\n=== PR ===\nBody\n\n{phrase}: a value",
                    config.trailers.forbid,
                )

    def test_supported_final_trailer_paragraph_is_removed_from_pr_body(self) -> None:
        parsed = parse_message(
            "Subject\n=== PR ===\nPR body\n\nCo-authored-by: Test Person <test@example.invalid>",
            (),
        )
        self.assertEqual(parsed.pr_body, "PR body")
        self.assertEqual(
            parsed.trailers,
            ("Co-authored-by: Test Person <test@example.invalid>",),
        )

    def test_multiple_supported_final_trailers_are_removed(self) -> None:
        parsed = parse_message(
            "Subject\n=== PR ===\nPR body\n\n"
            "Co-authored-by: Test Person <test@example.invalid>\n"
            "Signed-off-by: Another Person <another@example.invalid>",
            (),
        )
        self.assertEqual(parsed.pr_body, "PR body")
        self.assertEqual(
            parsed.trailers,
            (
                "Co-authored-by: Test Person <test@example.invalid>",
                "Signed-off-by: Another Person <another@example.invalid>",
            ),
        )

    def test_unrecognized_or_mixed_final_paragraph_stays_in_body(self) -> None:
        for paragraph in (
            "Note: keep this",
            "Note: keep this\nCo-authored-by: Test Person <test@example.invalid>",
        ):
            with self.subTest(paragraph=paragraph):
                parsed = parse_message(f"Subject\n=== PR ===\nPR body\n\n{paragraph}", ())
                self.assertEqual(parsed.pr_body, f"PR body\n\n{paragraph}")
                self.assertEqual(parsed.trailers, ())

    def test_trailer_is_removed_from_last_issue_block(self) -> None:
        parsed = parse_message(
            "Subject\n=== PR ===\nPR body\n=== ISSUE ===\nIssue title\nIssue body\n\n"
            "Co-authored-by: Test Person <test@example.invalid>",
            (),
        )
        self.assertEqual(parsed.pr_body, "PR body")
        self.assertEqual(parsed.issue, IssueDraft("Issue title", "Issue body"))
        self.assertEqual(
            parsed.trailers,
            ("Co-authored-by: Test Person <test@example.invalid>",),
        )

    def test_trailer_only_pr_body_is_refused_as_blank(self) -> None:
        with self.assertRaisesRegex(MessageError, "pull request body must not be blank"):
            parse_message(
                "Subject\n=== PR ===\n\nCo-authored-by: Test Person <test@example.invalid>",
                (),
            )

    def test_signed_off_by_is_case_insensitive(self) -> None:
        parsed = parse_message(
            "Subject\n=== PR ===\nPR body\n\nsIgNeD-oFf-bY: Test Person <test@example.invalid>",
            (),
        )
        self.assertEqual(parsed.pr_body, "PR body")
        self.assertEqual(
            parsed.trailers,
            ("sIgNeD-oFf-bY: Test Person <test@example.invalid>",),
        )

    def test_trailer_looking_line_in_middle_of_body_is_preserved(self) -> None:
        body = "First paragraph\nCo-authored-by: Test Person <test@example.invalid>\nLast paragraph"
        parsed = parse_message(f"Subject\n=== PR ===\n{body}", ())
        self.assertEqual(parsed.pr_body, body)
        self.assertEqual(parsed.trailers, ())

    def test_crlf_trailer_paragraph_is_removed(self) -> None:
        parsed = parse_message(
            "Subject\r\n=== PR ===\r\nPR body\r\n\r\n"
            "Co-authored-by: Test Person <test@example.invalid>\r\n",
            (),
        )
        self.assertEqual(parsed.pr_body, "PR body")
        self.assertEqual(
            parsed.trailers,
            ("Co-authored-by: Test Person <test@example.invalid>",),
        )

    def test_body_length_is_checked_after_trailer_removal(self) -> None:
        trailer = "Co-authored-by: " + "x" * 65_001
        parsed = parse_message(f"S\n=== PR ===\n{'x' * 65_000}\n\n{trailer}", ())
        self.assertEqual(len(parsed.pr_body), 65_000)
        self.assertEqual(parsed.trailers, (trailer,))

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
