"""Lets you run the tool as `python -m kit` without installing it."""

import sys

from .cli import main

sys.exit(main())
