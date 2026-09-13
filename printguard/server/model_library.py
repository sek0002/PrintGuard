"""Discovers installed model bundles without importing community Python code."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .model_profile import ModelProfile
from .model_recommendation import ModelRecommendation


class ModelLibrary:
    """Indexes the bundled detector and installed model profiles."""

    def __init__(self, default_dir: Path, library_dir: Path) -> None:
        self.default_dir = default_dir
        self.library_dir = library_dir
        self.entries: list[dict[str, Any]] = []
        self._paths: dict[str, Path] = {}
        self.refresh()

    def refresh(self) -> None:
        """Rebuilds the catalogue, reporting invalid bundles as unavailable."""
        paths = {"default": self.default_dir}
        if self.library_dir.exists():
            paths.update({p.name: p for p in sorted(self.library_dir.iterdir())
                          if p.is_dir() and not p.is_symlink() and p.name != "default"
                          and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", p.name)})
        preset_path = self.library_dir / "recommendations.json"
        presets = json.loads(preset_path.read_text()) if preset_path.exists() else {}
        if not isinstance(presets, dict):
            raise ValueError("recommendations.json must contain a model-id mapping")
        recommendations = {key: ModelRecommendation.model_validate(value).model_dump() for key, value in presets.items()}
        entries = []
        for model_id, path in paths.items():
            entry: dict[str, Any] = {
                "id": model_id, "name": "PrintGuard bundled" if model_id == "default" else model_id,
                "source": "", "license": "Not stated", "status": "Community", "notes": "",
                "runtimes": ["auto", "onnx", "litert"], "error": None,
                "recommendation": recommendations.get(model_id),
            }
            try:
                profile = ModelProfile.load(path)
                if model_id != "default" and profile is None:
                    raise ValueError("Missing model.json")
                if profile:
                    profile.model_path(path)
                    entry["runtimes"] = ["auto", profile.runtime]
                else:
                    entry.update(license="GPL-2.0-only repository", status="Bundled")
                info_path = path / "info.json"
                if info_path.exists():
                    info = json.loads(info_path.read_text())
                    if not isinstance(info, dict):
                        raise ValueError("info.json must contain an object")
                    for key in ("name", "source", "license", "status", "notes"):
                        if key in info:
                            if not isinstance(info[key], str):
                                raise ValueError(f"info.json {key} must be text")
                            entry[key] = info[key]
                    if entry["source"] and not entry["source"].startswith(("https://", "http://")):
                        raise ValueError("Source must be an HTTP or HTTPS URL")
            except (ValueError, OSError) as exc:
                entry["error"] = str(exc)
            entries.append(entry)
        self._paths = paths
        self.entries = entries

    def resolve(self, model_id: str) -> Path:
        """Returns a valid installed bundle selected by its catalogue identifier.

        Args:
            model_id: Identifier from the published catalogue.

        Raises:
            ValueError: The selection is missing or invalid.
        """
        entry = next((item for item in self.entries if item["id"] == model_id), None)
        if entry is None or entry["error"]:
            raise ValueError(f"Model is unavailable: {model_id}")
        return self._paths[model_id]
