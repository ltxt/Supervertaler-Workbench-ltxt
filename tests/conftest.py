"""Shared pytest configuration for the Supervertaler test suite.

Qt must be told to use the offscreen platform plugin *before* any PyQt6 module
is imported, or constructing a QApplication on a machine with no display fails.
Setting it here covers the whole session regardless of which test module pytest
happens to import first, so individual tests do not have to remember.

setdefault, not assignment: a developer running the suite on a real desktop can
export QT_QPA_PLATFORM themselves (or set it to "xcb") to watch widgets appear.
"""

import ast
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Let tests import `modules.*` when pytest is invoked from anywhere.
sys.path.insert(0, REPO_ROOT)

SUPERVERTALER_PY = os.path.join(REPO_ROOT, "Supervertaler.py")


def load_supervertaler_symbols(names, namespace=None):
    """Execute selected top-level definitions from Supervertaler.py in isolation.

    Importing Supervertaler.py would construct the whole application, so a test
    that needs to exercise one of its module-level functions locates that
    function in the AST and executes just it. The code under test is the real
    shipped code — only its surroundings are stubbed.

    Args:
        names: Names of top-level functions and/or assignments to load.
        namespace: Optional dict pre-populated with whatever the loaded code
            references (module aliases, stand-in classes). Used as the globals
            for the executed definitions.

    Returns:
        The namespace, now containing the requested symbols.

    Raises:
        AssertionError: if any requested name is not found, so a rename in
            Supervertaler.py fails the test loudly instead of silently
            skipping coverage.
    """
    ns = {} if namespace is None else namespace
    source = open(SUPERVERTALER_PY, encoding="utf-8").read()
    lines = source.splitlines(keepends=True)
    wanted = set(names)
    found = set()

    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            hit = node.name if node.name in wanted else None
        elif isinstance(node, ast.Assign):
            hit = next((t.id for t in node.targets
                        if isinstance(t, ast.Name) and t.id in wanted), None)
        else:
            hit = None
        if hit is None:
            continue
        segment = "".join(lines[node.lineno - 1:node.end_lineno])
        exec(compile(segment, "Supervertaler.py", "exec"), ns)
        found.add(hit)

    missing = wanted - found
    assert not missing, f"not found at top level of Supervertaler.py: {missing}"
    return ns
