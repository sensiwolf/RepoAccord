import unittest

from repoaccord.errors import TemplateError
from repoaccord.templates import (
    BodyCheck,
    Heading,
    Template,
    check_body,
    find_issue_template,
    find_pr_template,
    parse_template,
)


class TestTemplates(unittest.TestCase):
    def test_heading_extraction_ignores_fences_and_comments(self) -> None:
        template = parse_template(
            "## Summary ###\n"
            "```md\n## hidden\n```\n"
            "<!--\n## comment\n-->\n"
            "###   Test   Notes ##\n"
            "~~~~\n# also hidden\n~~~~\n"
        )
        self.assertEqual(
            template.headings,
            (Heading("summary", 2), Heading("test notes", 3)),
        )

    def test_front_matter_labels_string_and_list(self) -> None:
        string_labels = parse_template("---\nlabels: bug, urgent\nother: yes\n---\n## Bug")
        list_labels = parse_template("---\nlabels: [bug, urgent]\n---\n## Bug")
        self.assertEqual(string_labels.labels, ("bug", "urgent"))
        self.assertEqual(list_labels.labels, ("bug", "urgent"))
        self.assertEqual(string_labels.headings, (Heading("bug", 2),))

    def test_front_matter_errors(self) -> None:
        for text in (
            "---\nlabels: [unterminated\n---\n## Title",
            "---\nlabels: 7\n---\n## Title",
            "---\nlabels: [ok, 7]\n---\n## Title",
            "---\nlabels: ok\n## Missing close",
        ):
            with self.subTest(text=text), self.assertRaises(TemplateError):
                parse_template(text)

    def test_check_body_missing_and_empty(self) -> None:
        template = Template((Heading("summary", 2), Heading("testing", 2)), ())
        result = check_body("## Summary\n<!-- only comment -->\n## Testing\nDone", template)
        self.assertEqual(result, BodyCheck((), ("summary",)))
        missing = check_body("## Summary\nText", template)
        self.assertEqual(missing.missing, ("testing",))

    def test_body_sections_end_at_same_or_higher_heading(self) -> None:
        template = parse_template("## Summary\n### Detail\n## Testing")
        complete = check_body("## Summary\n### Detail\ntext\n## Testing\nok", template)
        self.assertEqual(complete.empty, ())
        self.assertIn(
            "summary",
            check_body("## Summary\n### Detail\n## Testing\nok", template).empty,
        )

    def test_pr_template_priority_and_case_insensitivity(self) -> None:
        paths = (
            "docs/PULL_REQUEST_TEMPLATE.md",
            "PULL_REQUEST_TEMPLATE.md",
            ".GITHUB/PULL_REQUEST_TEMPLATE.MD",
        )
        self.assertEqual(find_pr_template(paths), ".GITHUB/PULL_REQUEST_TEMPLATE.MD")
        self.assertEqual(find_pr_template(paths[0:2]), "PULL_REQUEST_TEMPLATE.md")
        self.assertEqual(find_pr_template(("docs/PULL_REQUEST_TEMPLATE.md",)), paths[0])

    def test_pr_template_directory_only_is_unsupported(self) -> None:
        for paths in (
            (".github/PULL_REQUEST_TEMPLATE/bug.md",),
            (".github/PULL_REQUEST_TEMPLATE",),
        ):
            with (
                self.subTest(paths=paths),
                self.assertRaisesRegex(
                    TemplateError,
                    "multiple PR templates",
                ),
            ):
                find_pr_template(paths)
        self.assertIsNone(find_pr_template(()))

    def test_issue_template_one_none_and_multiple(self) -> None:
        folder = ".github/ISSUE_TEMPLATE/"
        self.assertEqual(find_issue_template((folder + "bug.md",), None), folder + "bug.md")
        self.assertIsNone(find_issue_template(("README.md",), None))
        with self.assertRaisesRegex(TemplateError, "set issues.template"):
            find_issue_template((folder + "bug.md", folder + "feature.MD"), None)

    def test_issue_template_wanted_and_missing(self) -> None:
        paths = (".github/ISSUE_TEMPLATE/bug.md", ".github/ISSUE_TEMPLATE/feature.md")
        self.assertEqual(find_issue_template(paths, "BUG.MD"), paths[0])
        with self.assertRaisesRegex(TemplateError, "not found"):
            find_issue_template(paths, "other.md")

    def test_issue_forms(self) -> None:
        folder = ".github/ISSUE_TEMPLATE/"
        with self.assertRaisesRegex(TemplateError, "YAML issue forms"):
            find_issue_template((folder + "bug.yml", folder + "idea.yaml"), None)
        with self.assertRaisesRegex(TemplateError, "YAML issue forms"):
            find_issue_template((folder + "bug.yml",), "bug.md")
        self.assertEqual(
            find_issue_template((folder + "bug.md", folder + "idea.yml"), None),
            folder + "bug.md",
        )

    def test_nested_issue_markdown_is_not_selected(self) -> None:
        self.assertIsNone(find_issue_template((".github/ISSUE_TEMPLATE/nested/bug.md",), None))
