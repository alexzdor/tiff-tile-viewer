"""
storage.py — управление файлами и состоянием задач.
Хранит задачи в памяти (словарь). Управляет директориями uploads/ и tiles/.
"""

import uuid
import os
import shutil
import time
from datetime import datetime
from typing import Optional

# Корневые директории
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
TILES_DIR = os.path.join(BASE_DIR, "tiles")

# Максимальный размер входного файла: 500 МБ
MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024

# Допустимые расширения
ALLOWED_EXTENSIONS = {".tif", ".tiff"}

# Хранилище задач в памяти: task_id -> dict
_tasks: dict[str, dict] = {}


def create_task() -> str:
    """Создаёт новую задачу и возвращает её идентификатор."""
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",       # pending | processing | done | error
        "progress": 0,
        "error_message": None,
        "created_at": datetime.utcnow().isoformat(),
        "upload_path": None,
        "tiles_dir": None,
        "map_html_path": None,
        "bbox": None,              # [west, south, east, north] в WGS-84
        "zoom_min": None,
        "zoom_max": None,
    }
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    """Возвращает словарь задачи или None, если не найдена."""
    return _tasks.get(task_id)


def update_task(task_id: str, **kwargs) -> None:
    """Обновляет поля задачи."""
    if task_id in _tasks:
        _tasks[task_id].update(kwargs)


def set_upload_path(task_id: str, path: str) -> None:
    _tasks[task_id]["upload_path"] = path


def set_tiles_dir(task_id: str, path: str) -> None:
    _tasks[task_id]["tiles_dir"] = path


def get_upload_dir(task_id: str) -> str:
    """Возвращает директорию для загруженного файла задачи."""
    d = os.path.join(UPLOADS_DIR, task_id)
    os.makedirs(d, exist_ok=True)
    return d


def get_tiles_dir(task_id: str) -> str:
    """Возвращает директорию для тайлов задачи."""
    d = os.path.join(TILES_DIR, task_id)
    os.makedirs(d, exist_ok=True)
    return d


def delete_task_files(task_id: str) -> None:
    """Удаляет все файлы, связанные с задачей."""
    upload_dir = os.path.join(UPLOADS_DIR, task_id)
    tiles_dir = os.path.join(TILES_DIR, task_id)
    if os.path.exists(upload_dir):
        shutil.rmtree(upload_dir)
    if os.path.exists(tiles_dir):
        shutil.rmtree(tiles_dir)
    _tasks.pop(task_id, None)


def cleanup_old_tasks(max_age_seconds: int = 86400) -> None:
    """
    Удаляет задачи и файлы старше max_age_seconds (по умолчанию 24 часа).
    Вызывается при старте сервера.
    """
    now = time.time()
    to_delete = []
    for task_id, task in _tasks.items():
        created = datetime.fromisoformat(task["created_at"]).timestamp()
        if now - created > max_age_seconds:
            to_delete.append(task_id)
    for task_id in to_delete:
        delete_task_files(task_id)


def validate_filename(filename: str) -> tuple[bool, str]:
    """
    Проверяет расширение файла.
    Возвращает (ok, error_message).
    """
    if not filename:
        return False, "Имя файла не указано."
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, (
            f"Недопустимый формат файла «{ext}». "
            f"Ожидается файл с расширением .tif или .tiff."
        )
    return True, ""
