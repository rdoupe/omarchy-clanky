#!/usr/bin/env python3
"""Qt 6.12 palette qualification: Qt now ships its own `Color` type."""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
QML_FILES = [
    os.path.join(dirpath, name)
    for dirpath, _, names in os.walk(ROOT)
    if "/.git" not in dirpath
    for name in names
    if name.endswith((".qml", ".js"))
]
BARE_COLOR = re.compile(r"(?<![\w.])Color\s*(\.|$)")


def code_lines(path):
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            code = line.split("//", 1)[0]
            if code.strip():
                yield number, code


class PaletteQualification(unittest.TestCase):
    def test_no_bare_color_references(self):
        bare = [
            f"{os.path.relpath(path, ROOT)}:{number}: {code.strip()}"
            for path in QML_FILES
            for number, code in code_lines(path)
            if BARE_COLOR.search(code.rstrip())
        ]
        self.assertEqual(bare, [], "bare Color resolves to Qt's type on Qt 6.12")

    def test_commons_alias_imported_where_used(self):
        for path in QML_FILES:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if "Commons.Color" in text:
                self.assertRegex(text, r"(?m)^import qs\.Commons as Commons$", path)


if __name__ == "__main__":
    unittest.main()
