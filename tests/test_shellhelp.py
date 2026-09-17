"""Shell completion and the man page, generated from the live parser."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from voxelmill.cli import build_parser, main
from voxelmill.contracts import VoxelMillError
from voxelmill import shellhelp


def commands():
    return set(shellhelp._subparsers(build_parser()))


def test_every_subcommand_and_its_flags_reach_every_shell():
    """A hand-maintained completion is a second place to forget a flag.

    These read the same parser the CLI runs, so a new command or option cannot
    exist on one side only.
    """
    parser = build_parser()
    for shell in shellhelp.SHELLS:
        script = shellhelp.completion(shell, parser)
        for name in commands():
            assert name in script, f'{name} missing from {shell} completion'
        # A flag added late in this session, checked in every shell.
        assert '--elephant-foot-mm' in script or 'elephant-foot-mm' in script


def test_the_generated_bash_completion_is_valid_bash(tmp_path):
    script = tmp_path / 'voxelmill.bash'
    script.write_text(shellhelp.bash_completion(build_parser()))
    result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('shell,binary,flags', [
    ('zsh', 'zsh', ['-n']),
    ('fish', 'fish', ['--no-execute']),
])
def test_the_other_shells_parse_their_own_script_when_present(tmp_path, shell, binary, flags):
    if shutil.which(binary) is None:
        pytest.skip(f'{binary} is not installed here')
    script = tmp_path / f'voxelmill.{shell}'
    script.write_text(shellhelp.completion(shell, build_parser()))
    result = subprocess.run([binary, *flags, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_completion_rejects_a_shell_it_does_not_generate():
    with pytest.raises(VoxelMillError, match='Completion is available'):
        shellhelp.completion('powershell', build_parser())


def test_the_man_page_names_the_program_not_the_module():
    page = shellhelp.manpage(build_parser(), version='voxelmill 0.1.0')
    name = page.splitlines()[2]
    assert name.startswith('voxelmill \\-')
    # The parser's description is the module docstring's first line, which
    # names the file rather than the program.
    assert 'Command line entry point' not in name
    assert 'stereolithography' in name


def test_the_man_page_documents_every_command_and_renders_without_warnings(tmp_path):
    page = shellhelp.manpage(build_parser(), version='voxelmill 0.1.0')
    for name in commands():
        assert f'.SS {name.replace("-", chr(92) + "-")}' in page, name
    assert 'No description.' not in page
    assert '.SH EXIT STATUS' in page and 'VOXELMILL_PROFILE_PATH' in page
    if shutil.which('groff') is None:
        pytest.skip('groff is not installed here')
    source = tmp_path / 'voxelmill.1'
    source.write_text(page)
    result = subprocess.run(['groff', '-man', '-Tascii', '-ww', str(source)],
                            capture_output=True, text=True)
    assert result.returncode == 0
    # roff turns an unescaped leading hyphen into a different character, so a
    # warning-free render is the check that the escaping actually worked.
    assert result.stderr == ''
    rendered = subprocess.run(['col', '-b'], input=result.stdout,
                              capture_output=True, text=True).stdout
    assert '--elephant-foot-mm' in rendered
    assert '--allow-unresolved' in rendered


def test_the_cli_writes_both_to_a_file_or_to_stdout(tmp_path, capsys):
    assert main(['completion', 'bash']) == 0
    assert '_voxelmill_complete' in capsys.readouterr().out
    destination = tmp_path / 'nested' / 'voxelmill.1'
    assert main(['manpage', '--section', '8', '--output', str(destination)]) == 0
    assert capsys.readouterr().out == ''
    assert destination.read_text().startswith('.TH VOXELMILL 8 ')


def test_a_value_taking_flag_is_distinguished_from_a_switch():
    parser = build_parser()
    prepare = shellhelp._subparsers(parser)['prepare']
    actions = dict(shellhelp._options(prepare))
    assert shellhelp._takes_value(actions['--output'])
    assert not shellhelp._takes_value(actions['--components'])
    assert not shellhelp._takes_value(actions['--progress'])
