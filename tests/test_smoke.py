import os
import subprocess
import sys
from unittest import TestCase

from repoaccord import __version__


class TestSmoke(TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "0.0.0")

    def test_main_dry_run_and_fail_safe(self) -> None:
        for dry_run, expected_code in (("true", 0), ("false", 1)):
            with self.subTest(dry_run=dry_run):
                env = {"REPOACCORD_DRY_RUN": dry_run}
                system_root = os.environ.get("SYSTEMROOT")
                if system_root:
                    env["SYSTEMROOT"] = system_root

                result = subprocess.run(
                    [sys.executable, "-m", "repoaccord"],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

                self.assertEqual(result.returncode, expected_code)
                self.assertEqual(
                    result.stdout.strip(),
                    "RepoAccord is pre-alpha: no behaviour is implemented yet.",
                )
                self.assertEqual(result.stderr, "")
