import os

MESSAGE = "RepoAccord is pre-alpha: no behaviour is implemented yet."

if __name__ == "__main__":
    print(MESSAGE)
    raise SystemExit(0 if os.environ.get("REPOACCORD_DRY_RUN") == "true" else 1)
