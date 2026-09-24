"""The command line is a public interface: nothing on it disappears by accident."""
import json
from pathlib import Path

from voxelmill import shellhelp
from voxelmill.cli import build_parser

SNAPSHOT = Path(__file__).parent / 'data' / 'cli_surface.json'


def _surface():
    commands = shellhelp._subparsers(build_parser())
    return {name: sorted({flag for flag, _ in shellhelp._options(command)})
            for name, command in commands.items()}


def test_no_command_or_option_has_been_removed():
    """Adding is free. Removing or renaming is a breaking change: after 1.0 it
    needs a deprecation first. To record an intended change, regenerate
    tests/data/cli_surface.json from ``_surface()`` in the same commit."""
    recorded = json.loads(SNAPSHOT.read_text())
    current = _surface()
    missing_commands = sorted(set(recorded) - set(current))
    assert not missing_commands, f'commands removed: {missing_commands}'
    removed = {name: sorted(set(flags) - set(current[name]))
               for name, flags in recorded.items() if set(flags) - set(current[name])}
    assert not removed, f'options removed: {removed}'
