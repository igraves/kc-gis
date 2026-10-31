import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import "./style.css";

// MapLibre locates its worker relative to its own module URL, which breaks once
// Vite bundles it; point it at the worker file explicitly.
maplibregl.setWorkerUrl(workerUrl);

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const DEFAULT_VISIBLE = new Set(["county_boundary", "cities"]);
const HIDDEN_FIELDS = /^(shape|globalid|objectid|fid|created_|last_edited|shape__)/i;

const map = new maplibregl.Map({
  container: "map",
  style: BASEMAP,
  center: [-94.35, 38.99],
  zoom: 9.3,
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }));

const loaded = new Map(); // layer name -> list of map layer ids
const entriesByLayerId = new Map(); // map layer id -> manifest entry
const layerData = new Map(); // layer name -> GeoJSON, for zooming

// Keep polygons under lines under points regardless of toggle order.
function addAnchors() {
  map.addSource("anchors", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  for (const id of ["anchor-lines", "anchor-points"]) {
    map.addLayer({ id, type: "circle", source: "anchors" });
  }
}

function styleLayers(entry) {
  const { name, color, geometry_type: geom } = entry;
  if (geom === "esriGeometryPolygon") {
    const outlineOnly = name === "county_boundary";
    return [
      [{ id: `${name}-fill`, type: "fill", paint: { "fill-color": color, "fill-opacity": outlineOnly ? 0 : 0.2 } }, "anchor-lines"],
      [{ id: `${name}-line`, type: "line", paint: { "line-color": color, "line-width": outlineOnly ? 2.5 : 1 } }, "anchor-lines"],
    ];
  }
  if (geom === "esriGeometryPolyline") {
    return [[{ id: `${name}-line`, type: "line", paint: { "line-color": color, "line-width": 1.5 } }, "anchor-points"]];
  }
  return [[{
    id: `${name}-point`,
    type: "circle",
    paint: {
      "circle-color": color,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 14, 6],
      "circle-stroke-color": "#fff",
      "circle-stroke-width": 1,
    },
  }, undefined]];
}

async function showLayer(entry, visible) {
  if (!loaded.has(entry.name)) {
    if (!visible) return;
    const data = await fetch(`/${entry.file}`).then((r) => r.json());
    layerData.set(entry.name, data);
    map.addSource(entry.name, { type: "geojson", data });
    const ids = [];
    for (const [layer, beforeId] of styleLayers(entry)) {
      map.addLayer({ ...layer, source: entry.name }, beforeId);
      ids.push(layer.id);
      entriesByLayerId.set(layer.id, entry);
      map.on("mouseenter", layer.id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer.id, () => (map.getCanvas().style.cursor = ""));
    }
    loaded.set(entry.name, ids);
  }
  for (const id of loaded.get(entry.name)) {
    map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Popup cell HTML: links for URLs, readable dates for ArcGIS epoch-millisecond fields.
function formatValue(key, value) {
  if (typeof value === "string" && /^https?:\/\//.test(value)) {
    return `<a href="${escapeHtml(value)}" target="_blank" rel="noopener">${escapeHtml(value.replace(/^https?:\/\//, ""))}</a>`;
  }
  if (typeof value === "number" && /date/i.test(key) && value > 1e11) {
    return escapeHtml(new Date(value).toLocaleDateString());
  }
  return escapeHtml(value);
}

// One handler for all layers: show the topmost feature under the cursor.
function showPopup(e) {
  const [feature] = map.queryRenderedFeatures(e.point, { layers: [...entriesByLayerId.keys()] });
  if (!feature) return;
  const entry = entriesByLayerId.get(feature.layer.id);
  const rows = Object.entries(feature.properties)
    .filter(([k, v]) => !HIDDEN_FIELDS.test(k) && v !== null && v !== "")
    .map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${formatValue(k, v)}</td></tr>`)
    .join("");
  new maplibregl.Popup({ maxWidth: "360px" })
    .setLngLat(e.lngLat)
    .setHTML(`<strong>${escapeHtml(entry.title)}</strong><table>${rows}</table>`)
    .addTo(map);
}

const toggles = new Map(); // layer name -> checkbox input

function layerToggle(entry) {
  const label = document.createElement("label");
  label.className = "layer";
  label.title = `${entry.url}\nfetched ${entry.fetched_at}`;
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = DEFAULT_VISIBLE.has(entry.name);
  const swatch = document.createElement("span");
  swatch.className = "swatch";
  swatch.style.background = entry.color;
  const title = document.createElement("span");
  title.textContent = entry.title;
  const count = document.createElement("span");
  count.className = "count";
  count.textContent = entry.count.toLocaleString();
  label.append(input, swatch, title, count);

  input.addEventListener("change", async () => {
    label.classList.add("loading");
    await showLayer(entry, input.checked);
    label.classList.remove("loading");
  });
  toggles.set(entry.name, input);
  return label;
}

function boundsOf(geojson) {
  const bounds = new maplibregl.LngLatBounds();
  const visit = (c) => (typeof c[0] === "number" ? bounds.extend(c) : c.forEach(visit));
  geojson.features.forEach((f) => f.geometry && visit(f.geometry.coordinates));
  return bounds;
}

// Turn a layer on and fit the map to it.
async function zoomTo(entry) {
  const input = toggles.get(entry.name);
  if (input && !input.checked) {
    input.checked = true;
    await showLayer(entry, true);
  }
  map.fitBounds(boundsOf(layerData.get(entry.name)), { padding: 40 });
}

// Header for a themed group: description, zoom button, and plan documents.
function groupHeader(group, manifest) {
  const header = document.createElement("div");
  header.className = "group";
  if (group.description) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = group.description;
    header.append(p);
  }
  const zoomEntry = manifest.layers[group.zoom_layer];
  if (zoomEntry) {
    const button = document.createElement("button");
    button.textContent = `Zoom to ${zoomEntry.title}`;
    button.addEventListener("click", () => zoomTo(zoomEntry));
    header.append(button);
  }
  if (group.documents?.length) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `Plans & documents (${group.documents.length})`;
    const list = document.createElement("ul");
    for (const doc of group.documents) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = doc.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = doc.title;
      li.append(a);
      list.append(li);
    }
    details.append(summary, list);
    header.append(details);
  }
  return header;
}

function renderPanel(manifest) {
  const container = document.getElementById("layers");
  const groupMeta = manifest.groups ?? {};
  // Themed groups (e.g. Blue River) get their own heading; everything else groups by server.
  const sections = Object.groupBy(Object.values(manifest.layers), (l) => (groupMeta[l.group] ? `group:${l.group}` : l.server));
  for (const [key, entries] of Object.entries(sections)) {
    const group = key.startsWith("group:") ? groupMeta[key.slice(6)] : null;
    const h2 = document.createElement("h2");
    h2.textContent = group ? group.title : key;
    container.append(h2);
    if (group) container.append(groupHeader(group, manifest));
    for (const entry of entries) {
      container.append(layerToggle(entry));
      if (toggles.get(entry.name).checked) toggles.get(entry.name).dispatchEvent(new Event("change"));
    }
  }
}

map.on("click", showPopup);

map.on("load", async () => {
  addAnchors();
  const res = await fetch("/manifest.json");
  if (!res.ok) {
    document.getElementById("layers").textContent = "No data yet — run `uv run kcgis fetch` first.";
    return;
  }
  const manifest = await res.json();
  document.getElementById("updated").textContent = `Data updated ${new Date(manifest.updated_at).toLocaleString()}`;
  renderPanel(manifest);
});
