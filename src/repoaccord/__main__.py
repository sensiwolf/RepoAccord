import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "plan":
        from repoaccord.cli import main

        raise SystemExit(main())

    from repoaccord.github_api import UrllibTransport
    from repoaccord.run import run_from_environment

    raise SystemExit(run_from_environment(None, UrllibTransport()))
