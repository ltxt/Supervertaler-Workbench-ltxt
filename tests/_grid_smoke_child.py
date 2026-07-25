"""Child process for the headless grid smoke test.

Run by tests/test_grid_smoke.py, never imported by it. Constructs the real
application window, loads a project of tagged segments, and prints one JSON
object describing what the grid actually rendered.

It runs as a separate process for two reasons:

* the interpreter segfaults at teardown once Qt has built the full window, which
  inside pytest would abort the whole run rather than fail one test;
* the application reads and writes real user settings, so the parent points HOME
  and XDG_CONFIG_HOME at a throwaway directory and this process inherits that.

Output contract: a single line ``SMOKE_JSON <json>`` on stdout. The parent
ignores the exit code — see the module docstring in the test.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = ["Supervertaler.py"]

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Segments chosen to cover the cases that have actually broken: an attributed
# tag, a plain pair, real Trados standalone tags, software placeholders, and
# prose that must never be mistaken for markup.
SEGMENTS = [
    ('Specify <cf color="#227acb" font="tahoma"><b>Wall thickness </b></cf>'
     '<b>range </b><g3>②</g3>',
     'Geef het <cf color="#227acb" font="tahoma"><b>wanddikte</b></cf>'
     '<b>bereik </b><g3>②</g3> op'),
    ('Press <b>OK</b> to continue.', 'Druk op <b>OK</b> om door te gaan.'),
    ('Page <1/> of <3/>', 'Pagina <1/> van <3/>'),
    ('If x < y and y > z the test passes', 'Als x < y en y > z slaagt de test'),
]


def main() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(240, exit=True)

    import Supervertaler as SV
    from PyQt6.QtGui import QTextCursor
    from PyQt6.QtWidgets import QApplication

    from modules import tag_atoms as ta

    app = QApplication.instance() or QApplication([])
    window = SV.SupervertalerQt()
    window._needs_data_location_dialog = False

    segments = []
    for index, (source, target) in enumerate(SEGMENTS, start=1):
        segment = SV.Segment(id=index, source=source, target=target)
        segment.status = "draft"
        segments.append(segment)
    window.current_project = SV.Project(name="smoke", segments=segments)

    def rendered(column):
        """(label, raw) for every atom in a cell, plus the displayed text."""
        widget = window.table.cellWidget(0, column)
        document = widget.document()
        atoms = []
        for info in ta.atom_tokens(document):
            cursor = QTextCursor(document)
            cursor.setPosition(info["position"])
            cursor.setPosition(info["position"] + 1,
                               QTextCursor.MoveMode.KeepAnchor)
            atoms.append({
                "label": cursor.charFormat().property(ta.PROP_LABEL),
                "raw": info["raw"],
                "tooltip": cursor.charFormat().toolTip(),
            })
        return {"atoms": atoms, "displayed": document.toPlainText()}

    result = {
        "defaults": {
            "protection": SV.EditableGridTextEditor.tag_protection_enabled,
            "configured_level": SV.EditableGridTextEditor.tag_detail_level,
        },
        "views": {},
        "segments_intact": None,
    }

    for name, mode, level in (
        ("partial_medium", "partial", ta.DETAIL_MEDIUM),
        ("partial_short", "partial", ta.DETAIL_SHORT),
        ("full", "full", None),
    ):
        if level is not None:
            SV.EditableGridTextEditor.tag_detail_level = level
        window._apply_tag_view_mode_state(mode)
        window.load_segments_to_grid()
        app.processEvents()
        result["views"][name] = {
            "effective_level": SV.EditableGridTextEditor.tag_effective_detail,
            "source": rendered(2),
            "target": rendered(3),
        }

    # Rendering must never alter stored segment text.
    result["segments_intact"] = all(
        segment.source == SEGMENTS[i][0] and segment.target == SEGMENTS[i][1]
        for i, segment in enumerate(window.current_project.segments))

    print("SMOKE_JSON " + json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
