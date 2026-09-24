"""settings_schema.FIELDS is the one declaration of every setting; keep it whole."""
from voxelmill import shellhelp
from voxelmill.cli import build_parser
from voxelmill.config import DEFAULTS
from voxelmill.settings_schema import FIELDS, check_field


def _leaves():
    for section, table in DEFAULTS.items():
        if isinstance(table, dict):
            for key in table:
                yield f'{section}.{key}'


def test_every_default_has_exactly_one_declaration():
    assert set(FIELDS) == set(_leaves())


def test_every_default_satisfies_its_own_declaration():
    for path, field in FIELDS.items():
        section, key = path.split('.', 1)
        check_field(path, DEFAULTS[section][key], field)


def test_editor_ranges_stay_inside_what_validation_accepts():
    """A slider that offers a value the validator rejects is a trap."""
    for path, field in FIELDS.items():
        if field.range is None or field.kind not in ('number', 'int'):
            continue
        low, high = field.range
        assert low > field.minimum if field.positive else low >= field.minimum, (path, field.range, field.minimum)
        if field.maximum is not None:
            assert high <= field.maximum, (path, field.range, field.maximum)
        if field.below is not None:
            assert high < field.below, (path, field.range, field.below)


def test_every_declared_flag_exists_on_the_command_line():
    flags = {flag for command in shellhelp._subparsers(build_parser()).values()
             for flag, _ in shellhelp._options(command)}
    for path, field in FIELDS.items():
        if field.flag:
            assert field.flag in flags, (path, field.flag)


def test_every_field_is_explained():
    assert [path for path, field in FIELDS.items() if not field.help] == []
