from unittest import TestCase

from repoaccord import __version__


class TestSmoke(TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "0.0.0")
