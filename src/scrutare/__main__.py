"""Support python -m scrutare with the same exit codes as the console entry point."""

import sys

from scrutare.interfaces.cli import main

sys.exit(main())
