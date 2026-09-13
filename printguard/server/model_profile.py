"""Explicit input and output contracts for third-party ONNX models."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelProfile(BaseModel):
    """Describes a float32, single-image community model without executing model code."""

    model_config = ConfigDict(extra="forbid", strict=True)

    file: str
    format: Literal["classifier", "yolo_v5", "yolo_v8", "obico"]
    classes: list[str] = Field(min_length=1)
    failure_classes: list[str] = Field(min_length=1)
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    layout: Literal["NCHW", "NHWC"] = "NCHW"
    resize: Literal["stretch", "letterbox", "center_crop"] = "stretch"
    channel_order: Literal["RGB", "BGR"] = "RGB"
    scale: float = 1.0 / 255.0
    mean: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    std: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0], min_length=3, max_length=3)
    logits: bool = False
    binary: bool = False
    output_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_contract(self) -> ModelProfile:
        """Rejects ambiguous class mappings and invalid normalisation.

        Returns:
            The validated profile.

        Raises:
            ValueError: The profile cannot describe a supported model.
        """
        if Path(self.file).suffix.lower() not in (".onnx", ".tflite"):
            raise ValueError("custom models must be ONNX or TFLite exports; Darknet and PyTorch weights need conversion")
        if len(set(self.classes)) != len(self.classes) or not all(self.classes):
            raise ValueError("classes must contain unique, non-empty labels in model output order")
        if len(set(self.failure_classes)) != len(self.failure_classes) or not set(self.failure_classes) <= set(self.classes):
            raise ValueError("failure_classes must be unique labels from classes")
        if not np.isfinite([self.scale, *self.mean, *self.std]).all() or any(x <= 0 for x in self.std):
            raise ValueError("normalisation must be finite and std must be positive")
        if self.binary and (self.format != "classifier" or len(self.classes) != 2 or self.logits):
            raise ValueError("binary requires a two-class probability classifier")
        if self.logits and self.format != "classifier":
            raise ValueError("logits is only supported for classifiers")
        if self.format == "obico" and (self.layout != "NCHW" or self.resize != "stretch"):
            raise ValueError("Obico exports require NCHW input and stretch resizing")
        return self

    @classmethod
    def load(cls, model_dir: Path) -> ModelProfile | None:
        """Loads model.json when a custom model is selected.

        Args:
            model_dir: Directory containing model.json and its ONNX file.

        Returns:
            A validated profile, or None for the bundled encoder.
        """
        path = model_dir / "model.json"
        return cls.model_validate_json(path.read_text()) if path.exists() else None

    def model_path(self, model_dir: Path) -> Path:
        """Resolves the configured model inside its directory.

        Args:
            model_dir: Root of the model bundle.

        Returns:
            Existing ONNX file.

        Raises:
            ValueError: The path escapes the bundle or does not exist.
        """
        path = (model_dir / self.file).resolve()
        if not path.is_relative_to(model_dir.resolve()) or not path.is_file():
            raise ValueError("model file must exist inside MODEL_DIR")
        return path

    @property
    def runtime(self) -> str:
        """Returns the runtime required by this model file."""
        return "litert" if Path(self.file).suffix.lower() == ".tflite" else "onnx"

    @property
    def input_shape(self) -> tuple[int, ...]:
        """Returns the configured single-frame tensor dimensions."""
        return (1, 3, self.height, self.width) if self.layout == "NCHW" else (1, self.height, self.width, 3)

    @property
    def selected_output(self) -> int:
        """Returns the output containing class scores, including Obico's second output."""
        return self.output_index if self.output_index is not None else (1 if self.format == "obico" else 0)

    def preprocess(self, rgb: np.ndarray) -> np.ndarray:
        """Resizes and normalises an RGB image according to the model contract.

        Args:
            rgb: Camera frame in HxWx3 RGB order, with values from 0 to 255.

        Returns:
            Contiguous float32 tensor in the configured layout.

        Raises:
            ValueError: The frame has no usable RGB image.
        """
        if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) == 0:
            raise ValueError(f"expected non-empty HxWx3 RGB frame, got {rgb.shape}")
        width, height = self.width, self.height
        if self.resize == "letterbox":
            ratio = min(width / rgb.shape[1], height / rgb.shape[0])
            width, height = max(1, round(rgb.shape[1] * ratio)), max(1, round(rgb.shape[0] * ratio))
        if self.resize == "center_crop":
            ratio = max(width / rgb.shape[1], height / rgb.shape[0])
            width, height = max(width, int(rgb.shape[1] * ratio)), max(height, int(rgb.shape[0] * ratio))
        arr = _bilinear_resize(rgb, width, height)
        if self.resize == "center_crop":
            top, left = (height - self.height) // 2, (width - self.width) // 2
            arr = arr[top:top + self.height, left:left + self.width]
        if self.resize == "letterbox":
            canvas = np.full((self.height, self.width, 3), 114, dtype=np.float32)
            top = (self.height - height) // 2
            left = (self.width - width) // 2
            canvas[top:top + height, left:left + width] = arr
            arr = canvas
        if self.channel_order == "BGR":
            arr = arr[:, :, ::-1]
        arr = (arr * self.scale - np.asarray(self.mean, dtype=np.float32)) / np.asarray(self.std, dtype=np.float32)
        if self.layout == "NCHW":
            arr = arr.transpose(2, 0, 1)
        return np.ascontiguousarray(arr[None], dtype=np.float32)

    def classify(self, output: np.ndarray) -> dict[str, Any]:
        """Maps classifier probabilities or decoded detections to a failure confidence.

        Args:
            output: Selected ONNX output with its batch dimension removed.

        Returns:
            Classification result consumed by the shared engine.

        Raises:
            ValueError: Output shape, values or probabilities violate the profile.
        """
        if not np.isfinite(output).all():
            raise ValueError("model returned non-finite output")
        count = len(self.classes)
        failures = [self.classes.index(label) for label in self.failure_classes]
        if self.format == "classifier":
            if self.binary and output.shape == (1,):
                output = np.asarray([1.0 - output[0], output[0]])
            if output.shape != (count,):
                raise ValueError(f"classifier must return {count} class scores, got {output.shape}")
            scores = output.astype(np.float64)
            if self.logits:
                if count == 1:
                    scores = np.exp(-np.logaddexp(0, -scores))
                else:
                    scores = np.exp(scores - scores.max())
                    scores /= scores.sum()
            _probabilities(scores)
            if count > 1 and not np.isclose(scores.sum(), 1.0, atol=1e-4):
                raise ValueError("classifier probabilities must sum to one; use logits for raw logits")
            probability = float(scores[failures].sum())
        else:
            if self.format == "yolo_v8":
                if output.ndim != 2 or output.shape[0] != 4 + count:
                    raise ValueError(f"YOLOv8 output must have shape [4 + classes, detections], got {output.shape}")
                scores = output[4:].T
            elif self.format == "yolo_v5":
                if output.ndim != 2 or output.shape[1] != 5 + count:
                    raise ValueError(f"YOLOv5 output must have shape [detections, 5 + classes], got {output.shape}")
                _probabilities(output[:, 4:])
                scores = output[:, 5:] * output[:, 4:5]
            else:
                if output.ndim != 2 or output.shape[1] != count:
                    raise ValueError(f"Obico confidence output must have shape [detections, classes], got {output.shape}")
                scores = output
            _probabilities(scores)
            winners = scores.argmax(axis=1)
            selected = np.isin(winners, failures)
            probability = float(scores[np.arange(len(scores))[selected], winners[selected]].max(initial=0.0))
        return {
            "prediction": "failure" if probability >= 0.5 else "success",
            "distances": {},
            "margin": 0.0,
            "failure_probability": min(1.0, probability),
        }


def _probabilities(values: np.ndarray) -> None:
    if np.any((values < 0) | (values > 1)):
        raise ValueError("model confidence values must be between zero and one")


def _bilinear_resize(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    y = np.maximum(0, (np.arange(height) + 0.5) * rgb.shape[0] / height - 0.5)
    x = np.maximum(0, (np.arange(width) + 0.5) * rgb.shape[1] / width - 0.5)
    y0 = y.astype(int)
    x0 = x.astype(int)
    y1 = np.minimum(y0 + 1, rgb.shape[0] - 1)
    x1 = np.minimum(x0 + 1, rgb.shape[1] - 1)
    wy = (y - y0)[:, None, None]
    wx = (x - x0)[None, :, None]
    top = rgb[y0[:, None], x0[None, :]] * (1 - wx) + rgb[y0[:, None], x1[None, :]] * wx
    bottom = rgb[y1[:, None], x0[None, :]] * (1 - wx) + rgb[y1[:, None], x1[None, :]] * wx
    return (top * (1 - wy) + bottom * wy).astype(np.float32)
