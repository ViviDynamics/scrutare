"""Keep the installed CLI proof on the selected public release and exact commit."""

from pathlib import Path

import yaml


def test_ci_installs_selected_nare_release_and_verifies_its_commit():
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/ci.yml').read_text())
    steps = workflow['jobs']['checks']['steps']
    install = next(step['run'] for step in steps if 'nare_checkout=' in step.get('run', ''))
    assert 'git clone --depth 1 --branch 2026.10.4 ' in install
    assert '79405f9d2e3db2efe4a3ffda35faaf1680000acd' in install
    assert '--python 3.14 --locked --no-dev' in install
    assert 'SCRUTARE_TEST_NARE_EXECUTABLE=' in install


def test_documented_installation_verifies_selected_release_commit():
    guide = (Path(__file__).parents[1] / 'docs/session-fanout.md').read_text()
    assert 'nare/tree/2026.10.4' in guide
    assert '79405f9d2e3db2efe4a3ffda35faaf1680000acd' in guide
    assert '899e1eb3851dcce4a4ab907f76384c49f4aca2fa' not in guide
