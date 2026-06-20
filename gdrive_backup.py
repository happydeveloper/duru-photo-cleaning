"""
gdrive_backup.py - Google Drive 백업 모듈

사용 전 준비:
  1. https://console.cloud.google.com 에서 프로젝트 생성
  2. Google Drive API 활성화
  3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)
  4. credentials.json 다운로드 -> ~/.config/photo_cleaner/credentials.json
"""

import io
import json
import os
from pathlib import Path
from typing import Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

SCOPES      = ["https://www.googleapis.com/auth/drive.file"]
CONFIG_DIR  = Path.home() / ".config" / "photo_cleaner"
TOKEN_FILE  = CONFIG_DIR / "token.json"
CREDS_FILE  = CONFIG_DIR / "credentials.json"
CHUNK_SIZE  = 4 * 1024 * 1024   # 4 MB

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".raw", ".cr2", ".cr3",
    ".nef", ".arw", ".dng", ".tif", ".tiff", ".gif", ".bmp", ".webp",
    ".avif", ".psd",
}


# ── 인증 ──────────────────────────────────────────────────────────────────────

def credentials_exist() -> bool:
    return CREDS_FILE.exists()


def token_exists() -> bool:
    return TOKEN_FILE.exists()


def authenticate() -> Credentials:
    """OAuth 인증 수행. 브라우저가 열려 구글 로그인을 요청한다."""
    if not CREDS_FILE.exists():
        raise FileNotFoundError(
            f"credentials.json 파일이 없습니다.\n"
            f"Google Cloud Console에서 OAuth 클라이언트 ID를 생성하고\n"
            f"{CREDS_FILE} 에 저장하세요."
        )

    creds = None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return creds


def get_service(creds: Credentials):
    return build("drive", "v3", credentials=creds)


# ── 폴더 관리 ──────────────────────────────────────────────────────────────────

def get_or_create_folder(service, name: str, parent_id: Optional[str] = None) -> str:
    """Drive에서 폴더를 찾거나 새로 만들어 폴더 ID를 반환."""
    query = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and trashed=false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"

    res = service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]

    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def get_backup_folder_id(service, date_str: str) -> str:
    """photo_cleaner_backup/<date> 폴더 ID를 반환 (없으면 생성)."""
    root_id = get_or_create_folder(service, "photo_cleaner_backup")
    return get_or_create_folder(service, date_str, parent_id=root_id)


# ── 업로드 ────────────────────────────────────────────────────────────────────

def upload_file(
    service,
    path: Path,
    folder_id: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> bool:
    """
    파일 하나를 Drive 폴더에 청크 업로드.

    on_progress(uploaded_bytes, total_bytes, filename) 콜백 지원.
    반환값: 성공 여부
    """
    try:
        media = MediaFileUpload(
            str(path),
            resumable=True,
            chunksize=CHUNK_SIZE,
        )
        request = service.files().create(
            body={"name": path.name, "parents": [folder_id]},
            media_body=media,
            fields="id",
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and on_progress:
                on_progress(
                    int(status.resumable_progress),
                    int(status.total_size or path.stat().st_size),
                    path.name,
                )

        if on_progress:
            size = path.stat().st_size
            on_progress(size, size, path.name)

        return True

    except HttpError as e:
        raise RuntimeError(f"Drive 업로드 오류: {e}") from e


# ── 항목별 이미지 파일 추출 ────────────────────────────────────────────────────

def collect_images(result: dict) -> list[Path]:
    """
    scan 결과 항목에서 실제 이미지 파일 목록을 반환.

    file_list 항목은 paths 를 그대로 사용.
    번들(Photos Library 등) 항목은 내부 originals/ 폴더를 탐색.
    캐시/메타데이터 항목은 빈 목록 반환 (백업 대상 아님).
    """
    label = result["label"]
    paths = result["paths"]

    if result["file_list"]:
        return list(paths)

    SKIP_LABELS = {
        "Photos 캐시", "photoanalysisd 캐시", "QuickLook 썸네일",
        "Photos App Support", "iCloud 사진 캐시", "Photos 데이터 디렉토리",
        "mediaanalysisd 캐시",
    }
    if label in SKIP_LABELS:
        return []

    images = []
    for bundle in paths:
        if not bundle.is_dir():
            if bundle.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(bundle)
            continue
        # Photos Library: originals/ 우선 탐색
        originals = bundle / "originals"
        search_root = originals if originals.exists() else bundle
        for p in search_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(p)

    return images


# ── 백업 실행 ─────────────────────────────────────────────────────────────────

class BackupSession:
    """
    하나의 백업 세션.
    on_file(filename, idx, total) — 파일 시작 알림
    on_progress(uploaded, total_bytes, filename) — 청크 진행
    on_done(success_count, fail_count, folder_url) — 완료
    """

    def __init__(
        self,
        creds: Credentials,
        date_str: str,
        on_file: Optional[Callable] = None,
        on_progress: Optional[Callable] = None,
        on_done: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        self._creds      = creds
        self._date_str   = date_str
        self.on_file     = on_file
        self.on_progress = on_progress
        self.on_done     = on_done
        self.on_error    = on_error

    def run(self, results: list[dict]) -> None:
        try:
            service   = get_service(self._creds)
            folder_id = get_backup_folder_id(service, self._date_str)
            folder_url = (
                f"https://drive.google.com/drive/folders/{folder_id}"
            )

            all_files: list[Path] = []
            for r in results:
                all_files.extend(collect_images(r))

            # 중복 제거 (경로 기준)
            seen = set()
            unique: list[Path] = []
            for p in all_files:
                if p not in seen:
                    seen.add(p)
                    unique.append(p)

            total   = len(unique)
            success = 0
            fail    = 0

            for idx, path in enumerate(unique, 1):
                if self.on_file:
                    self.on_file(path.name, idx, total)

                try:
                    upload_file(service, path, folder_id, self.on_progress)
                    success += 1
                except Exception as e:
                    fail += 1
                    if self.on_error:
                        self.on_error(path.name, str(e))

            if self.on_done:
                self.on_done(success, fail, folder_url)

        except Exception as e:
            if self.on_error:
                self.on_error("", str(e))
