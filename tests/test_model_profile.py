"""Third-party model contracts and their integration with hub scoring."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from printguard.engine.vision import defect_score
from printguard.server.model_profile import ModelProfile
from printguard.server import inference
from printguard.server.platform import ServerPlatform


def profile(**overrides: Any) -> ModelProfile:
    """Builds a small two-class model contract."""
    return ModelProfile.model_validate({
        "file": "test.onnx", "format": "classifier", "classes": ["good", "spaghetti"],
        "failure_classes": ["spaghetti"], "width": 4, "height": 2, **overrides,
    })


def test_rgb_and_normalisation_are_not_the_bundled_greyscale_pipeline() -> None:
    image = np.full((2, 4, 3), [255, 128, 0], dtype=np.uint8)
    tensor = profile().preprocess(image)
    assert tensor.shape == (1, 3, 2, 4)
    assert tensor.dtype == np.float32
    np.testing.assert_allclose(tensor[0, :, 0, 0], [1, 128 / 255, 0])
    tensor = profile(layout="NHWC", channel_order="BGR", mean=[0.0, 0.5, 0.0], std=[1.0, 0.5, 1.0]).preprocess(image)
    assert tensor.shape == (1, 2, 4, 3)
    np.testing.assert_allclose(tensor[0, 0, 0], [0, (128 / 255 - 0.5) / 0.5, 1], atol=1e-7)


def test_bilinear_resize_and_letterbox_padding() -> None:
    image = np.array([[[0, 0, 0], [255, 255, 255]]], dtype=np.uint8)
    tensor = profile(width=3, height=1).preprocess(image)
    np.testing.assert_allclose(tensor[0, 0, 0], [0, 0.5, 1])
    tensor = profile(width=4, height=4, resize="letterbox").preprocess(image)
    np.testing.assert_allclose(tensor[0, :, 0], 114 / 255)
    np.testing.assert_allclose(tensor[0, :, -1], 114 / 255)
    assert tensor[0, 0, 1, 0] == 0
    assert tensor[0, 0, 1, -1] == 1


def test_classifier_probabilities_logits_and_failure_mapping() -> None:
    result = profile().classify(np.array([0.2, 0.8]))
    assert result["prediction"] == "failure"
    assert defect_score(result) == pytest.approx(0.8)
    assert defect_score(result, 2) == 1
    assert defect_score(result, 0) == 0.5
    assert profile(logits=True).classify(np.array([-1000, 1000]))["failure_probability"] == 1
    binary = profile(classes=["spaghetti"], logits=True)
    assert binary.classify(np.array([0.0]))["failure_probability"] == 0.5
    multiclass = profile(classes=["spaghetti", "good", "detached"], failure_classes=["spaghetti", "detached"])
    assert multiclass.classify(np.array([0.3, 0.2, 0.5]))["failure_probability"] == pytest.approx(0.8)


@pytest.mark.parametrize("format", ["obico", "yolo_v5", "yolo_v8"])
def test_detectors_select_failure_classes_and_preserve_confidence(format) -> None:
    scores = np.array([[0.05, 0.9], [0.95, 0.8], [0.1, 0.7]], dtype=np.float32)
    boxes = np.zeros((3, 4))
    if format == "yolo_v5":
        output = np.concatenate([boxes, np.full((3, 1), 0.8), scores], axis=1)
        expected = 0.72
    elif format == "yolo_v8":
        output = np.concatenate([boxes, scores], axis=1).T
        expected = 0.9
    else:
        output = scores
        expected = 0.9
    result = profile(format=format).classify(output)
    assert result["failure_probability"] == pytest.approx(expected)
    empty = output[:, :0] if format == "yolo_v8" else output[:0]
    assert profile(format=format).classify(empty)["failure_probability"] == 0


@pytest.mark.parametrize("output", [np.array([np.nan, 0]), np.array([0, np.inf]), np.array([-1, 2]), np.array([0.1, 0.1]), np.ones((1, 2))])
def test_invalid_classifier_outputs_raise_instead_of_reporting_success(output) -> None:
    with pytest.raises(ValueError):
        profile().classify(output)


@pytest.mark.parametrize("overrides", [
    {"failure_classes": ["typo"]}, {"classes": ["good", "good"]}, {"std": [1.0, 0.0, 1.0]},
    {"width": 0}, {"scale": float("nan")}, {"file": "model.pt"}, {"format": "obico", "resize": "letterbox"},
    {"format": "obico", "logits": True}, {"unknown_option": True},
])
def test_invalid_profiles_fail_early(overrides) -> None:
    with pytest.raises(ValueError):
        profile(**overrides)


def test_profile_loading_and_paths(tmp_path: Path) -> None:
    assert ModelProfile.load(tmp_path) is None
    (tmp_path / "model.json").write_text(profile().model_dump_json())
    assert ModelProfile.load(tmp_path) == profile()
    with pytest.raises(ValueError, match="inside MODEL_DIR"):
        profile().model_path(tmp_path)
    (tmp_path / "test.onnx").touch()
    assert profile().model_path(tmp_path) == tmp_path / "test.onnx"
    with pytest.raises(ValueError, match="inside MODEL_DIR"):
        profile(file="../test.onnx").model_path(tmp_path)


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_probability_does_not_reach_monitor(probability) -> None:
    with pytest.raises(ValueError):
        defect_score({"failure_probability": probability})


def fake_onnx(monkeypatch, *, shape=None, output=None) -> list:
    """Stubs only the external ONNX runtime and records actual input tensors."""
    tensors = []

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        def get_inputs(self):
            return [SimpleNamespace(name="image", type="tensor(float)", shape=shape or [1, 3, 2, 4])]

        def get_outputs(self):
            return [SimpleNamespace(name="boxes"), SimpleNamespace(name="confs")]

        def run(self, names, inputs):
            assert names == ["confs"]
            tensors.append(inputs["image"])
            return [output if output is not None else np.array([[[0.1, 0.9]]], dtype=np.float32)]

    monkeypatch.setattr(inference.OnnxInference, "_register_plugins", lambda self: None)
    monkeypatch.setattr(inference.ort, "get_ep_devices", lambda: [])
    monkeypatch.setattr(inference.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(inference.ort, "InferenceSession", Session)
    return tensors


async def test_custom_hub_model_without_prototype_files(tmp_path, monkeypatch) -> None:
    tensors = fake_onnx(monkeypatch)
    monkeypatch.setattr(inference.os, "cpu_count", lambda: 1)
    monkeypatch.setenv("PRINTGUARD_PLUGINS", "off")
    (tmp_path / "model.json").write_text(profile(format="obico").model_dump_json())
    (tmp_path / "test.onnx").touch()
    hub = ServerPlatform(tmp_path, tmp_path / "data", "http://localhost:9997", "rtsp://localhost:8554")
    try:
        await hub.configure({"inference_runtime": "auto"})
        result = await hub.infer(np.full((20, 30, 3), [255, 0, 0], dtype=np.uint8))
        assert defect_score(result) == pytest.approx(0.9)
        assert hub.inference_device == "ONNX CPU"
        assert all(t.shape == (1, 3, 2, 4) for t in tensors)
        np.testing.assert_allclose(tensors[-1][0, :, 0, 0], [1, 0, 0])
        with pytest.raises(ValueError, match="Automatic or ONNX"):
            await hub.configure({"inference_runtime": "litert"})
        assert defect_score(await hub.infer(np.zeros((2, 4, 3), dtype=np.uint8))) == pytest.approx(0.9)
    finally:
        await hub.close()


def test_onnx_input_shape_mismatch_is_rejected(tmp_path, monkeypatch) -> None:
    fake_onnx(monkeypatch, shape=[1, 3, 224, 224])
    with pytest.raises(ValueError, match="does not match profile"):
        inference.OnnxInference(tmp_path / "test.onnx", profile(format="obico"))


def test_wrong_output_contract_is_rejected_before_starting_workers(tmp_path, monkeypatch) -> None:
    fake_onnx(monkeypatch, output=np.zeros((1, 12, 4)))
    (tmp_path / "test.onnx").touch()
    with pytest.raises(ValueError, match="Obico confidence output"):
        inference.Inference(tmp_path, "auto", profile(format="obico"))


@pytest.mark.skipif(not os.environ.get("PRINTGUARD_TEST_OBICO_DIR"), reason="official model is an optional local download")
async def test_official_obico_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs the downloaded upstream model through hub configuration and inference."""
    monkeypatch.setenv("PRINTGUARD_PLUGINS", "off")
    folder = Path(os.environ["PRINTGUARD_TEST_OBICO_DIR"])
    hub = ServerPlatform(folder, tmp_path, "http://localhost:9997", "rtsp://localhost:8554")
    try:
        await hub.configure({"inference_runtime": "auto"})
        for image in (np.zeros((480, 640, 3), dtype=np.uint8), np.full((240, 320, 3), 127, dtype=np.uint8)):
            result = await hub.infer(image)
            assert np.isfinite(result["failure_probability"])
            assert 0 <= defect_score(result) <= 1
        assert hub.workers >= 1
        assert hub.inference_device
    finally:
        await hub.close()


def test_output_batch_dimension_is_not_silently_discarded(tmp_path, monkeypatch) -> None:
    fake_onnx(monkeypatch, output=np.array([[0.1], [0.9]]))
    model = inference.OnnxInference(tmp_path / "test.onnx", profile(format="obico"))
    try:
        with pytest.raises(ValueError, match="single-image batch"):
            model.run(np.zeros((1, 3, 2, 4), dtype=np.float32))
    finally:
        model.close()


def test_center_crop_preserves_the_middle_of_a_wide_frame() -> None:
    image = np.zeros((2, 6, 3), dtype=np.uint8)
    image[:, 2:4] = 255
    tensor = profile(width=2, height=2, resize="center_crop").preprocess(image)
    np.testing.assert_allclose(tensor, 1.0)


def test_binary_classifier_can_identify_normal_as_the_positive_class() -> None:
    contract = profile(classes=["spaghetti", "normal"], binary=True)
    assert contract.classify(np.array([0.9]))["failure_probability"] == pytest.approx(0.1)
    with pytest.raises(ValueError):
        contract.classify(np.array([1.1]))


def test_single_score_onnx_export_keeps_its_score(tmp_path, monkeypatch) -> None:
    fake_onnx(monkeypatch, output=np.array([0.7]))
    contract = profile(classes=["spaghetti"], output_index=1)
    model = inference.OnnxInference(tmp_path / "test.onnx", contract)
    try:
        assert contract.classify(model.run(np.zeros(contract.input_shape, dtype=np.float32)))["failure_probability"] == 0.7
    finally:
        model.close()


async def test_snapshot_in_progress_finishes_with_its_original_model(tmp_path, monkeypatch) -> None:
    import asyncio
    from printguard.server import platform as server_platform

    monkeypatch.setenv("PRINTGUARD_PLUGINS", "off")
    (tmp_path / "model.json").write_text(profile().model_dump_json())
    (tmp_path / "test.onnx").touch()
    instances = []
    entered, proceed = asyncio.Event(), asyncio.Event()

    class Runtime:
        """Holds an in-flight snapshot until the test permits completion."""

        workers, device, runtime, capacity_fps = 1, "test", "onnx", 1.0

        def __init__(self, *args):
            self.closed = False
            instances.append(self)

        async def run(self, tensor):
            """Waits for the concurrent model switch."""
            entered.set()
            await proceed.wait()
            assert not self.closed
            return np.array([0.1, 0.9])

        def close(self):
            """Records runtime disposal."""
            self.closed = True

    monkeypatch.setattr(server_platform, "Inference", Runtime)
    hub = ServerPlatform(tmp_path, tmp_path / "data", "http://localhost:9997", "rtsp://localhost:8554")
    try:
        await hub.configure({"inference_runtime": "auto"})
        snapshot = asyncio.create_task(hub.infer(np.zeros((2, 4, 3), dtype=np.uint8)))
        await entered.wait()
        await hub.configure({"inference_runtime": "auto"})
        assert not instances[0].closed
        proceed.set()
        assert (await snapshot)["failure_probability"] == 0.9
        assert instances[0].closed
        assert not instances[1].closed
    finally:
        proceed.set()
        await hub.close()
