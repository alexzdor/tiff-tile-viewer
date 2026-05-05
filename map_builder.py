"""
map_builder.py — формирование HTML-страницы интерактивной карты.

Принимает параметры (bbox, zoom, центр, URL-префикс тайлов),
возвращает готовую HTML-строку — никаких файлов на диск не пишет.
"""

# URL CDN для Leaflet.js
LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"


def build_map_html(
    tiles_url_prefix: str,   # например "/tiles/abc123"
    bbox: list,              # [west, south, east, north]
    center: list,            # [lon, lat]
    zoom_min: int,
    zoom_max: int,
    session_id: str,
) -> str:
    """
    Генерирует и возвращает HTML-строку интерактивной карты.
    Тайлы загружаются по URL: tiles_url_prefix/{z}/{x}/{y}.png
    """
    west, south, east, north = bbox
    center_lat, center_lon = center[1], center[0]

    initial_zoom = max(zoom_min, zoom_max - 2)

    # Шаблон URL тайлов для Leaflet (фигурные скобки — его синтаксис)
    tiles_url = f"{tiles_url_prefix}/{{z}}/{{x}}/{{y}}.png"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Интерактивная карта — {session_id[:8]}</title>
  <link rel="stylesheet" href="{LEAFLET_CSS}" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #1a1a2e;
      color: #e0e0e0;
      height: 100vh;
      display: flex;
      flex-direction: column;
    }}

    header {{
      background: #16213e;
      padding: 10px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid #0f3460;
      flex-shrink: 0;
    }}

    header h1 {{ font-size: 1rem; font-weight: 600; color: #e94560; }}

    #meta-info {{ font-size: 0.75rem; color: #aaa; }}

    #map {{ flex: 1; }}

    #info-panel {{
      background: #16213e;
      padding: 8px 20px;
      font-size: 0.75rem;
      color: #aaa;
      border-top: 1px solid #0f3460;
      display: flex;
      gap: 20px;
      flex-shrink: 0;
    }}

    #download-btn {{
      margin-left: auto;
      background: #e94560;
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 4px 14px;
      font-size: 0.75rem;
      cursor: pointer;
      text-decoration: none;
    }}

    #download-btn:hover {{ opacity: 0.85; }}
  </style>
</head>
<body>
  <header>
    <h1>📍 Интерактивная карта аэрофотосъёмки</h1>
    <div id="meta-info">
      Охват: {west:.5f}° – {east:.5f}° (дол.) &nbsp;|&nbsp;
      {south:.5f}° – {north:.5f}° (шир.) &nbsp;|&nbsp;
      Уровни: {zoom_min}–{zoom_max}
    </div>
  </header>

  <div id="map"></div>

  <div id="info-panel">
    <span>Масштаб: <span id="zoom-display">{initial_zoom}</span></span>
    <span>Координаты: <span id="coords-display">—</span></span>
    <a id="download-btn" href="/download/{session_id}">⬇ Скачать тайлы</a>
  </div>

  <script src="{LEAFLET_JS}"></script>
  <script>
    var map = L.map('map', {{
      center: [{center_lat}, {center_lon}],
      zoom: {initial_zoom},
      minZoom: {zoom_min},
      maxZoom: {zoom_max},
    }});

    // Подложка OpenStreetMap
    var osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      opacity: 0.4,
    }}).addTo(map);

    // Слой ортофотоплана
    var photoLayer = L.tileLayer('{tiles_url}', {{
        minZoom: {zoom_min},
        maxZoom: {zoom_max},
        tms: true,
        opacity: 1.0,
        errorTileUrl: '',
    }}).addTo(map);

    var bounds = L.latLngBounds([{south}, {west}], [{north}, {east}]);
    map.setMaxBounds(bounds.pad(0.5));

    L.rectangle(bounds, {{ color: '#e94560', weight: 2, fill: false }}).addTo(map);

    L.control.layers(
      {{ 'OpenStreetMap': osm }},
      {{ 'Ортофотоплан': photoLayer }},
      {{ position: 'topright' }}
    ).addTo(map);

    L.control.scale({{ imperial: false }}).addTo(map);

    map.on('zoomend', function() {{
      document.getElementById('zoom-display').textContent = map.getZoom();
    }});

    map.on('mousemove', function(e) {{
      var lat = e.latlng.lat.toFixed(6);
      var lng = e.latlng.lng.toFixed(6);
      document.getElementById('coords-display').textContent = lat + '°, ' + lng + '°';
    }});

    map.fitBounds(bounds);
  </script>
</body>
</html>"""
