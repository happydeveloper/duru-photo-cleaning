"""
gdrive_backup.py — Google Drive 백업 모듈

photo_tui.py 에서 import 해서 사용합니다.
단독으로 실행하지 않습니다.

준비 단계 (최초 1회):
    1. https://console.cloud.google.com 에서 프로젝트 생성
    2. Google Drive API 활성화
    3. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)
    4. credentials.json 다운로드
       -> ~/.config/photo_cleaner/credentials.json 에 저장

인증 흐름:
    authenticate() 호출
      -> token.json 있으면 로드해서 바로 사용
      -> 만료됐으면 자동 갱신
      -> 없으면 브라우저에서 구글 로그인 후 token.json 생성

업로드 방식:
    파일을 4MB 청크 단위로 나눠 Drive 에 올립니다 (Resumable Upload).
    네트워크가 끊겨도 이어서 업로드할 수 있는 구글 공식 방식입니다.
"""

import os
from pathlib import Path
from typing import Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


# ---------------------------------------------------------------------------
# 설정 상수
# ---------------------------------------------------------------------------

# drive.file 스코프: 이 앱이 업로드한 파일만 접근 가능합니다.
# 사용자의 기존 Drive 파일을 읽거나 수정할 수 없어 안전합니다.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

CONFIG_DIR = Path.home() / ".config" / "photo_cleaner"
TOKEN_FILE = CONFIG_DIR / "token.json"       # 로그인 후 자동 저장
CREDS_FILE = CONFIG_DIR / "credentials.json" # 사용자가 직접 저장해야 함

# 청크 크기: 4MB. 너무 작으면 API 호출이 잦고, 너무 크면 재시도 시 손해가 큽니다.
CHUNK_SIZE = 4 * 1024 * 1024

# 이 목록에 있는 확장자만 Drive 에 업로드합니다.
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".raw", ".cr2", ".cr3",
    ".nef", ".arw", ".dng", ".tif", ".tiff", ".gif", ".bmp", ".webp",
    ".avif", ".psd",
}


# ---------------------------------------------------------------------------
# 인증
# ---------------------------------------------------------------------------

def credentials_exist() -> bool:
    """credentials.json 파일이 있는지 확인합니다."""
    return CREDS_FILE.exists()


def token_exists() -> bool:
    """token.json 파일이 있는지 확인합니다 (이미 로그인한 적 있는지 여부)."""
    return TOKEN_FILE.exists()


def authenticate() -> Credentials:
    """Google OAuth 인증을 수행하고 Credentials 객체를 반환합니다.

    처음 실행 시: 브라우저가 열리고 구글 계정 로그인을 요청합니다.
    이후 실행 시: token.json 에서 자동으로 로드합니다.
    토큰이 만료됐으면: refresh_token 으로 자동 갱신합니다.

    Raises:
        FileNotFoundError: credentials.json 이 없을 때
    """
    if not CREDS_FILE.exists():
        raise FileNotFoundError(
            f"credentials.json 파일이 없습니다.\n"
            f"Google Cloud Console에서 OAuth 클라이언트 ID를 생성하고\n"
            f"{CREDS_FILE} 에 저장하세요."
        )

    creds = None
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 이전에 저장된 토큰이 있으면 로드합니다.
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # 토큰이 만료됐지만 refresh_token 이 있으면 브라우저 없이 갱신합니다.
            creds.refresh(Request())
        else:
            # 처음 로그인하거나 refresh_token 도 없으면 브라우저를 엽니다.
            # port=0: 사용 가능한 포트를 자동으로 선택합니다.
            flow  = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        # 다음 실행을 위해 토큰을 저장합니다.
        TOKEN_FILE.write_text(creds.to_json())

    return creds


def get_service(creds: Credentials):
    """Drive API v3 서비스 객체를 반환합니다.

    이 객체를 통해 파일 업로드, 폴더 생성 등 모든 Drive 작업을 수행합니다.
    """
    return build("drive", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# 폴더 관리
# ---------------------------------------------------------------------------

def get_or_create_folder(service, name: str, parent_id: Optional[str] = None) -> str:
    """Drive 에서 폴더를 찾거나 없으면 새로 만들고 폴더 ID 를 반환합니다.

    같은 이름의 폴더가 이미 있으면 새로 만들지 않고 기존 ID 를 반환합니다.
    이렇게 하면 여러 번 실행해도 폴더가 중복 생성되지 않습니다.
    """
    # Drive API 의 쿼리 언어로 조건을 지정합니다.
    query = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and trashed=false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"

    res   = service.files().list(q=query, fields="files(id, name)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]  # 이미 있으면 첫 번째 결과를 사용

    # 폴더 생성
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]

    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def get_backup_folder_id(service, date_str: str) -> str:
    """Drive 에 photo_cleaner_backup/<날짜> 폴더를 만들고 ID 를 반환합니다.

    예: photo_cleaner_backup/2026-06-20/
    날짜별로 폴더가 나뉘어 실행할 때마다 구분됩니다.
    """
    root_id = get_or_create_folder(service, "photo_cleaner_backup")
    return get_or_create_folder(service, date_str, parent_id=root_id)


# ---------------------------------------------------------------------------
# 파일 업로드
# ---------------------------------------------------------------------------

def upload_file(
    service,
    path: Path,
    folder_id: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> bool:
    """파일 하나를 Drive 폴더에 청크 단위로 업로드합니다.

    Args:
        service:     Drive API 서비스 객체
        path:        업로드할 로컬 파일 경로
        folder_id:   업로드 대상 Drive 폴더 ID
        on_progress: 청크마다 호출되는 콜백
                     on_progress(업로드된_바이트, 전체_바이트, 파일명)

    Returns:
        True (성공). 실패 시 RuntimeError 를 발생시킵니다.
    """
    try:
        # resumable=True: 업로드를 청크 단위로 나눠 보냅니다.
        # 네트워크 오류 시 처음부터 다시 보내지 않고 이어서 재시도할 수 있습니다.
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

        # next_chunk() 를 반복 호출해 청크를 하나씩 보냅니다.
        # response 가 None 이 아니면 업로드 완료입니다.
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and on_progress:
                on_progress(
                    int(status.resumable_progress),
                    int(status.total_size or path.stat().st_size),
                    path.name,
                )

        # 마지막 청크 후 status 가 없을 수 있어 완료 시 한 번 더 콜백을 호출합니다.
        if on_progress:
            size = path.stat().st_size
            on_progress(size, size, path.name)

        return True

    except HttpError as e:
        raise RuntimeError(f"Drive 업로드 오류: {e}") from e


# ---------------------------------------------------------------------------
# 백업 대상 이미지 파일 수집
# ---------------------------------------------------------------------------

def collect_images(result: dict) -> list[Path]:
    """스캔 결과 항목에서 실제로 백업할 이미지 파일 목록을 추출합니다.

    항목 유형별 처리 방식:
    - file_list=True  : paths 가 이미 이미지 파일 목록이므로 그대로 반환합니다.
    - Photos Library  : 번들 내부의 originals/ 폴더에서 원본만 추출합니다.
    - 캐시/메타데이터 : 앱이 자동 재생성하는 파일이므로 백업하지 않습니다.

    Photos Library 내부 구조:
        Photos Library.photoslibrary/
            originals/           <- 원본 사진이 여기 있습니다
                0/ 1/ ... F/     <- 해시 기반 서브폴더
                    *.HEIC *.JPG
            resources/           <- 썸네일 등 (백업 불필요)
    """
    label = result["label"]
    paths = result["paths"]

    # 개별 파일 목록은 그대로 사용합니다.
    if result["file_list"]:
        return list(paths)

    # 캐시/메타데이터 항목은 백업하지 않습니다.
    # 이런 파일들은 앱을 다시 실행하면 자동으로 재생성됩니다.
    SKIP_LABELS = {
        "Photos 캐시", "photoanalysisd 캐시", "QuickLook 썸네일",
        "Photos App Support", "iCloud 사진 캐시", "Photos 데이터 디렉토리",
        "mediaanalysisd 캐시",
    }
    if label in SKIP_LABELS:
        return []

    # Photos Library / iPhoto Library: 번들 내부에서 이미지 파일을 찾습니다.
    images = []
    for bundle in paths:
        if not bundle.is_dir():
            if bundle.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(bundle)
            continue

        # originals/ 폴더가 있으면 그 안만 탐색합니다 (썸네일 폴더 제외).
        originals   = bundle / "originals"
        search_root = originals if originals.exists() else bundle

        for p in search_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                images.append(p)

    return images


# ---------------------------------------------------------------------------
# 백업 세션
# ---------------------------------------------------------------------------

class BackupSession:
    """하나의 백업 작업 단위를 관리합니다.

    콜백 함수를 통해 진행 상황을 외부(photo_tui.py)에 알립니다.
    UI 코드와 백업 로직을 분리하기 위한 설계입니다.

    콜백 시그니처:
        on_file(파일명: str, 현재번호: int, 전체수: int)
            -> 파일 업로드를 시작할 때 호출됩니다.
        on_progress(업로드된_바이트: int, 전체_바이트: int, 파일명: str)
            -> 청크 하나를 보낼 때마다 호출됩니다. 진행 바 업데이트에 사용합니다.
        on_done(성공수: int, 실패수: int, Drive_폴더_URL: str)
            -> 모든 파일 처리가 끝났을 때 호출됩니다.
        on_error(파일명: str, 오류메시지: str)
            -> 파일 하나가 실패했을 때 호출됩니다.
            파일명이 빈 문자열이면 세션 전체 오류입니다.
    """

    def __init__(
        self,
        creds: Credentials,
        date_str: str,
        on_file:     Optional[Callable] = None,
        on_progress: Optional[Callable] = None,
        on_done:     Optional[Callable] = None,
        on_error:    Optional[Callable] = None,
    ):
        self._creds      = creds
        self._date_str   = date_str  # Drive 폴더명에 사용 (예: "2026-06-20")
        self.on_file     = on_file
        self.on_progress = on_progress
        self.on_done     = on_done
        self.on_error    = on_error

    def run(self, results: list[dict]) -> None:
        """스캔 결과 목록을 받아 Drive 에 업로드합니다.

        내부에서 같은 파일이 여러 항목에 중복으로 들어 있을 경우를 제거합니다.
        """
        try:
            service    = get_service(self._creds)
            folder_id  = get_backup_folder_id(service, self._date_str)
            folder_url = f"https://drive.google.com/drive/folders/{folder_id}"

            # 모든 항목에서 이미지 파일을 수집합니다.
            all_files: list[Path] = []
            for r in results:
                all_files.extend(collect_images(r))

            # 같은 경로가 두 번 들어오지 않도록 중복을 제거합니다.
            # 순서를 유지하기 위해 dict 대신 seen 집합을 사용합니다.
            seen: set[Path] = set()
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
            # 파일명을 빈 문자열로 넘겨 "세션 전체 오류"임을 알립니다.
            if self.on_error:
                self.on_error("", str(e))
