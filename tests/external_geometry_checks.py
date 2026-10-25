"""Numerical serializer unit checks, run explicitly in the MoGe environment.

These small synthetic arrays test mesh conversion only. They neither load a
model nor provide evidence of successful image reconstruction or rendering.
NumPy is deliberately required only by this opt-in script, outside core pytest.
"""

import importlib.util
from pathlib import Path
import unittest

import numpy as np


WORKER_PATH = (Path(__file__).resolve().parents[1]
               / "src/outdoorgraph/adapters/moge_worker.py")
SPEC = importlib.util.spec_from_file_location("outdoorgraph_moge_serializer_checks", WORKER_PATH)
WORKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKER)


def synthetic_plane(height=5, width=5):
    """Camera-local plane with pixel-indexed colors and a nonuniform mask."""
    yy, xx = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    points = np.stack([xx * 0.1, yy * 0.1, np.full((height, width), 10)], axis=-1).astype(np.float32)
    valid = np.ones((height, width), dtype=bool)
    rgb = np.stack([xx * 17, yy * 19, np.full((height, width), 99)], axis=-1).astype(np.uint8)
    region = xx < max(1, width - 2)
    return points, valid, rgb, region


class MeshSerializerNumericalChecks(unittest.TestCase):
    def test_full_valid_plane_has_expected_faces_and_pixel_center_uv(self):
        inputs = synthetic_plane()
        vertices, faces, uv, colors, weights = WORKER.make_mesh(*inputs, stride=2, edge_ratio=0.25)
        self.assertEqual(vertices.shape, (9, 3))
        self.assertEqual(faces.shape, (8, 3))
        self.assertEqual(uv.shape, (9, 2))
        self.assertEqual(colors.shape, (9, 3))
        self.assertEqual(weights.shape, (9,))
        self.assertTrue(np.isfinite(vertices).all())
        np.testing.assert_allclose(np.unique(uv[:, 0]), [0.1, 0.5, 0.9], atol=1e-7)
        np.testing.assert_allclose(np.unique(uv[:, 1]), [0.1, 0.5, 0.9], atol=1e-7)
        np.testing.assert_array_equal(np.unique(faces), np.arange(9))
        triangles = vertices[faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        self.assertTrue((normals[:, 2] < 0).all(), "Plane faces should point toward the CV camera")

    def test_stride_four_does_not_bridge_full_resolution_invalid_band(self):
        points, valid, rgb, region = synthetic_plane()
        # Sampled corners at x=0/4 are valid; every intervening cell crosses x=2.
        valid[:, 2] = False
        with self.assertRaisesRegex(ValueError, "triangles|surface|faces"):
            WORKER.make_mesh(points, valid, rgb, region, stride=4, edge_ratio=0.25)

    def test_disconnected_far_vertex_is_compacted_before_bounds(self):
        points, valid, rgb, region = synthetic_plane(3, 3)
        points[-1, -1] = [1000, -1000, 1000]
        vertices, faces, uv, colors, weights = WORKER.make_mesh(
            points, valid, rgb, region, stride=1, edge_ratio=0.25)
        self.assertEqual(len(vertices), 8)
        self.assertEqual(len(faces), 7)
        self.assertLess(float(np.abs(vertices[:, :2]).max()), 1)
        self.assertEqual(float(vertices[:, 2].max()), 10)
        np.testing.assert_array_equal(np.unique(faces), np.arange(len(vertices)))
        self.assertEqual(len(uv), len(vertices))
        self.assertEqual(len(colors), len(vertices))
        self.assertEqual(len(weights), len(vertices))

    def test_depth_discontinuity_cannot_become_a_surface_bridge(self):
        points, valid, rgb, region = synthetic_plane()
        points[:, 2:, 2] = 30
        vertices, faces, _, _, _ = WORKER.make_mesh(
            points, valid, rgb, region, stride=1, edge_ratio=0.25)
        self.assertGreater(len(faces), 0)
        face_depths = vertices[faces, 2]
        # Both supported constant-depth patches remain; no face spans the jump.
        np.testing.assert_array_equal(np.unique(vertices[:, 2]), [10, 30])
        np.testing.assert_array_equal(np.ptp(face_depths, axis=1), np.zeros(len(faces)))

    def test_region_weights_and_colors_follow_uv_after_vertex_remapping(self):
        points, valid, rgb, region = synthetic_plane()
        points[-1, -1] = [1000, -1000, 1000]
        vertices, faces, uv, colors, weights = WORKER.make_mesh(
            points, valid, rgb, region, stride=2, edge_ratio=0.25)
        rows = np.rint(uv[:, 1] * region.shape[0] - 0.5).astype(int)
        columns = np.rint(uv[:, 0] * region.shape[1] - 0.5).astype(int)
        np.testing.assert_array_equal(weights, region[rows, columns].astype(np.float32))
        np.testing.assert_allclose(colors, rgb[rows, columns].astype(np.float32) / 255, atol=1e-7)
        np.testing.assert_allclose(vertices, points[rows, columns], atol=1e-7)
        np.testing.assert_array_equal(np.unique(faces), np.arange(len(vertices)))
        self.assertEqual(set(weights.tolist()), {0.0, 1.0})
        self.assertTrue((weights[faces] == 1).all(axis=1).any())


if __name__ == "__main__":
    unittest.main(verbosity=2)
