"""kcgis command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .arcgis import ArcGISClient, ArcGISError, walk_catalog
from .fetch import DEFAULT_DATA_DIR, Fetcher
from .osm import OSMError
from .sources import DEFAULT_REGISTRY, load_registry
from .usgs import USGSError


def cmd_list(args, registry) -> int:
    manifest = Fetcher(registry, args.data_dir).load_manifest()["layers"]
    for layer in registry.layers.values():
        local = manifest.get(layer.name)
        fetched = f"{local['count']:>7} features  {local['fetched_at']}" if local else "      — not fetched"
        flags = " [large]" if layer.large else ""
        print(f"{layer.name:24} {layer.source:8} {fetched}  {layer.title}{flags}")
    return 0


def cmd_fetch(args, registry) -> int:
    layers = registry.select(args.layers, include_large=args.include_large)
    fetcher = Fetcher(registry, args.data_dir)
    failures = 0
    for layer in layers:
        print(f"fetching {layer.name} ({registry.servers[layer.source].title})…")
        try:
            entry = fetcher.fetch(layer)
        except (ArcGISError, OSMError, USGSError) as exc:
            failures += 1
            print(f"  FAILED: {exc}", file=sys.stderr)
            continue
        note = "changed" if entry["changed"] else "unchanged"
        print(f"  {entry['count']} features, {entry['bytes'] / 1e6:.1f} MB ({note})")
    return 1 if failures else 0


def cmd_status(args, registry) -> int:
    fetcher = Fetcher(registry, args.data_dir)
    for layer in registry.select(args.layers, include_large=args.include_large):
        try:
            s = fetcher.status(layer)
        except ArcGISError as exc:
            print(f"{layer.name:24} ERROR {exc}")
            continue
        print(f"{s['layer']:24} {s['state']:17} local={s['local_count']} remote={s['remote_count']}"
              f"  last_edit={s['source_last_edit'] or '?'}")
    return 0


def cmd_catalog(args, registry) -> int:
    root = registry.servers[args.server].root if args.server in registry.servers else args.server
    known = {l.url for l in registry.layers.values()}
    client = ArcGISClient()
    for service, kind, layer in walk_catalog(client, root):
        if args.grep and args.grep.lower() not in f"{service} {layer['name']}".lower():
            continue
        url = f"{root}/{service}/{kind}/{layer['id']}"
        mark = "*" if url in known else " "
        geom = layer["geometryType"].removeprefix("esriGeometry")
        print(f"{mark} {geom:9} {service}/{kind}/{layer['id']}  {layer['name']}")
    return 0


def cmd_map(args, registry) -> int:
    from .staticmap import render_map

    unknown = [n for n in [*args.layers, args.extent] if n and n not in registry.layers]
    if unknown:
        raise SystemExit(f"unknown layer(s): {', '.join(unknown)}")
    out = args.output or Path("maps") / f"{'_'.join(args.layers)}.png"
    print(render_map(registry, args.data_dir, args.layers, out, title=args.title, column=args.column, extent=args.extent))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kcgis", description="Jackson County, MO GIS data tracker")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="path to sources.toml")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list registry layers and local fetch state").set_defaults(func=cmd_list)

    for name, func, help_ in [
        ("fetch", cmd_fetch, "download layers to data/ (default: all non-large layers)"),
        ("status", cmd_status, "check remote layers for changes since the last fetch"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("layers", nargs="*")
        p.add_argument("--include-large", action="store_true", help="include large layers such as parcels")
        p.set_defaults(func=func)

    p = sub.add_parser("catalog", help="list every layer on a server ('*' = already in registry)")
    p.add_argument("server", help="registry server key (jackson, marc) or a REST services root URL")
    p.add_argument("--grep", help="filter by service or layer name")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("map", help="render fetched layers to a static image")
    p.add_argument("layers", nargs="+")
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--title")
    p.add_argument("--column", help="choropleth column for the first layer")
    p.add_argument("--extent", help="frame the map to this layer instead of the county")
    p.set_defaults(func=cmd_map)

    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
        return args.func(args, registry)
    except KeyError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
