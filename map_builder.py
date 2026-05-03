"""
map_builder.py — формирование HTML-страницы интерактивной карты.

Принимает параметры задачи (bbox, zoom, центр) и путь к тайлам,
возвращает готовый HTML-файл карты.
"""

import os

# URL CDN для Leaflet.js
LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"


def build_map_html(
    tiles_dir: str,
    bbox: list,          # [west, south, east, north]
    center: list,        # [lon, lat]
    zoom_min: int,
    zoom_max: int,
    task_id: str,
) -> str:
    """
    Генерирует HTML-файл интерактивной карты и сохраняет его в tiles_dir.

    Возвращает путь к созданному файлу.
    """
    west, south, east, north = bbox
    center_lat, center_lon = center[1], center[0]

    # Начальный zoom — немного меньше максимального, чтобы карта была читаема
    initial_zoom = max(zoom_min, zoom_max - 2)

    # URL-шаблон тайлов (относительный путь для локального открытия)
    tiles_url = "{z}/{x}/{y}.png"

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Интерактивная карта — задача {task_id[:8]}</title>
  <link rel="stylesheet" href="{LEAFLET_CSS}" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

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

    header h1 {{
      font-size: 1rem;
      font-weight: 600;
      color: #e94560;
    }}

    #meta-info {{
      font-size: 0.75rem;
      color: #aaa;
    }}

    #map {{
      flex: 1;
    }}

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

    #coords-display {{
      font-family: monospace;
    }}
  </style>
</head>
<body>
  <header>
    <h1>📍 Интерактивная карта аэрофотосъёмки</h1>
    <div id="meta-info">
      Охват: {west:.5f}°W, {south:.5f}°S → {east:.5f}°E, {north:.5f}°N
      &nbsp;|&nbsp; Уровни масштабирования: {zoom_min}–{zoom_max}
    </div>
  </header>

  <div id="map"></div>

  <div id="info-panel">
    <span>Масштаб: <span id="zoom-display">{initial_zoom}</span></span>
    <span>Координаты курсора: <span id="coords-display">—</span></span>
  </div>

  <script src="{LEAFLET_JS}"></script>
  <script>
    // Инициализация карты
    var map = L.map('map', {{
      center: [{center_lat}, {center_lon}],
      zoom: {initial_zoom},
      minZoom: {zoom_min},
      maxZoom: {zoom_max},
    }});

    // Подложка OpenStreetMap (для контекста)
    var osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      opacity: 0.4,
    }}).addTo(map);

    // Слой с нашими тайлами
    var photoLayer = L.tileLayer('{tiles_url}', {{
      minZoom: {zoom_min},
      maxZoom: {zoom_max},
      tms: false,
      opacity: 1.0,
      errorTileUrl: '',
    }}).addTo(map);

    // Ограничение области навигации по bbox ортофотоплана
    var bounds = L.latLngBounds(
      [{south}, {west}],
      [{north}, {east}]
    );
    map.setMaxBounds(bounds.pad(0.5));

    // Рамка охвата ортофотоплана
    L.rectangle(bounds, {{
      color: '#e94560',
      weight: 2,
      fill: false,
    }}).addTo(map);

    // Управление слоями
    L.control.layers(
      {{ 'OpenStreetMap': osm }},
      {{ 'Ортофотоплан': photoLayer }},
      {{ position: 'topright' }}
    ).addTo(map);

    // Шкала масштаба
    L.control.scale({{ imperial: false }}).addTo(map);

    // Обновление информационной панели
    map.on('zoomend', function() {{
      document.getElementById('zoom-display').textContent = map.getZoom();
    }});

    map.on('mousemove', function(e) {{
      var lat = e.latlng.lat.toFixed(6);
      var lng = e.latlng.lng.toFixed(6);
      document.getElementById('coords-display').textContent = lat + '°, ' + lng + '°';
    }});

    // Автоматически выставляем вид по охвату
    map.fitBounds(bounds);
  </script>
</body>
</html>"""

    output_path = os.path.join(tiles_dir, "map.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path
