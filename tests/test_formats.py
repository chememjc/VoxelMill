"""The printer file format registry: one dispatch point for every format."""
import numpy as np
import pytest

from voxelmill.contracts import VoxelMillError
from voxelmill.formats import FORMATS, for_path
from test_ctb import frames, settings, write_ctb


def test_every_format_is_found_by_its_suffix_in_any_case():
    assert set(FORMATS) == {'.goo', '.ctb'}
    assert for_path('part.GOO').name == 'goo' and for_path('a/b.ctb').name == 'ctb'


def test_an_unknown_suffix_is_a_structured_error():
    with pytest.raises(VoxelMillError) as error:
        for_path('part.stl')
    assert error.value.code == 'slice_format'


def test_info_summary_and_display_agree_across_formats(tmp_path):
    ctb = tmp_path / 'part.ctb'
    write_ctb(ctb)
    fmt = for_path(ctb)
    info = fmt.info(ctb, decode_all=True)
    assert info['format'] == 'ctb' and info['layers'] == info['decoded_layers'] == 3
    summary = fmt.summary(ctb)
    assert summary['layer_count'] == 3 and summary['small_preview'] is None
    assert summary['z_mm'] == pytest.approx([0.1, 0.2, 0.3])
    layer = fmt.display_layer(ctb, 1)
    assert layer['format'] == 'ctb' and np.array_equal(layer['mask'], frames()[1])
    with pytest.raises(VoxelMillError):
        fmt.display_layer(ctb, 3)
    # Converted to GOO, the same layers read back through the other entry.
    from voxelmill.ctb import convert_slices
    goo = tmp_path / 'part.goo'
    convert_slices(ctb, goo, settings())
    assert for_path(goo).info(goo)['layers'] == 3
    assert for_path(goo).summary(goo)['small_preview'] is not None
