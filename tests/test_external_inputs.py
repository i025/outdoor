"""Declared external files are content inputs to the same incremental executor."""
from dataclasses import replace
import os
from pathlib import Path

import pytest

from outdoorgraph.execution import InputSlot, OperatorSpec, execute, proxy_registry
from outdoorgraph.ir import Dependency, Node, Scene
from outdoorgraph.relations import RelationSpec


def external_graph(path, *, explicit=False):
    registry = proxy_registry()
    registry.register_relation(RelationSpec("test_feed", ("raw",), ("processed",),
                                            input_ports=("source",), validators=(lambda *args: True,)))
    calls = []

    def read(node, inputs, context):
        calls.append(node.id)
        return {"kind": "raw", "value": Path(context["external_input_paths"]["source"]).read_text()}

    def consume(node, inputs, context):
        calls.append(node.id)
        return {"kind": "processed", "value": inputs["source"]["value"]}

    declaration = {"source": path} if explicit else lambda node, inputs, context: {"source": node.params["uri"]}
    registry.register(OperatorSpec("test.file", read, {}, ("raw",), external_inputs=declaration))
    registry.register(OperatorSpec("test.consume", consume,
                                  {"source": InputSlot(("raw",), relations=("test_feed",))}, ("processed",)))
    scene = Scene("external_files", [Node("file", "raw", "test.file", {"uri": str(path)}),
                                     Node("consumer", "processed", "test.consume"),
                                     Node("independent", "background", "background.constant")],
                  [Dependency("file", "consumer", "test_feed", status="validated", input_port="source")])
    return scene, registry, calls


@pytest.mark.parametrize("suffix", [".png", ".npz", ".obj", ".texture"])
def test_same_path_changed_bytes_invalidate_only_input_and_descendants(tmp_path, suffix):
    resource = tmp_path / ("input" + suffix)
    resource.write_text("original")
    scene, registry, calls = external_graph(resource)
    first = execute(scene, registry=registry)
    assert first.passed
    calls.clear()
    unchanged = execute(scene, previous=first, registry=registry)
    assert unchanged.passed and unchanged.rebuilt_ids == [] and calls == []
    old_stat = resource.stat()
    resource.write_text("modified")  # Identical size and restored mtime defeat stat-only keys.
    os.utime(resource, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    changed = execute(scene, previous=first, registry=registry)
    assert changed.passed and changed.rebuilt_ids == ["file", "consumer"]
    assert changed.preserved_ids == ["independent"]
    assert calls == ["file", "consumer"]
    assert changed.artifacts["consumer"]["value"] == "modified"
    assert changed.artifacts["file"]["_execution"]["signature"] != first.artifacts["file"]["_execution"]["signature"]
    assert first.artifacts["file"]["value"] == "original"


def test_external_input_mtime_only_does_not_invalidate_content_cache(tmp_path):
    resource = tmp_path / "texture.png"
    resource.write_text("pixels")
    scene, registry, calls = external_graph(resource, explicit=True)
    first = execute(scene, registry=registry)
    old = resource.stat()
    os.utime(resource, ns=(old.st_atime_ns, old.st_mtime_ns + 10_000_000))
    calls.clear()
    same = execute(scene, previous=first, registry=registry)
    assert same.passed and same.rebuilt_ids == [] and calls == []


def test_missing_external_input_fails_before_old_cache_or_downstream_execution(tmp_path):
    resource = tmp_path / "geometry.npz"
    resource.write_text("mesh")
    scene, registry, calls = external_graph(resource)
    first = execute(scene, registry=registry)
    resource.unlink()
    calls.clear()
    missing = execute(scene, previous=first, registry=registry)
    assert not missing.passed and missing.status == "failed"
    assert "file" not in missing.artifacts and "consumer" not in missing.artifacts
    assert missing.preserved_ids == ["independent"] and calls == []
    assert any(f["node_id"] == "file" and f["phase"] == "external_inputs"
               and f["error_type"] == "FileNotFoundError" for f in missing.failures)
    assert first.artifacts["file"]["value"] == "mesh"


def test_external_paths_are_resolved_against_explicit_base_and_file_uri(tmp_path):
    resource = tmp_path / "geometry.npz"
    resource.write_text("mesh")
    scene, registry, _ = external_graph("geometry.npz")
    ambiguous = execute(scene, registry=registry)
    assert not ambiguous.passed and "base" in ambiguous.failures[0]["reason"].lower()
    resolved = execute(scene, registry=registry, context={"external_input_base_dir": tmp_path})
    assert resolved.passed and resolved.artifacts["file"]["value"] == "mesh"
    scene.get_node("file").params["uri"] = resource.as_uri()
    file_uri = execute(scene, registry=registry)
    assert file_uri.passed and file_uri.artifacts["file"]["value"] == "mesh"


def test_separate_external_inputs_do_not_invalidate_independent_branch(tmp_path):
    first_path, second_path = tmp_path / "a.npz", tmp_path / "b.npz"
    first_path.write_text("a")
    second_path.write_text("b")
    scene, registry, _ = external_graph(first_path)
    scene.nodes.append(Node("second_file", "raw", "test.file", {"uri": str(second_path)}))
    before = execute(scene, registry=registry)
    second_path.write_text("c")
    after = execute(scene, previous=before, registry=registry)
    assert after.passed and after.rebuilt_ids == ["second_file"]
    assert set(after.preserved_ids) == {"file", "consumer", "independent"}


@pytest.mark.parametrize("uri", ["https://example.test/asset.png", "."])
def test_nonlocal_or_directory_input_is_not_reused(tmp_path, uri):
    scene, registry, _ = external_graph(uri)
    result = execute(scene, registry=registry, context={"external_input_base_dir": tmp_path})
    assert not result.passed and result.failures[0]["phase"] == "external_inputs"


def test_external_input_change_during_callback_cannot_produce_reusable_success(tmp_path):
    resource = tmp_path / "source.txt"
    resource.write_text("before")
    scene, registry, _ = external_graph(resource)
    original = registry.get("test.file")

    def mutate(node, inputs, context):
        artifact = original.run(node, inputs, context)
        resource.write_text("during")
        return artifact

    registry.register(replace(original, run=mutate), replace=True)
    result = execute(scene, registry=registry)
    assert not result.passed and "file" not in result.artifacts
    assert "changed" in result.failures[0]["reason"].lower()
