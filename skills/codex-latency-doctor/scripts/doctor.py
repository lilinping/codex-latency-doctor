"""Launch the CLI from the installed skill directory."""

import sys

if __name__ == "__main__":
    if sys.version_info < (3, 11):
        sys.exit("Codex Latency Doctor requires Python 3.11 or newer.")

    from codex_latency_doctor.__main__ import main

    sys.exit(main())
