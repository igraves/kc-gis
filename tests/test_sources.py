import pytest

from kcgis.sources import load_registry


def test_project_registry_loads():
    reg = load_registry()
    assert reg.county_fips == "29095"
    assert reg.boundary_layer in reg.layers
    assert {"jackson", "marc"} <= reg.servers.keys()


def test_select_skips_large_layers_by_default():
    reg = load_registry()
    names = {l.name for l in reg.select()}
    assert "parcels" not in names
    assert "parcels" in {l.name for l in reg.select(include_large=True)}
    assert [l.name for l in reg.select(["parcels"])] == ["parcels"]


def test_select_unknown_layer():
    with pytest.raises(KeyError):
        load_registry().select(["nope"])


def test_blue_river_group():
    reg = load_registry()
    group = reg.groups["blue_river"]
    assert group.zoom_layer in reg.layers and group.documents
    members = [l for l in reg.layers.values() if l.group == "blue_river"]
    assert members and all(l.clip_layer is None or reg.layers[l.clip_layer].group == "blue_river" for l in members)
    # Private parcel owners must not be requested.
    assert "OWN_NAME" not in reg.layers["br_private_parcels"].fields


def test_rejects_unknown_clip_layer(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(
        '[county]\nname="X"\nfips="1"\nboundary_layer="b"\n[servers.s]\ntitle="S"\nroot="https://x"\n'
        '[layers.b]\nsource="s"\ntitle="B"\nurl="https://x/0"\nclip="nope"\n'
    )
    with pytest.raises(ValueError, match="unknown clip layer"):
        load_registry(path)


def test_rejects_unknown_source(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(
        '[county]\nname="X"\nfips="1"\nboundary_layer="b"\n'
        '[layers.b]\nsource="missing"\ntitle="B"\nurl="https://x/0"\n'
    )
    with pytest.raises(ValueError, match="unknown source"):
        load_registry(path)
