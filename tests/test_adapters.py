import copy

import pytest

from outdoorgraph.adapters import (
    BundleValidationError, SceneBundle, cv_to_world, resolve_uri, validate_bundle,
)
from outdoorgraph.ir import Dependency, Node, Scene
from outdoorgraph.validation import SceneValidationError


@pytest.fixture
def bundle():
    return SceneBundle(
        scene=Scene("reference_scene", [
            Node("observed_mesh", "terrain", "external.moge.mesh", {"asset_id": "mesh"}),
            Node("grass", "vegetation", "external.infinigen.grass", {"region_id": "lawn"}),
        ], dependencies=[Dependency("observed_mesh", "grass", "supported_by_terrain", status="validated")]),
        camera={"id": "reference_camera", "width": 640, "height": 480,
                "intrinsics_pixels": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                "world_from_camera_cv": [[1, 0, 0, 0], [0, 0, 1, 0],
                                         [0, -1, 0, 0], [0, 0, 0, 1]]},
        assets=[{"id": "mesh", "uri": "assets/mesh.npz", "format": "npz",
                 "coordinate_frame": "world_z_up", "provenance": {"model": "MoGe"}}],
        regions=[{"id": "lawn", "uri": "regions/lawn.png", "semantic": "grass",
                  "source": "manual", "width": 640, "height": 480}],
        provenance={"image": "reference.png"},
    )


def test_bundle_json_round_trip_preserves_core_ir(bundle, tmp_path):
    original = bundle.to_dict()
    restored = SceneBundle.from_dict(original)
    assert restored.to_dict() == original
    assert restored.scene.schema_version == "0.1"
    assert restored.schema_version == "0.2"
    path = bundle.save(tmp_path / "nested" / "scene_bundle.json")
    assert SceneBundle.load(path).to_dict() == original
    # Deserializing preserves identifiers without retaining mutable input aliases.
    original["camera"]["intrinsics_pixels"][0][0] = 10
    assert restored.camera["intrinsics_pixels"][0][0] == 500


def test_frame_defaults_are_independent(bundle):
    other = SceneBundle(bundle.scene, bundle.camera, bundle.assets, bundle.regions)
    bundle.coordinate_frame["scale_status"] = "calibrated"
    assert other.coordinate_frame["scale_status"] == "model_estimated"


@pytest.mark.parametrize("collection", ["assets", "regions"])
def test_bundle_rejects_duplicate_external_ids(bundle, collection):
    items = getattr(bundle, collection)
    items.append(copy.deepcopy(items[0]))
    with pytest.raises(BundleValidationError, match="Duplicate"):
        validate_bundle(bundle)


@pytest.mark.parametrize("key", ["asset_id", "region_id"])
def test_bundle_rejects_dangling_node_reference(bundle, key):
    bundle.scene.nodes[0].params[key] = "missing"
    with pytest.raises(BundleValidationError, match=f"Missing {key}"):
        validate_bundle(bundle)


def test_bundle_runs_core_ir_validation(bundle):
    bundle.scene.dependencies.append(Dependency("grass", "observed_mesh", "cycle", status="validated"))
    with pytest.raises(SceneValidationError, match="Cycle"):
        validate_bundle(bundle)


@pytest.mark.parametrize("key,value", [
    ("units", "cm"), ("axis", "y_up"), ("handedness", "left"),
])
def test_bundle_rejects_incompatible_world_frame(bundle, key, value):
    bundle.coordinate_frame[key] = value
    with pytest.raises(BundleValidationError, match="Bundle coordinates"):
        validate_bundle(bundle)


@pytest.mark.parametrize("matrix", [
    [[500, 0], [0, 500]],
    [[0, 0, 320], [0, 500, 240], [0, 0, 1]],
    [[500, 0, 320], [0, -1, 240], [0, 0, 1]],
    [[500, 0, float("nan")], [0, 500, 240], [0, 0, 1]],
    [[500, 0, 320], [0, 500, 240], [0, 1, 1]],
])
def test_bundle_rejects_bad_camera_intrinsics(bundle, matrix):
    bundle.camera["intrinsics_pixels"] = matrix
    with pytest.raises(BundleValidationError, match="intrinsics"):
        validate_bundle(bundle)


@pytest.mark.parametrize("matrix,message", [
    ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], "4x4"),
    ([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, float("inf")], [0, 0, 0, 1]], "finite"),
    ([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 1, 1]], "bottom row"),
    ([[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "orthogonal"),
    ([[-1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "determinant"),
])
def test_bundle_rejects_bad_camera_pose(bundle, matrix, message):
    bundle.camera["world_from_camera_cv"] = matrix
    with pytest.raises(BundleValidationError, match=message):
        validate_bundle(bundle)


@pytest.mark.parametrize("change,message", [
    ({"width": 639}, "dimensions"), ({"height": 0}, "positive integers"),
    ({"source": "inferred"}, "source"), ({"semantic": ""}, "semantic"),
])
def test_bundle_rejects_bad_region_metadata(bundle, change, message):
    bundle.regions[0].update(change)
    with pytest.raises(BundleValidationError, match=message):
        validate_bundle(bundle)


def test_bundle_accepts_asset_frame_metadata(bundle):
    bundle.assets[0]["coordinate_frame"] = {"axis": "z_up", "units": "m"}
    validate_bundle(bundle)


@pytest.mark.parametrize("change,message", [
    ({"id": ""}, "ID"), ({"format": ""}, "format"),
    ({"coordinate_frame": {}}, "coordinate_frame"), ({"provenance": []}, "provenance"),
    ({"uri": "https://example.com/mesh.glb"}, "local"),
])
def test_bundle_rejects_bad_asset_metadata(bundle, change, message):
    bundle.assets[0].update(change)
    with pytest.raises(BundleValidationError, match=message):
        validate_bundle(bundle)


def test_local_uri_resolution(tmp_path):
    expected = (tmp_path / "assets" / "mesh.npz").resolve()
    assert resolve_uri(tmp_path, "assets/mesh.npz") == expected
    assert resolve_uri(tmp_path / "other", str(expected)) == expected
    assert resolve_uri(tmp_path, expected.as_uri()) == expected


@pytest.mark.parametrize("uri", ["file:", "file:///mesh%00.npz", "file://remote/mesh.npz"])
def test_local_uri_rejects_invalid_or_remote_file_uri(tmp_path, uri):
    with pytest.raises(BundleValidationError):
        resolve_uri(tmp_path, uri)


def test_cv_to_world_basis_and_metric_scale():
    assert cv_to_world((1, 0, 0)) == (1, 0, 0)
    assert cv_to_world((0, 1, 0)) == (0, 0, -1)
    assert cv_to_world((0, 0, 1)) == (0, 1, 0)
    assert cv_to_world((2, 3, 4), scale=0.5) == (1, 2, -1.5)


@pytest.mark.parametrize("scale", [0, -1, float("inf"), float("nan"), True, 10 ** 400])
def test_cv_to_world_rejects_invalid_scale(scale):
    with pytest.raises(BundleValidationError, match="Scale"):
        cv_to_world((1, 2, 3), scale)


@pytest.mark.parametrize("point", [(1, 2), (1, 2, float("nan")), (1, "2", 3)])
def test_cv_to_world_rejects_invalid_point(point):
    with pytest.raises(BundleValidationError, match="Point"):
        cv_to_world(point)


def test_bundle_rejects_nonfinite_json_on_load(bundle, tmp_path):
    path = bundle.save(tmp_path / "bundle.json")
    path.write_text(path.read_text().replace('"model_estimated"', 'NaN'))
    with pytest.raises(BundleValidationError, match="Non-finite JSON"):
        SceneBundle.load(path)
