"""Selectable LiteRT and ONNX inference for the hub platform."""

from __future__ import annotations

import asyncio
import ctypes
import importlib
import importlib.util
import logging
import os
import sys
import sysconfig
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import onnxruntime as ort
from ai_edge_litert.interpreter import Interpreter

from .model_profile import ModelProfile

InferenceRuntime = Literal["auto", "litert", "onnx"]
Model = Callable[[np.ndarray], np.ndarray]

BENCHMARK_RUNS = 10
BENCHMARK_TENSOR = np.zeros((1, 3, 224, 224), dtype=np.float32)
SCALING_GAIN = 1.1
PLUGIN_MODULES = ("onnxruntime_ep_nv_tensorrt_rtx", "onnxruntime_ep_openvino")
CUDA_RUNTIME_LIBRARY = "nvidia/cuda_runtime/lib/libcudart.so.12"
WINDOWS_PROVIDERS = {
    "MIGraphXExecutionProvider",
    "NvTensorRtRtxExecutionProvider",
    "OpenVINOExecutionProvider",
    "QNNExecutionProvider",
    "VitisAIExecutionProvider",
}
DEFAULT_CPU_PROVIDER = "CPUExecutionProvider"
DEVICE_PRIORITY = ("GPU", "NPU", "CPU")
REGISTERED_LIBRARIES: set[str] = set()
logger = logging.getLogger(__name__)


def _preload_cuda_runtime() -> None:
    """Loads the CUDA runtime that the TensorRT RTX provider library links against.

    The NVIDIA Container Toolkit injects the driver, not the runtime, and the wheel
    carrying `libcudart.so.12` installs it under site-packages where the dynamic linker
    does not look, so the provider library cannot open without it being loaded first.
    """
    library = Path(sysconfig.get_paths()["purelib"], CUDA_RUNTIME_LIBRARY)
    if library.exists():
        ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)


def _register_library(name: str, path: str) -> bool:
    """Registers a provider library with ONNX Runtime, reporting whether it is usable.

    A provider whose libraries the host cannot supply, such as a GPU image started
    without the drivers passed through, leaves inference on the CPU rather than
    stopping PrintGuard from starting.
    """
    if name in REGISTERED_LIBRARIES:
        return True
    try:
        ort.register_execution_provider_library(name, path)
    except Exception as exc:
        logger.warning("execution provider %s is unavailable: %s", name, exc)
        return False
    REGISTERED_LIBRARIES.add(name)
    return True


def _device_rank(device: ort.OrtEpDevice) -> tuple[int, bool]:
    """Orders one provider device by the throughput its hardware can be expected to reach."""
    return DEVICE_PRIORITY.index(device.device.type.name), device.device.metadata.get("Discrete") != "1"


def _execution_devices(devices: list[ort.OrtEpDevice]) -> list[ort.OrtEpDevice]:
    """Returns the hardware the registered providers offer, fastest first.

    A provider registers one device per piece of hardware it can reach, so a GPU
    missing from this list is one the host's driver never handed to the provider,
    which is the difference between a GPU that is unusable and one that is merely
    slower. ONNX Runtime's own selection policies pick a device without saying which,
    and OpenVINO's meta devices choose again at inference time, so neither can name
    the hardware actually in use: the choice is made here instead, where it is named
    in the `compute` readout and in the log.
    """
    return sorted(
        (
            device
            for device in devices
            if device.ep_name != DEFAULT_CPU_PROVIDER and "ov_meta_device" not in device.ep_metadata
        ),
        key=_device_rank,
    )


def _device_label(device: ort.OrtEpDevice) -> str:
    """Names the hardware behind a provider device, for example `Intel GPU`."""
    return f"{device.ep_vendor} {device.device.type.name}"


def _throughput(model: Model, workers: int) -> float:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda _: model(BENCHMARK_TENSOR), range(workers)))
        started = time.perf_counter()
        list(pool.map(lambda _: [model(BENCHMARK_TENSOR) for _ in range(BENCHMARK_RUNS)], range(workers)))
        elapsed = time.perf_counter() - started
    return workers * BENCHMARK_RUNS / elapsed


def _measure_concurrency(model: Model) -> tuple[int, float]:
    """Returns the worker count where throughput stops growing, and that throughput.

    Concurrency is measured rather than derived from the core count because how far
    a runtime scales depends on the execution provider, on whether its Python
    binding releases the GIL, and on any CPU quota the container is under. Doubling
    from one worker and stopping at the first step that fails to pay for itself
    lands on the host's real ceiling in a handful of measurements.
    """
    ceiling = os.cpu_count() or 2
    best, best_fps, workers = 1, 0.0, 1
    while True:
        fps = _throughput(model, workers)
        if fps < best_fps * SCALING_GAIN:
            return best, best_fps
        best, best_fps = workers, fps
        if workers >= ceiling:
            return best, best_fps
        workers = min(workers * 2, ceiling)


class OnnxInference:
    """Runs the ONNX model through the fastest available execution provider.

    Core ML compiles the model on every session rather than into a cache directory.
    Its cache lookup builds the model URL with `NSURL URLWithString`, which yields
    nil for any path holding a space, so a cache under `~/Library/Application
    Support` fails every session it is meant to speed up - and the desktop app,
    which is where that path is used, could not start at all. Compiling costs
    about 0.2s per session.
    """

    runtime = "onnx"

    def __init__(self, model_path: Path, profile: ModelProfile | None = None) -> None:
        self._single_score = profile is not None and profile.format == "classifier" and len(profile.classes) == 1
        self._resources = ExitStack()
        self._register_plugins()
        if sys.platform == "win32":
            self._register_windows_providers()

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        devices = _execution_devices(ort.get_ep_devices())
        if devices:
            logger.info("execution providers offer: %s", ", ".join(_device_label(device) for device in devices))
            options.add_provider_for_devices(devices[:1], {})
            self._session = ort.InferenceSession(str(model_path), sess_options=options)
            self.device = _device_label(devices[0])
        elif "CoreMLExecutionProvider" in ort.get_available_providers():
            providers = [
                (
                    "CoreMLExecutionProvider",
                    {"ModelFormat": "MLProgram", "MLComputeUnits": "ALL", "RequireStaticInputShapes": "1"},
                ),
                DEFAULT_CPU_PROVIDER,
            ]
            self._session = ort.InferenceSession(str(model_path), sess_options=options, providers=providers)
            self.device = "Apple Core ML"
        else:
            self._session = ort.InferenceSession(
                str(model_path), sess_options=options, providers=[DEFAULT_CPU_PROVIDER]
            )
            self.device = "ONNX CPU"

        inputs = self._session.get_inputs()
        self._input_name = inputs[0].name
        self._output_name = self._session.get_outputs()[0].name
        if profile is not None:
            try:
                if len(inputs) != 1 or inputs[0].type != "tensor(float)":
                    raise ValueError("custom models must have one float32 image input")
                shape = inputs[0].shape
                if len(shape) != 4 or any(
                    isinstance(actual, int) and actual != expected
                    for actual, expected in zip(shape, profile.input_shape)
                ):
                    raise ValueError(f"model input {shape} does not match profile {profile.input_shape}")
                outputs = self._session.get_outputs()
                if profile.selected_output >= len(outputs):
                    raise ValueError("profile output_index does not exist in the ONNX model")
                self._output_name = outputs[profile.selected_output].name
            except Exception:
                self.close()
                raise

    def _register_plugins(self) -> None:
        _preload_cuda_runtime()
        for module_name in PLUGIN_MODULES:
            if importlib.util.find_spec(module_name) is None:
                continue
            module = importlib.import_module(module_name)
            _register_library(module_name, module.get_library_path())

    def _register_windows_providers(self) -> None:
        if sys.getwindowsversion().build < 26100:
            return
        from winui3.microsoft.windows.applicationmodel.dynamicdependency.bootstrap import InitializeOptions, initialize
        import winui3.microsoft.windows.ai.machinelearning as winml

        self._resources.enter_context(initialize(options=InitializeOptions.ON_NO_MATCH_SHOW_UI))
        providers = [
            provider
            for provider in winml.ExecutionProviderCatalog.get_default().find_all_providers()
            if provider.name in WINDOWS_PROVIDERS
        ]
        for provider in providers:
            if provider.ready_state != winml.ExecutionProviderReadyState.READY:
                result = provider.ensure_ready_async().get()
                if result.status != winml.ExecutionProviderReadyResultState.SUCCESS:
                    continue
            _register_library(provider.name, provider.library_path)

    def run(self, tensor: np.ndarray) -> np.ndarray:
        """Returns the selected model output for one preprocessed frame."""
        output = self._session.run([self._output_name], {self._input_name: tensor})[0]
        if self._single_score and output.shape == (1,):
            return output.copy()
        if output.ndim < 2 or output.shape[0] != 1:
            raise ValueError(f"model output must have a single-image batch dimension, got {output.shape}")
        return output[0].copy()

    def close(self) -> None:
        """Releases provider runtimes held for the session lifetime."""
        self._session = None
        self._resources.close()


class LiteRtInference:
    """Runs the LiteRT model on one single-threaded CPU interpreter per worker thread.

    `Interpreter.invoke` releases the GIL, so interpreters held per thread run
    genuinely in parallel; the `CompiledModel` API does not, and serialises every
    caller onto one core no matter how many workers are given to it.
    """

    runtime = "litert"
    device = "LiteRT CPU"

    def __init__(self, model_path: Path, profile: ModelProfile | None = None) -> None:
        self._model_path = str(model_path)
        self._interpreters = threading.local()
        probe = Interpreter(model_path=self._model_path, num_threads=1)
        self._input_index = probe.get_input_details()[0]["index"]
        inputs, outputs = probe.get_input_details(), probe.get_output_details()
        selected = profile.selected_output if profile else 0
        if profile and (len(inputs) != 1 or inputs[0]["dtype"] != np.float32
                        or tuple(inputs[0]["shape"]) != profile.input_shape):
            raise ValueError("TFLite model must have one float32 input matching the profile")
        if selected >= len(outputs) or outputs[selected]["dtype"] != np.float32:
            raise ValueError("Selected TFLite output must exist and contain float32 scores")
        self._output_index = outputs[selected]["index"]

    def run(self, tensor: np.ndarray) -> np.ndarray:
        """Returns the selected model output for one preprocessed frame."""
        interpreter = getattr(self._interpreters, "interpreter", None)
        if interpreter is None:
            interpreter = Interpreter(model_path=self._model_path, num_threads=1)
            interpreter.allocate_tensors()
            self._interpreters.interpreter = interpreter
        interpreter.set_tensor(self._input_index, tensor)
        interpreter.invoke()
        return interpreter.get_tensor(self._output_index)[0].copy()

    def close(self) -> None:
        """Drops the per-thread interpreters."""
        self._interpreters = threading.local()


class Inference:
    """Runs the requested model runtime at the concurrency it measurably sustains."""

    def __init__(self, model_dir: Path, runtime: InferenceRuntime, profile: ModelProfile | None = None) -> None:
        candidates: list[OnnxInference | LiteRtInference] = []
        if runtime not in ("auto", "onnx", "litert"):
            raise ValueError(f"unsupported inference runtime: {runtime}")
        if profile is not None and runtime not in ("auto", profile.runtime):
            raise ValueError(f"custom {profile.runtime.upper()} models require Automatic or {profile.runtime.upper()} Runtime in Settings")
        try:
            if runtime in ("auto", "onnx") and (profile is None or profile.runtime == "onnx"):
                path = profile.model_path(model_dir) if profile else model_dir / "encoder_float32.onnx"
                candidates.append(OnnxInference(path, profile))
            if runtime in ("auto", "litert") and (profile is None or profile.runtime == "litert"):
                path = profile.model_path(model_dir) if profile else model_dir / "encoder_float32.tflite"
                candidates.append(LiteRtInference(path, profile))
            benchmark = np.zeros(profile.input_shape, dtype=np.float32) if profile else BENCHMARK_TENSOR
            if profile is not None:
                profile.classify(candidates[0].run(benchmark))
            measured = [
                _measure_concurrency(lambda _, candidate=candidate: candidate.run(benchmark))
                for candidate in candidates
            ]
        except Exception:
            for candidate in candidates:
                candidate.close()
            raise
        logger.info(
            "inference benchmark: %s",
            ", ".join(
                f"{candidate.device} {fps:.1f} fps across {workers} workers"
                for candidate, (workers, fps) in zip(candidates, measured)
            ),
        )
        selected, (self.workers, self.capacity_fps) = max(zip(candidates, measured), key=lambda pair: pair[1][1])
        for candidate in candidates:
            if candidate is not selected:
                candidate.close()
        self._selected = selected
        self.runtime = selected.runtime
        self.device = selected.device
        self._pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="inference")

    async def run(self, tensor: np.ndarray) -> np.ndarray:
        """Returns the selected model output for one preprocessed frame."""
        return await asyncio.get_running_loop().run_in_executor(self._pool, self._selected.run, tensor)

    def close(self) -> None:
        """Releases the selected model runtime and its worker threads."""
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._selected.close()
