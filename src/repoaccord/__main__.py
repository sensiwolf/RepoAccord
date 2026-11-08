import os
import sys

MESSAGE = "RepoAccord is pre-alpha: no behaviour is implemented yet."

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from repoaccord.cli import main

        raise SystemExit(main())
    print(MESSAGE)
    raise SystemExit(0 if os.environ.get("REPOACCORD_DRY_RUN") == "true" else 1)
