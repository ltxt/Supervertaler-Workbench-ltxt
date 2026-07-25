"""Shared pytest configuration for the Supervertaler test suite.

Qt must be told to use the offscreen platform plugin *before* any PyQt6 module
is imported, or constructing a QApplication on a machine with no display fails.
Setting it here covers the whole session regardless of which test module pytest
happens to import first, so individual tests do not have to remember.

setdefault, not assignment: a developer running the suite on a real desktop can
export QT_QPA_PLATFORM themselves (or set it to "xcb") to watch widgets appear.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Let tests import `modules.*` when pytest is invoked from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
