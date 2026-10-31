"""Load the layer registry from sources.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PROJECT_ROOT / "sources.toml"


@dataclass(frozen=True)
class Server:
    key: str
    title: str
    root: str
    homepage: str = ""


@dataclass(frozen=True)
class Group:
    """A themed set of layers (e.g. a plan area) shown together in the viewer."""

    key: str
    title: str
    description: str = ""
    zoom_layer: str = ""
    documents: list[dict[str, str]] = field(default_factory=list)


LAYER_KINDS = ("arcgis", "usgs_gauges")


@dataclass(frozen=True)
class Layer:
    name: str
    source: str
    title: str
    url: str
    kind: str = "arcgis"
    where: str = "1=1"
    fields: str = "*"
    clip: bool | str = False  # True = county boundary, or the name of a polygon layer
    large: bool = False
    simplify: float | None = None
    group: str = ""
    color: str = "#3388ff"

    @property
    def clip_layer(self) -> str | None:
        """Name of the layer whose geometry clips this one, if any (True means the county boundary)."""
        return self.clip if isinstance(self.clip, str) else None


@dataclass
class Registry:
    county_name: str
    county_fips: str
    boundary_layer: str
    servers: dict[str, Server] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    layers: dict[str, Layer] = field(default_factory=dict)

    def select(self, names: list[str] | None = None, include_large: bool = False) -> list[Layer]:
        """Named layers in order, or every non-large layer when names is empty."""
        if names:
            unknown = [n for n in names if n not in self.layers]
            if unknown:
                raise KeyError(f"unknown layer(s): {', '.join(unknown)}")
            return [self.layers[n] for n in names]
        return [l for l in self.layers.values() if include_large or not l.large]


def load_registry(path: Path = DEFAULT_REGISTRY) -> Registry:
    raw = tomllib.loads(path.read_text())
    county = raw["county"]
    reg = Registry(
        county_name=county["name"],
        county_fips=county["fips"],
        boundary_layer=county["boundary_layer"],
        servers={k: Server(key=k, **v) for k, v in raw.get("servers", {}).items()},
        groups={k: Group(key=k, **v) for k, v in raw.get("groups", {}).items()},
        layers={k: Layer(name=k, **v) for k, v in raw.get("layers", {}).items()},
    )
    for layer in reg.layers.values():
        if layer.source not in reg.servers:
            raise ValueError(f"layer {layer.name}: unknown source {layer.source!r}")
        if layer.kind not in LAYER_KINDS:
            raise ValueError(f"layer {layer.name}: unknown kind {layer.kind!r}")
        if layer.group and layer.group not in reg.groups:
            raise ValueError(f"layer {layer.name}: unknown group {layer.group!r}")
        if layer.kind == "usgs_gauges" and not layer.clip_layer:
            raise ValueError(f"layer {layer.name}: usgs_gauges needs clip = \"<polygon layer>\"")
        if layer.clip_layer and layer.clip_layer not in reg.layers:
            raise ValueError(f"layer {layer.name}: unknown clip layer {layer.clip_layer!r}")
    for group in reg.groups.values():
        if group.zoom_layer and group.zoom_layer not in reg.layers:
            raise ValueError(f"group {group.key}: unknown zoom_layer {group.zoom_layer!r}")
    if reg.boundary_layer not in reg.layers:
        raise ValueError(f"boundary_layer {reg.boundary_layer!r} is not a defined layer")
    return reg
