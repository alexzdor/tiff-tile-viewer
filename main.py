"""
main.py — FastAPI-приложение.

Эндпоинты:
  GET   /                    — главная страница (форма загрузки)
  POST  /upload              — загрузка GeoTIFF, обработка, возврат HTML-карты
  GET   /download/{session}  — скачивание ZIP-архива сгенерированных тайлов
  GET   /tiles/{session_id}/{z}/{x}/{y} - возвращает отдельный тайл PNG по его координатам в тайловой пирамиде.

"""

import os
import io
import uuid
import shutil
import zipfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse

import processing
import map_builder

# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
UPLOADS_DIR   = os.path.join(BASE_DIR, "uploads")
TILES_DIR     = os.path.join(BASE_DIR, "tiles")

MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024   # 500 МБ
ALLOWED_EXTENSIONS  = {".tif", ".tiff"}

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(TILES_DIR,   exist_ok=True)

app = FastAPI(
    title="GeoTIFF Viewer",
    description="Веб-модуль подготовки геопривязанных материалов аэрофотосъёмки "
                "к интерактивному отображению на веб-карте.",
    version="1.0.0",
)


# ──────────────────────────────────────────────────────────────────────────────
# Эндпоинты
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/tiles/{session_id}/{z}/{x}/{filename}")
async def serve_tile(session_id: str, z: int, x: int, filename: str):
    tile_path = os.path.join(TILES_DIR, session_id, str(z), str(x), filename)
    if not os.path.isfile(tile_path):
        raise HTTPException(status_code=404, detail="Тайл не найден.")
    return FileResponse(tile_path, media_type="image/png")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Главная страница — форма загрузки файла."""
    path = os.path.join(TEMPLATES_DIR, "index.html")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/upload", response_class=HTMLResponse)
async def upload(file: UploadFile = File(...)):
    """
    Принимает файл GeoTIFF, выполняет обработку и сразу возвращает
    HTML-страницу с готовой интерактивной картой.

    HTTP 400 — недопустимый формат файла.
    HTTP 413 — файл превышает допустимый размер.
    HTTP 422 — ошибка обработки (некорректный GeoTIFF и т.п.).
    HTTP 500 — внутренняя ошибка сервера.
    """
    # Валидация расширения(проверка)
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:       #.tif / .tiff
        raise HTTPException(
            status_code=400,
            detail=(
                f"Недопустимый формат файла «{ext}». "
                "Ожидается файл с расширением .tif или .tiff."
            ),
        )

    # Чтение и проверка размера
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        size_mb  = len(content) / (1024 * 1024)
        limit_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=(
                f"Размер файла ({size_mb:.1f} МБ) превышает "
                f"допустимый лимит ({limit_mb:.0f} МБ)."
            ),
        )

    # Уникальная директория для этого запроса
    session_id = uuid.uuid4().hex
    upload_dir = os.path.join(UPLOADS_DIR, session_id)
    tiles_dir  = os.path.join(TILES_DIR,   session_id)
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(tiles_dir,  exist_ok=True)


    upload_path = os.path.join(upload_dir, filename)
    with open(upload_path, "wb") as f_out:
        f_out.write(content)

    # Обработка: GDAL + gdal2tiles
    try:
        #вызов
        result = processing.process_geotiff(upload_path, tiles_dir)
    except RuntimeError as e:
        _cleanup(upload_dir, tiles_dir)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        _cleanup(upload_dir, tiles_dir)
        raise HTTPException(
            status_code=500,
            detail=f"Внутренняя ошибка сервера при обработке файла: {e}",
        )
    finally:
        # Загруженный GeoTIFF больше не нужен — удаляем сразу
        shutil.rmtree(upload_dir, ignore_errors=True)

    # Генерация и возврат HTML-карты
    # Тайлы доступны по URL /tiles/{session_id}/
    map_html = map_builder.build_map_html(
        tiles_url_prefix=f"/tiles/{session_id}",
        bbox=result["bbox"],
        center=result["center"],
        zoom_min=result["zoom_min"],
        zoom_max=result["zoom_max"],
        session_id=session_id,
    )
    # Возвращение html браузеру
    return HTMLResponse(content=map_html)


@app.get("/download/{session_id}")
async def download_tiles(session_id: str):
    """
    Формирует и возвращает ZIP-архив тайлов указанной сессии.
    """
    tiles_dir = os.path.join(TILES_DIR, session_id)
    if not os.path.isdir(tiles_dir):
        raise HTTPException(
            status_code=404,
            detail="Тайлы не найдены. Возможно, сессия уже истекла.",
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(tiles_dir):
            for fname in files:
                fpath   = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, tiles_dir)
                zf.write(fpath, arcname)

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="tiles_{session_id[:8]}.zip"'
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────────────────────────────────────

def _cleanup(*dirs: str) -> None:
    """Удаляет переданные директории, игнорируя ошибки."""
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, log_level="warning")
