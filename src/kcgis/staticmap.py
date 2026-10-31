"""Render fetched layers to a static PNG/SVG/PDF map (requires the `maps` extra)."""

from __future__ import annotations

from pathlib import Path

from .sources import Registry


def render_map(
    registry: Registry,
    data_dir: Path,
    layer_names: list[str],
    out: Path,
    title: str | None = None,
    column: str | None = None,
    extent: str | None = None,
) -> Path:
    """Draw layers (first at the bottom) over the county outline, framed to `extent` or the county."""
    try:
        import geopandas as gpd
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("static maps need the maps extra: uv sync --extra maps") from exc

    def load(name: str):
        path = data_dir / f"{name}.geojson"
        if not path.exists():
            raise SystemExit(f"{name} has not been fetched; run: kcgis fetch {name}")
        return gpd.read_file(path).to_crs(3857)

    fig, ax = plt.subplots(figsize=(11, 11))
    boundary = load(registry.boundary_layer)

    for i, name in enumerate(layer_names):
        layer = registry.layers[name]
        gdf = load(name)
        if gdf.empty:
            continue
        geom = gdf.geom_type.iloc[0]
        # Choropleth on the first layer when a column is given.
        if column and i == 0:
            gdf.plot(ax=ax, column=column, cmap="viridis", legend=True, linewidth=0.2, edgecolor="white",
                     legend_kwds={"label": column, "shrink": 0.6})
        elif "Polygon" in geom:
            gdf.plot(ax=ax, color=layer.color, alpha=0.35, edgecolor=layer.color, linewidth=0.5, label=layer.title)
        elif "Line" in geom:
            gdf.plot(ax=ax, color=layer.color, linewidth=0.8, label=layer.title)
        else:
            gdf.plot(ax=ax, color=layer.color, markersize=8, label=layer.title)

    boundary.boundary.plot(ax=ax, color="#222222", linewidth=1.2)
    xmin, ymin, xmax, ymax = (load(extent) if extent else boundary).total_bounds
    pad = (xmax - xmin) * 0.03
    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)
    ax.set_axis_off()
    ax.set_title(title or f"{registry.county_name}: " + ", ".join(registry.layers[n].title for n in layer_names))
    if not column and ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    sources = sorted({registry.servers[registry.layers[n].source].title for n in [registry.boundary_layer, *layer_names]})
    fig.text(0.01, 0.01, "Data: " + "; ".join(sources), fontsize=8, color="#555555")

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out
