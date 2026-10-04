"""Local gh executable for tests that exercise the actual subprocess transport."""

import os
import sys

import pytest


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    raw_diff = b"diff --git a/example.py b/example.py\r\n+print('h\xc3\xa9llo')\r\n"
    executable = tmp_path / "gh"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "if '--header' in args:\n"
        f"    sys.stdout.buffer.write({raw_diff!r})\n"
        "elif '--paginate' in args:\n"
        "    print(json.dumps([[{'filename': 'example.py', 'status': 'modified'}]]))\n"
        "else:\n"
        "    print(json.dumps({'number': 12, 'state': 'open', 'merged': False,\n"
        "        'head': {'sha': 'abc123'}, 'changed_files': 1,\n"
        "        'base': {'ref': 'main', 'sha': 'base123',\n"
        "                 'repo': {'full_name': 'owner/repo'}}}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    return raw_diff
