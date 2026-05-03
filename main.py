"""
main.py — FastAPI-приложение.

Эндпоинты:
  POST   /upload                — загрузка GeoTIFF и запуск обработки
  GET    /status/{task_id}      — статус задачи
  GET    /result/{task_id}      — HTML-страница карты
  GET    /tiles/{task_id}/{z}/{x}/{y}.png  — отдача тайлов
  GET    /download/{task_id}    — скачивание ZIP-архива с тайлами
  DELETE /task/{task_id}        — удаление задачи и файлов
"""

import os
import io
import zipfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import (
    HTMLResponse, FileResponse, StreamingResponse, JSONResponse
)
from fastapi.staticfiles import StaticFiles

import storage
import processing
import map_builder

# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

app = FastAPI(
    title="GeoTIFF Viewer",
    description="Веб-модуль подготовки геопривязанных материалов аэрофотосъёмки "
                "к интерактивному отображению на веб-карте.",
    version="1.0.0",
)

# Раздача тайлов по URL /tiles/{task_id}/...
tiles_root = storage.TILES_DIR
app.mount("/tiles", StaticFiles(directory=tiles_root), name="tiles")


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────────────────────────────────────

def _get_task_or_404(task_id: str) -> dict:
    task = storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Задача {task_id!r} не найдена.")
    return task


# ──────────────────────────────────────────────────────────────────────────────
# Эндпоинты
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Главная страница — форма загрузки файла."""
    path = os.path.join(TEMPLATES_DIR, "index.html")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """
    Принимает файл GeoTIFF, выполняет валидацию,
    запускает синхронную обработку и возвращает task_id.

    HTTP 400 — недопустимый формат файла.
    HTTP 413 — файл превышает допустимый размер.
    """
    # Валидация расширения
    ok, err = storage.validate_filename(file.filename or "")
    if not ok:
        raise HTTPException(status_code=400, detail=err)

    # Чтение файла в память для проверки размера
    content = await file.read()
    if len(content) > storage.MAX_FILE_SIZE_BYTES:
        size_mb = len(content) / (1024 * 1024)
        limit_mb = storage.MAX_FILE_SIZE_BYTES / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=(
                f"Размер файла ({size_mb:.1f} МБ) превышает допустимый лимит "
                f"({limit_mb:.0f} МБ)."
            ),
        )

    # Создаём задачу и сохраняем файл
    task_id = storage.create_task()
    upload_dir = storage.get_upload_dir(task_id)
    upload_path = os.path.join(upload_dir, file.filename)
    with open(upload_path, "wb") as f_out:
        f_out.write(content)

    storage.update_task(task_id, upload_path=upload_path, status="processing", progress=10)

    # Синхронная обработка
    tiles_dir = storage.get_tiles_dir(task_id)
    try:
        storage.update_task(task_id, progress=20)

        result = processing.process_geotiff(upload_path, tiles_dir)

        storage.update_task(task_id, progress=85)

        # Генерация HTML-карты
        map_html_path = map_builder.build_map_html(
            tiles_dir=tiles_dir,
            bbox=result["bbox"],
            center=result["center"],
            zoom_min=result["zoom_min"],
            zoom_max=result["zoom_max"],
            task_id=task_id,
        )

        storage.update_task(
            task_id,
            status="done",
            progress=100,
            tiles_dir=tiles_dir,
            map_html_path=map_html_path,
            bbox=result["bbox"],
            zoom_min=result["zoom_min"],
            zoom_max=result["zoom_max"],
        )

    except RuntimeError as e:
        storage.update_task(task_id, status="error", error_message=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        msg = f"Внутренняя ошибка сервера при обработке файла: {e}"
        storage.update_task(task_id, status="error", error_message=msg)
        raise HTTPException(status_code=500, detail=msg)

    return JSONResponse({
        "task_id": task_id,
        "status": "done",
        "bbox": result["bbox"],
        "zoom_min": result["zoom_min"],
        "zoom_max": result["zoom_max"],
        "center": result["center"],
    })


@app.get("/status/{task_id}")
async def get_status(task_id: str):
    """
    Возвращает статус задачи.

    Статусы: pending | processing | done | error
    """
    task = _get_task_or_404(task_id)
    return JSONResponse({
        "task_id": task_id,
        "status": task["status"],
        "progress": task["progress"],
        "error_message": task.get("error_message"),
        "bbox": task.get("bbox"),
        "zoom_min": task.get("zoom_min"),
        "zoom_max": task.get("zoom_max"),
    })


@app.get("/result/{task_id}", response_class=HTMLResponse)
async def get_result(task_id: str):
    """
    Возвращает HTML-страницу интерактивной карты.
    Доступно только для задач со статусом 'done'.
    """
    task = _get_task_or_404(task_id)

    if task["status"] == "error":
        raise HTTPException(
            status_code=422,
            detail=f"Задача завершилась с ошибкой: {task['error_message']}",
        )
    if task["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail="Задача ещё не завершена. Дождитесь статуса 'done'.",
        )

    map_html_path = task.get("map_html_path")
    if not map_html_path or not os.path.exists(map_html_path):
        raise HTTPException(status_code=500, detail="HTML-файл карты не найден.")

    with open(map_html_path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/download/{task_id}")
async def download_tiles(task_id: str):
    """
    Формирует и возвращает ZIP-архив со всеми тайлами задачи.
    """
    task = _get_task_or_404(task_id)

    if task["status"] != "done":
        raise HTTPException(
            status_code=409,
            detail="Скачивание доступно только для завершённых задач.",
        )

    tiles_dir = task.get("tiles_dir")
    if not tiles_dir or not os.path.isdir(tiles_dir):
        raise HTTPException(status_code=500, detail="Каталог тайлов не найден.")

    # Формируем ZIP в памяти
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(tiles_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, tiles_dir)
                zf.write(fpath, arcname)

    zip_buffer.seek(0)
    filename = f"tiles_{task_id[:8]}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    """
    Удаляет задачу и все связанные с ней файлы.
    """
    _get_task_or_404(task_id)
    storage.delete_task_files(task_id)
    return JSONResponse({"deleted": True, "task_id": task_id})


# ──────────────────────────────────────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # Очищаем устаревшие задачи при запуске
    storage.cleanup_old_tasks()

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
