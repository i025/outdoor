"""JSON contracts for external process adapters, preserving the v0.1 scene IR.

Camera poses map OpenCV camera coordinates (right, down, forward) into the
bundle's metric, right-handed Z-up world. Asset URIs remain local references;
validation neither loads geometry nor starts external software.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from outdoorgraph.ir import Scene
from outdoorgraph.validation import validate_scene


class BundleValidationError(ValueError):
    """An external scene bundle violates its interchange contract."""


def _world_frame() -> dict[str, str]:
    return {"units": "m", "axis": "z_up", "handedness": "right",
            "scale_status": "model_estimated"}


@dataclass
class SceneBundle:
    scene: Scene
    camera: dict[str, Any]
    assets: list[dict[str, Any]]
    regions: list[dict[str, Any]]
    coordinate_frame: dict[str, Any] = field(default_factory=_world_frame)
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "0.2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SceneBundle":
        if not isinstance(value, dict):
            raise BundleValidationError("SceneBundle must be a JSON object")
        value = deepcopy(value)
        try:
            bundle = cls(
                scene=Scene.from_dict(value["scene"]),
                camera=value["camera"], assets=value["assets"], regions=value["regions"],
                coordinate_frame=value.get("coordinate_frame", _world_frame()),
                provenance=value.get("provenance", {}),
                schema_version=value.get("schema_version", "0.2"),
            )
        except (KeyError, TypeError) as error:
            raise BundleValidationError(f"Malformed SceneBundle: {error}") from error
        validate_bundle(bundle)
        return bundle

    def save(self, path: str | Path) -> Path:
        validate_bundle(self)
        data = json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "SceneBundle":
        def reject_constant(value: str) -> None:
            raise BundleValidationError(f"Non-finite JSON number: {value}")

        value = json.loads(Path(path).read_text(encoding="utf-8"),
                           parse_constant=reject_constant)
        return cls.from_dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BundleValidationError(f"{label} must be a non-empty string")
    return value


def _unique_ids(values: Iterable[Any], label: str) -> set[str]:
    result: set[str] = set()
    for value in values:
        value = _text(value, f"{label} ID")
        if value in result:
            raise BundleValidationError(f"Duplicate {label} ID: {value}")
        result.add(value)
    return result


def _finite(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _matrix(value: Any, size: int, label: str) -> list[list[float]]:
    if (not isinstance(value, (list, tuple)) or len(value) != size
            or any(not isinstance(row, (list, tuple)) or len(row) != size for row in value)
            or any(not _finite(item) for row in value for item in row)):
        raise BundleValidationError(f"{label} must be a finite {size}x{size} matrix")
    return value


def _dimensions(value: dict[str, Any], label: str) -> tuple[int, int]:
    dimensions = value.get("width"), value.get("height")
    if any(not isinstance(item, int) or isinstance(item, bool) or item <= 0
           for item in dimensions):
        raise BundleValidationError(f"{label} width and height must be positive integers")
    return dimensions


def resolve_uri(root: str | Path, uri: str) -> Path:
    """Resolve a relative/absolute local path or file URI without loading it."""
    uri = _text(uri, "URI")
    if "\x00" in uri:
        raise BundleValidationError("URI contains a null byte")
    parsed = urlsplit(uri)
    if parsed.scheme:
        if (parsed.scheme != "file" or parsed.netloc not in ("", "localhost")
                or parsed.query or parsed.fragment):
            raise BundleValidationError("Only local asset and region URIs are supported")
        uri = _text(unquote(parsed.path), "File URI path")
        if "\x00" in uri:
            raise BundleValidationError("URI contains a null byte")
    path = Path(uri).expanduser()
    if not path.is_absolute():
        path = Path(root).expanduser() / path
    return path.resolve()


def cv_to_world(point: Iterable[float], scale: float = 1) -> tuple[float, float, float]:
    """Rotate camera-local CV coordinates to Z-up: (x, y, z) -> (x, z, -y)."""
    try:
        point = tuple(point)
    except TypeError as error:
        raise BundleValidationError("Point must contain three finite coordinates") from error
    if len(point) != 3 or not all(_finite(value) for value in point):
        raise BundleValidationError("Point must contain three finite coordinates")
    if not _finite(scale) or scale <= 0:
        raise BundleValidationError("Scale must be finite and positive")
    x, y, z = point
    result = float(x * scale), float(z * scale), float(-y * scale)
    if not all(math.isfinite(value) for value in result):
        raise BundleValidationError("Scaled point must remain finite")
    return result


def validate_bundle(bundle: SceneBundle) -> None:
    if bundle.schema_version != "0.2":
        raise BundleValidationError("Unsupported bundle schema version")
    if not isinstance(bundle.scene, Scene):
        raise BundleValidationError("Bundle scene must be a Scene")
    _text(bundle.scene.id, "Scene ID")
    _unique_ids((node.id for node in bundle.scene.nodes), "node")
    _unique_ids((item.id for item in bundle.scene.evidence), "evidence")
    _unique_ids((item.id for item in bundle.scene.constraints), "constraint")
    validate_scene(bundle.scene)
    frame = bundle.coordinate_frame
    if (not isinstance(frame, dict) or frame.get("units") != "m"
            or frame.get("axis") != "z_up" or frame.get("handedness") != "right"):
        raise BundleValidationError("Bundle coordinates require meters, z_up, and right handedness")
    _text(frame.get("scale_status", "model_estimated"), "Scale status")
    if not isinstance(bundle.provenance, dict):
        raise BundleValidationError("Bundle provenance must be an object")
    camera = bundle.camera
    if not isinstance(camera, dict):
        raise BundleValidationError("Camera must be an object")
    _text(camera.get("id"), "Camera ID")
    dimensions = _dimensions(camera, "Camera")
    intrinsics = _matrix(camera.get("intrinsics_pixels"), 3, "Camera intrinsics")
    if (intrinsics[0][0] <= 0 or intrinsics[1][1] <= 0
            or any(abs(value - target) > 1e-6
                   for value, target in zip(intrinsics[2], (0, 0, 1)))):
        raise BundleValidationError("Camera intrinsics need positive focal lengths and bottom row [0,0,1]")
    transform = _matrix(camera.get("world_from_camera_cv"), 4, "Camera transform")
    if any(abs(value - target) > 1e-6
           for value, target in zip(transform[3], (0, 0, 0, 1))):
        raise BundleValidationError("Camera transform bottom row must be [0,0,0,1]")
    for row in range(3):
        for col in range(3):
            dot = sum(transform[k][row] * transform[k][col] for k in range(3))
            if abs(dot - (1 if row == col else 0)) > 1e-6:
                raise BundleValidationError("Camera transform rotation must be orthogonal")
    a, b, c = (row[:3] for row in transform[:3])
    determinant = (a[0] * (b[1] * c[2] - b[2] * c[1])
                   - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0]))
    if abs(determinant - 1) > 1e-6:
        raise BundleValidationError("Camera transform rotation determinant must be +1")
    for collection, label in ((bundle.assets, "Asset"), (bundle.regions, "Region")):
        if not isinstance(collection, list) or any(not isinstance(item, dict) for item in collection):
            raise BundleValidationError(f"{label}s must be a list of objects")
        _unique_ids((item.get("id") for item in collection), label.lower())
        for item in collection:
            resolve_uri(".", item.get("uri"))
    asset_ids = {item["id"] for item in bundle.assets}
    region_ids = {item["id"] for item in bundle.regions}
    for asset in bundle.assets:
        _text(asset.get("format"), "Asset format")
        asset_frame = asset.get("coordinate_frame")
        if not ((isinstance(asset_frame, str) and asset_frame.strip())
                or (isinstance(asset_frame, dict) and asset_frame)):
            raise BundleValidationError("Asset coordinate_frame must be a non-empty string or object")
        if not isinstance(asset.get("provenance"), dict):
            raise BundleValidationError("Asset provenance must be an object")
    for region in bundle.regions:
        _text(region.get("semantic"), "Region semantic")
        if region.get("source") not in ("manual", "model"):
            raise BundleValidationError("Region source must be manual or model")
        if _dimensions(region, "Region") != dimensions:
            raise BundleValidationError("Region mask dimensions must match the camera")
    for node in bundle.scene.nodes:
        for key, ids in (("asset_id", asset_ids), ("region_id", region_ids)):
            if key in node.params:
                reference = node.params[key]
                if not isinstance(reference, str) or reference not in ids:
                    raise BundleValidationError(f"Missing {key} reference on node {node.id}: {reference}")
