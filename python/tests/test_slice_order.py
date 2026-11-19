"""Project order persists across Alignment/Viewer filename and subset views."""
import json
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "py"))
from slice_order import apply_order, load_order, order_path, read_order, save_order
from slice_index import build_adjust_pairs, slice_stem_from_image_filename


def test_default_natural_order_and_new_files():
    assert apply_order(["s10", "s2", "s1"], []) == ["s1", "s2", "s10"]
    assert apply_order(["s10", "s2", "s1"], ["missing", "s2", "s2"]) == ["s2", "s1", "s10"]


def test_subset_save_keeps_hidden_ids(tmp_path):
    path = tmp_path / "slice_order.json"
    save_order(path, ["a", "b", "c", "d"])
    assert save_order(path, ["d", "b"]) == ["a", "d", "c", "b"]
    assert apply_order(["b", "d"], read_order(path)) == ["d", "b"]


def test_append_new_ids_and_reopen(tmp_path):
    path = tmp_path / "slice_order.json"
    save_order(path, ["s2", "s1"])
    save_order(path, ["s3", "s2", "s1"])
    assert load_order(path) == ["s3", "s2", "s1"]
    assert not list(tmp_path.glob("*.tmp"))


def test_conflicting_window_refused(tmp_path):
    path = tmp_path / "slice_order.json"
    save_order(path, ["a", "b"])
    expected = read_order(path)
    save_order(path, ["b", "a"])
    with pytest.raises(ValueError, match="another window"):
        save_order(path, ["a", "b"], expected=expected)
    assert read_order(path) == ["b", "a"]


@pytest.mark.parametrize("content", ['{broken', '{"slice_ids": [1]}', '[]'])
def test_corrupt_file_is_not_overwritten(tmp_path, content):
    path = tmp_path / "slice_order.json"
    path.write_text(content, encoding="utf-8")
    assert load_order(path) == []
    with pytest.raises(ValueError):
        save_order(path, ["a"])
    assert path.read_text(encoding="utf-8") == content


def test_alignment_viewer_and_adjacent_share_dotted_ids(tmp_path):
    root = tmp_path / "brain"
    image_dir = root / "data/counting/00_dapi"
    annotations = root / "data/counting/01_slices/align/run"
    image_dir.mkdir(parents=True)
    annotations.mkdir(parents=True)
    (root / ".masonjar").mkdir()
    ids = ["202607.M554.01.63", "202607.M554.01.64", "202607.M554.01.65"]
    for sid in ids:
        (image_dir / f"{sid}.png").write_bytes(b"original image")
        (annotations / f"Annotation_{sid}.pkl").write_bytes(b"original annotation")
    path = order_path(image_dir)
    assert path == root / ".masonjar/slice_order.json"
    assert order_path(annotations) == path
    saved = [ids[2], ids[0], ids[1]]
    save_order(path, saved)
    alignment = apply_order([f"{sid}.png" for sid in ids], load_order(path),
                            key=slice_stem_from_image_filename)
    pairs, _, _ = build_adjust_pairs(image_dir, annotations)
    assert [slice_stem_from_image_filename(n) for n in alignment] == [p[2] for p in pairs] == saved
    # Compare Adjacent uses current_index + offset into this same pair list.
    assert pairs[1 + 1][2] == ids[1]
    assert pairs[1 - 1][2] == ids[2]
    for image, annotation, sid in pairs:
        assert Path(image).name == sid + ".png"
        assert Path(annotation).name == f"Annotation_{sid}.pkl"
        assert Path(annotation).read_bytes() == b"original annotation"
    subset = root / ".masonjar/subset.json"
    subset.write_text(json.dumps({"slice_ids": [ids[0], ids[2]]}))
    assert [p[2] for p in build_adjust_pairs(image_dir, annotations, subset)[0]] == saved[:2]


def test_no_project_does_not_write_arbitrary_parent(tmp_path):
    assert order_path(tmp_path / "unbound") is None


def test_dialog_controls():
    from qtpy.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from slice_order_dialog import SliceOrderDialog
    dialog = SliceOrderDialog(["b", "a", "c"], {"a": 30, "b": 20, "c": 10})
    dialog.sections.setCurrentRow(1)
    dialog.move_selected(-1)
    assert dialog.names() == ["a", "b", "c"]
    dialog.sort_ap()
    assert dialog.names() == ["c", "b", "a"]
    dialog.set_names(list(reversed(dialog.names())))
    assert dialog.names() == ["a", "b", "c"]
    dialog.reject()


def controller_method(name, **namespace):
    tree = ast.parse((Path(__file__).resolve().parents[2] / "py/map.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AlignmentController")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    method.decorator_list = []
    exec(compile(ast.Module(body=[method], type_ignores=[]), "map-method", "exec"), namespace)
    return namespace[name]


def test_reordering_preserves_current_file_ap_angles_and_labels(tmp_path, monkeypatch):
    """Run the real controller action without importing heavyweight atlas models."""
    from qtpy.QtWidgets import QDialog
    import slice_order_dialog
    class AcceptedDialog:
        def __init__(self, *args):
            pass
        def exec(self):
            return QDialog.DialogCode.Accepted
        def names(self):
            return ["brain.03.png", "brain.01.png", "brain.02.png"]
    monkeypatch.setattr(slice_order_dialog, "SliceOrderDialog", AcceptedDialog)
    states = {f"brain.0{i}.png": SimpleNamespace(ap_position=i * 10, x_angle=i,
              label=object()) for i in range(1, 4)}
    before = {n: (s.ap_position, s.x_angle, s.label) for n, s in states.items()}
    events = []
    controller = SimpleNamespace(
        _project_slice_order_path=tmp_path / "slice_order.json",
        file_list=list(states), current_section=1, num_slices=3,
        atlas_slices=states, _ap_locked=False,
        _sync_current_slice_from_controls=lambda: None,
        _slice_id_from_filename=slice_stem_from_image_filename,
        _seed_predicted_delta_from_slices=lambda: None,
        update_section_header=lambda: None,
        progress_bar=SimpleNamespace(setValue=lambda x: None, setFormat=lambda x: None),
        persist_alignment_session=lambda reason: events.append(reason),
    )
    action = controller_method("edit_slice_order", read_order=read_order, save_order=save_order)
    action(controller)
    assert controller.file_list[controller.current_section] == "brain.02.png"
    assert controller._ap_locked
    assert events == ["slice_order"]
    assert before == {n: (s.ap_position, s.x_angle, s.label) for n, s in states.items()}
    assert read_order(controller._project_slice_order_path) == ["brain.03", "brain.01", "brain.02"]
    # Automatic AP extrapolation must not overwrite the reordered slice states.
    controller_method("adjust_positions")(controller)
    assert before == {n: (s.ap_position, s.x_angle, s.label) for n, s in states.items()}


def test_alignment_slice_parser_preserves_full_id():
    parse = controller_method("_slice_id_from_filename")
    assert parse("202607.M554.01.63.png") == "202607.M554.01.63"
