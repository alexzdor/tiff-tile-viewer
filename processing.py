"""
processing.py — вычислительный модуль.

Выполняет:
1. Открытие и валидацию файла GeoTIFF через GDAL.
2. Извлечение метаданных (CRS, bbox, разрешение, число каналов).
3. Репроецирование в Web Mercator (EPSG:3857) при необходимости.
4. Генерацию тайловой пирамиды XYZ с помощью gdal2tiles.
5. Определение диапазона уровней масштабирования.
"""

import os
import subprocess
import sys
import math
from osgeo import gdal, osr

gdal.UseExceptions()


# ──────────────────────────────────────────────────────────────────────────────
# Константы
# ──────────────────────────────────────────────────────────────────────────────

# Целевая система координат для веб-карт
TARGET_EPSG = 4326          # gdal2tiles требует WGS-84 (EPSG:4326) на входе
REPROJECTED_SUFFIX = "_wgs84.tif"

# Ограничения на тайловые уровни
ZOOM_MIN_ABSOLUTE = 0
ZOOM_MAX_ABSOLUTE = 20


# ──────────────────────────────────────────────────────────────────────────────
# Публичный интерфейс
# ──────────────────────────────────────────────────────────────────────────────

def process_geotiff(input_path: str, tiles_dir: str) -> dict:
    """
    Выполняет полный пайплайн обработки GeoTIFF.

    Параметры:
        input_path  — путь к загруженному файлу GeoTIFF.
        tiles_dir   — директория, куда будут сохранены тайлы.

    Возвращает словарь:
        {
            "bbox":      [west, south, east, north],  # WGS-84 градусы
            "zoom_min":  int,
            "zoom_max":  int,
            "center":    [lon, lat],
        }

    Бросает RuntimeError с описанием ошибки при любой проблеме.
    """
    # Шаг 1: Открытие и валидация
    ds = _open_and_validate(input_path)

    # Шаг 2: Извлечение метаданных
    meta = _extract_metadata(ds)
    ds = None  # закрываем датасет

    # Шаг 3: Репроецирование в WGS-84 (если необходимо)
    working_path = _reproject_if_needed(input_path, meta)

    # Шаг 4: Определение диапазона уровней масштабирования
    zoom_min, zoom_max = _compute_zoom_range(meta)

    # Шаг 5: Генерация тайлов
    _generate_tiles(working_path, tiles_dir, zoom_min, zoom_max)

    # Удаляем временный репроецированный файл, если он был создан
    if working_path != input_path and os.path.exists(working_path):
        os.remove(working_path)

    bbox = meta["bbox_wgs84"]
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2

    return {
        "bbox": bbox,
        "zoom_min": zoom_min,
        "zoom_max": zoom_max,
        "center": [center_lon, center_lat],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Внутренние функции
# ──────────────────────────────────────────────────────────────────────────────

def _open_and_validate(path: str):
    """
    Открывает файл через GDAL и проверяет наличие геопривязки.
    Бросает RuntimeError при проблемах.
    """
    if not os.path.exists(path):
        raise RuntimeError(f"Файл не найден: {path}")

    try:
        ds = gdal.Open(path, gdal.GA_ReadOnly)
    except Exception as e:
        raise RuntimeError(f"Не удалось открыть файл как растр: {e}")

    if ds is None:
        raise RuntimeError(
            "Файл не является корректным растровым изображением. "
            "Убедитесь, что загружен файл формата GeoTIFF (.tif / .tiff)."
        )

    # Проверка наличия геопривязки
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()

    # Трансформация по умолчанию (без привязки): (0,1,0,0,0,1)
    default_gt = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    has_gt = gt != default_gt
    has_proj = bool(proj and proj.strip())

    if not has_gt and not has_proj:
        raise RuntimeError(
            "Файл GeoTIFF не содержит геопривязки. "
            "Загрузите файл с корректной пространственной привязкой (система координат и трансформация)."
        )

    if not has_proj:
        raise RuntimeError(
            "Файл GeoTIFF содержит матрицу трансформации, но не содержит описания системы координат. "
            "Добавьте информацию о проекции (например, EPSG:4326 или EPSG:3857) в файл GeoTIFF."
        )

    # Проверка числа каналов
    n_bands = ds.RasterCount
    if n_bands == 0:
        raise RuntimeError("Файл GeoTIFF не содержит растровых каналов.")

    if n_bands > 4:
        raise RuntimeError(
            f"Файл содержит {n_bands} каналов. "
            "Поддерживаются файлы с 1 (оттенки серого), 3 (RGB) или 4 (RGBA) каналами."
        )

    return ds


def _extract_metadata(ds) -> dict:
    """
    Извлекает метаданные из открытого датасета GDAL.
    Возвращает словарь с ключами: crs_wkt, bbox_wgs84, pixel_size_deg,
    width, height, n_bands, epsg.
    """
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    width = ds.RasterXSize
    height = ds.RasterYSize

    # Координаты угловых точек в исходной CRS
    x_min = gt[0]
    y_max = gt[3]
    x_max = gt[0] + gt[1] * width
    y_min = gt[3] + gt[5] * height  # gt[5] отрицательный

    # Определяем исходную CRS
    src_srs = osr.SpatialReference()
    src_srs.ImportFromWkt(proj)
    src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    # Целевая CRS — WGS-84
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    # Трансформация в WGS-84
    transform = osr.CoordinateTransformation(src_srs, wgs84)

    def transform_point(x, y):
        lon, lat, _ = transform.TransformPoint(x, y)
        return lon, lat

    try:
        lon_min, lat_min = transform_point(x_min, y_min)
        lon_max, lat_max = transform_point(x_max, y_max)
    except Exception as e:
        raise RuntimeError(
            f"Не удалось перевести координаты в WGS-84. "
            f"Возможно, система координат файла не поддерживается: {e}"
        )

    # Нормализуем: west < east, south < north
    west = min(lon_min, lon_max)
    east = max(lon_min, lon_max)
    south = min(lat_min, lat_max)
    north = max(lat_min, lat_max)

    # Размер пикселя в градусах (приблизительно для определения zoom)
    # Пересчитываем через разницу координат и размер в пикселях
    pixel_size_deg = (east - west) / width

    # Пытаемся получить EPSG
    epsg = None
    try:
        src_srs.AutoIdentifyEPSG()
        epsg = src_srs.GetAuthorityCode(None)
    except Exception:
        pass

    return {
        "crs_wkt": proj,
        "bbox_wgs84": [west, south, east, north],
        "pixel_size_deg": pixel_size_deg,
        "width": width,
        "height": height,
        "n_bands": ds.RasterCount,
        "epsg": epsg,
        "src_srs": src_srs,
        "native_bbox": [x_min, y_min, x_max, y_max],
    }


def _reproject_if_needed(input_path: str, meta: dict) -> str:
    """
    Если файл не в WGS-84 (EPSG:4326), перепроецирует его.
    Возвращает путь к рабочему файлу (оригинальному или перепроецированному).
    """
    epsg = meta.get("epsg")
    src_srs: osr.SpatialReference = meta["src_srs"]

    # Проверяем, является ли уже WGS-84
    wgs84_check = osr.SpatialReference()
    wgs84_check.ImportFromEPSG(4326)
    wgs84_check.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    if src_srs.IsSame(wgs84_check):
        return input_path  # уже WGS-84, репроецирование не нужно

    # Формируем путь для выходного файла
    base, _ = os.path.splitext(input_path)
    output_path = base + REPROJECTED_SUFFIX

    try:
        warp_options = gdal.WarpOptions(
            dstSRS="EPSG:4326",
            resampleAlg=gdal.GRA_Bilinear,
            format="GTiff",
        )
        result = gdal.Warp(output_path, input_path, options=warp_options)
        if result is None:
            raise RuntimeError("gdal.Warp вернул None.")
        result = None  # закрываем
    except Exception as e:
        raise RuntimeError(
            f"Не удалось перепроецировать файл в WGS-84 (EPSG:4326): {e}"
        )

    return output_path


def _compute_zoom_range(meta: dict) -> tuple[int, int]:
    """
    Определяет диапазон уровней масштабирования на основе
    пространственного разрешения файла.

    Формула: zoom = log2(360 / (pixel_size_deg * 256))
    Максимальный zoom — тот, при котором один пиксель тайла
    соответствует примерно одному пикселю изображения.
    """
    pixel_size = meta["pixel_size_deg"]
    if pixel_size <= 0:
        return 0, 10

    zoom_max_float = math.log2(360.0 / (pixel_size * 256))
    zoom_max = min(int(zoom_max_float), ZOOM_MAX_ABSOLUTE)
    zoom_max = max(zoom_max, 1)

    # Минимальный zoom определяем по охвату bbox
    west, south, east, north = meta["bbox_wgs84"]
    lon_span = east - west
    lat_span = north - south
    max_span = max(lon_span, lat_span)

    if max_span > 0:
        zoom_min_float = math.log2(360.0 / (max_span))
        zoom_min = max(int(zoom_min_float) - 1, ZOOM_MIN_ABSOLUTE)
    else:
        zoom_min = 0

    zoom_min = min(zoom_min, zoom_max)

    return zoom_min, zoom_max


def _generate_tiles(input_path: str, tiles_dir: str,
                    zoom_min: int, zoom_max: int) -> None:
    """
    Запускает gdal2tiles для генерации тайловой пирамиды XYZ.
    Команда определяется функцией _find_gdal2tiles() кроссплатформенно.
    """
    cmd = _find_gdal2tiles() + [
        "--profile=mercator",
        f"--zoom={zoom_min}-{zoom_max}",
        "--resampling=average",
        "--tilesize=256",
        "--webviewer=none",
        "--processes=1",
        input_path,
        tiles_dir,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Время обработки превысило допустимый лимит (10 минут). "
            "Попробуйте загрузить файл меньшего размера или с меньшим разрешением."
        )

    if result.returncode != 0:
        error_detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"Генерация тайлов завершилась с ошибкой:\n{error_detail}"
        )


def _find_gdal2tiles() -> list:
    """
    Возвращает команду запуска gdal2tiles в виде списка аргументов.
    Работает на Windows, Linux и macOS.
    """
    import shutil
    import glob

    # Вариант 1: запуск как Python-модуль (pip install gdal2tiles)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "gdal2tiles", "--version"],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "gdal2tiles"]
    except Exception:
        pass

    # Вариант 2: gdal2tiles.py рядом с интерпретатором (Scripts/ на Windows)
    python_dir = os.path.dirname(sys.executable)
    scripts_dir = os.path.join(python_dir, "Scripts")
    candidates = [
        os.path.join(scripts_dir, "gdal2tiles.py"),
        os.path.join(python_dir,  "gdal2tiles.py"),
        "/usr/bin/gdal2tiles.py",
        "/usr/local/bin/gdal2tiles.py",
    ]
    for path in candidates:
        if os.path.exists(path):
            return [sys.executable, path]

    # Вариант 3: рекурсивный поиск по дереву Python
    for pattern in [os.path.join(python_dir, "**", "gdal2tiles.py")]:
        found = glob.glob(pattern, recursive=True)
        if found:
            return [sys.executable, found[0]]

    # Вариант 4: gdal2tiles как исполняемый файл в PATH
    exe = shutil.which("gdal2tiles") or shutil.which("gdal2tiles.py")
    if exe:
        return [exe]

    raise RuntimeError(
        "Не удалось найти gdal2tiles. "
        "Выполните в командной строке: pip install gdal2tiles"
    )
