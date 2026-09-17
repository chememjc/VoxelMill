"""A reopened project must show the names the user gave its parts, not the
hash-named files ``load_project`` extracts them to.
"""
from __future__ import annotations

from pathlib import Path

from voxelmill.gui.document import Document


def test_reopened_project_keeps_human_names_not_extracted_hashes(tmp_path):
    primary = tmp_path / 'latch_fat_finger.stl'
    extra = tmp_path / 'bracket.stl'
    primary.write_bytes(b'primary mesh fixture data' * 10)
    extra.write_bytes(b'extra mesh fixture data' * 10)

    document = Document(source=primary)
    document.add_extra_model({'path': extra})
    project = tmp_path / 'part.voxmil'
    document.save(project)

    reopened = Document.load(project, tmp_path / 'extract')
    assert reopened.source_name == 'latch_fat_finger.stl'
    # The extracted primary lives under a hash name; the display name must not
    # be re-derived from it.
    assert Path(reopened.source).name != 'latch_fat_finger.stl'
    assert reopened.extra_models[0]['name'] == 'bracket.stl'
    assert Path(reopened.extra_models[0]['path']).name != 'bracket.stl'

    # Saving the reopened project again -- with self.source now the hash-named
    # extracted file -- must not regress the display name to that hash.
    resaved = tmp_path / 'resaved.voxmil'
    reopened.save(resaved)
    twice_reopened = Document.load(resaved, tmp_path / 'extract2')
    assert twice_reopened.source_name == 'latch_fat_finger.stl'
    assert twice_reopened.extra_models[0]['name'] == 'bracket.stl'
