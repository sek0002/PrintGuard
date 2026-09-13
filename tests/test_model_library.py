"""Installed model discovery and engine selection behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from printguard.engine.engine import Engine
from printguard.server.model_library import ModelLibrary
from tests.fakes import FakePlatform


def test_catalogue_marks_bad_profiles_unavailable_and_rejects_path_selection(tmp_path: Path) -> None:
    default = tmp_path / "bundled"
    default.mkdir()
    folder = tmp_path / "library" / "community"
    folder.mkdir(parents=True)
    library = ModelLibrary(default, folder.parent)
    assert library.entries[1]["error"] == "Missing model.json"
    with pytest.raises(ValueError):
        library.resolve("../bundled")
    (folder / "model.json").write_text(json.dumps({"file": "net.tflite", "format": "classifier", "classes": ["failure"], "failure_classes": ["failure"], "width": 2, "height": 2}))
    (folder / "net.tflite").touch()
    (folder / "info.json").write_text(json.dumps({"name": "Community beta", "status": "Beta", "license": "Not stated"}))
    library.refresh()
    assert library.resolve("community") == folder
    assert library.entries[1]["runtimes"] == ["auto", "litert"]
    (folder / "info.json").write_text(json.dumps({"source": "javascript:alert(1)"}))
    library.refresh()
    assert library.entries[1]["error"]


def model_platform() -> FakePlatform:
    """Builds a platform with one selectable community model."""
    platform = FakePlatform()
    platform.model_selection = True
    platform.models = [*platform.models, {"id": "community", "name": "Community", "runtimes": ["auto", "onnx"], "error": None}]
    return platform


async def test_selection_persists_and_runtime_changes_to_compatible_automatic() -> None:
    platform = model_platform()
    engine = Engine(platform)
    await engine.start()
    try:
        await engine.request({"cmd": "settings.update", "patch": {"inference_runtime": "litert"}})
        events = await engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}})
        assert engine.settings["model_id"] == "community"
        assert engine.settings["inference_runtime"] == "auto"
        assert any(e.get("model_selection") for e in events)
        assert platform.state["settings"]["model_id"] == "community"
        with pytest.raises(Exception):
            await engine.request({"cmd": "settings.update", "patch": {"model_id": "../elsewhere"}})
        assert engine.settings["model_id"] == "community"
    finally:
        await engine.stop()
    restored = Engine(platform)
    await restored.start()
    try:
        assert restored.settings["model_id"] == "community"
    finally:
        await restored.stop()


async def test_failed_switch_keeps_previous_settings_and_runtime(monkeypatch) -> None:
    platform = model_platform()
    engine = Engine(platform)
    await engine.start()
    async def fail(settings: dict) -> None:
        raise ValueError("Model validation failed")
    monkeypatch.setattr(platform, "configure", fail)
    try:
        with pytest.raises(Exception, match="Model validation failed"):
            await engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}})
        assert engine.settings["model_id"] == "default"
        assert platform.inference_runtime == "auto"
    finally:
        await engine.stop()


async def test_concurrent_settings_updates_do_not_lose_the_model_selection(monkeypatch) -> None:
    platform = model_platform()
    engine = Engine(platform)
    await engine.start()
    entered, proceed = asyncio.Event(), asyncio.Event()
    async def slow(settings: dict) -> None:
        entered.set()
        await proceed.wait()
    monkeypatch.setattr(platform, "configure", slow)
    try:
        switch = asyncio.create_task(engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}}))
        await entered.wait()
        theme = asyncio.create_task(engine.request({"cmd": "settings.update", "patch": {"theme": "light"}}))
        proceed.set()
        await asyncio.gather(switch, theme)
        assert engine.settings["model_id"] == "community"
        assert engine.settings["theme"] == "light"
    finally:
        await engine.stop()


def test_recommendations_cover_default_and_validate_slider_ranges(tmp_path: Path) -> None:
    folder = tmp_path / "library"
    folder.mkdir()
    preset = {"sensitivity": 1.0, "threshold": 0.35, "recommended": True, "summary": "Held-out frame evaluation; provisional.", "evaluated_at": "2026-09-13"}
    (folder / "recommendations.json").write_text(json.dumps({"default": preset}))
    library = ModelLibrary(tmp_path / "bundled", folder)
    assert library.entries[0]["recommendation"] == {**preset, "consecutive": None}
    preset["threshold"] = 0.0
    (folder / "recommendations.json").write_text(json.dumps({"default": preset}))
    with pytest.raises(ValueError):
        library.refresh()
    assert library.entries[0]["recommendation"]["threshold"] == 0.35


async def test_model_presets_apply_only_when_enabled_and_preserve_actions(monkeypatch) -> None:
    platform = model_platform()
    platform.models[1]["recommendation"] = {"sensitivity": 1.0, "threshold": 0.35}
    engine = Engine(platform)
    await engine.start()
    try:
        await engine.request({"cmd": "monitor.add", "monitor": {"sensitivity": 2.5, "threshold": 0.75, "on_defect": "none", "consecutive": 7}})
        mid = next(iter(engine.monitors))
        await engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}})
        assert engine.monitors[mid]["threshold"] == 0.75
        await engine.request({"cmd": "settings.update", "patch": {"model_presets_enabled": True}})
        assert engine.monitors[mid]["threshold"] == 0.35
        assert engine.monitors[mid]["sensitivity"] == 1.0
        assert engine.monitors[mid]["consecutive"] == 7
        assert engine.monitors[mid]["on_defect"] == "none"
        await engine.request({"cmd": "settings.update", "patch": {"model_id": "default"}})
        await engine.request({"cmd": "monitor.update", "id": mid, "patch": {"threshold": 0.9}})
        await engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}})
        assert engine.monitors[mid]["threshold"] == 0.35
        assert platform.state["settings"]["model_presets_enabled"] is True
        await engine.request({"cmd": "settings.update", "patch": {"model_presets_enabled": False}})
        await engine.request({"cmd": "monitor.update", "id": mid, "patch": {"threshold": 0.9}})
        await engine.request({"cmd": "settings.update", "patch": {"model_id": "default"}})
        await engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}})
        assert engine.monitors[mid]["threshold"] == 0.9
    finally:
        await engine.stop()


async def test_failed_switch_does_not_apply_preset(monkeypatch) -> None:
    platform = model_platform()
    platform.models[1]["recommendation"] = {"sensitivity": 1.0, "threshold": 0.35}
    engine = Engine(platform)
    await engine.start()
    try:
        await engine.request({"cmd": "monitor.add", "monitor": {"threshold": 0.8}})
        await engine.request({"cmd": "settings.update", "patch": {"model_presets_enabled": True}})
        async def fail(settings: dict) -> None:
            raise ValueError("Broken model")
        monkeypatch.setattr(platform, "configure", fail)
        with pytest.raises(RuntimeError, match="Broken model"):
            await engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}})
        assert next(iter(engine.monitors.values()))["threshold"] == 0.8
        assert engine.settings["model_id"] == "default"
    finally:
        await engine.stop()


async def test_consecutive_presets_switch_and_legacy_preserves_count() -> None:
    platform = model_platform()
    platform.models[1]["recommendation"] = {"sensitivity": 1.0, "threshold": 0.4, "consecutive": 6}
    engine = Engine(platform)
    await engine.start()
    try:
        await engine.request({"cmd": "monitor.add", "monitor": {"consecutive": 3, "on_defect": "none"}})
        await engine.request({"cmd": "settings.update", "patch": {"model_presets_enabled": True, "model_id": "community"}})
        monitor = next(iter(engine.monitors.values()))
        assert monitor["consecutive"] == 6
        assert monitor["on_defect"] == "none"
        await engine.request({"cmd": "settings.update", "patch": {"model_id": "default"}})
        assert monitor["consecutive"] == 6
        await engine.request({"cmd": "settings.update", "patch": {"model_presets_enabled": False}})
        await engine.request({"cmd": "monitor.update", "id": monitor["id"], "patch": {"consecutive": 4}})
        await engine.request({"cmd": "settings.update", "patch": {"model_id": "community"}})
        assert engine.monitors[monitor["id"]]["consecutive"] == 4
    finally:
        await engine.stop()


@pytest.mark.parametrize("count", [0, 31, 2.5, True])
def test_invalid_consecutive_preset_rejected(count) -> None:
    from printguard.server.model_recommendation import ModelRecommendation
    with pytest.raises(ValueError):
        ModelRecommendation(sensitivity=1.0, threshold=0.4, consecutive=count, recommended=False, summary="Trial", evaluated_at="2026-09-13")
