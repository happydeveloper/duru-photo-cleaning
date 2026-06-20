#!/usr/bin/env python3
"""
photo_tui.py — macOS 사진 파일 정리 TUI

패널 기반 대화형 화면(TUI)으로 사진 파일을 정리합니다.
Google Drive 백업 후 삭제하는 기능을 포함합니다.

실행:
    python3 photo_tui.py

화면 구성:
    - 스캔 결과 테이블 (번호, 항목명, 크기, 경로)
    - 로그 패널 (실시간 진행 메시지)
    - 상태 바 (현재 작업 상태 한 줄)
    - 명령 입력창 (하단)

주요 명령:
    scan              사진 파일 스캔 (시작 시 자동 실행)
    delete <번호|all> 휴지통으로 이동 (Drive 인증 시 백업 여부 확인)
    rm <번호|all>     완전 삭제 (Drive 인증 시 백업 여부 확인)
    auth              Google Drive 인증
    backup <번호|all> Drive 에 백업만
    backup-delete     백업 후 휴지통 이동
    backup-rm         백업 후 완전 삭제
    help / F1         명령어 목록 표시
    q / Ctrl+C        종료

의존성:
    pip3 install textual google-api-python-client google-auth-oauthlib google-auth-httplib2
"""

import os
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path

# Textual: 파이썬용 TUI 프레임워크. 위젯 기반으로 패널 화면을 만듭니다.
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Label, RichLog, Static
from textual.reactive import reactive  # 값이 바뀌면 화면을 자동으로 다시 그립니다.

import gdrive_backup as gd  # Google Drive 백업 모듈


# ---------------------------------------------------------------------------
# 스캔 로직
# ---------------------------------------------------------------------------
# photo_cleaner.py 와 동일한 로직입니다.
# TUI 에서 직접 임포트하지 않고 여기서 다시 정의한 이유는
# photo_cleaner.py 가 시그널 핸들러와 커서 제어 코드를 포함하고 있어
# TUI 와 충돌할 수 있기 때문입니다.

HOME = Path.home()

SCAN_TARGETS = [
    ("Photos Library",        "macOS 사진 앱 라이브러리 (원본+썸네일)",
     HOME / "Pictures",                    "*.photoslibrary"),
    ("iPhoto Library",        "구형 iPhoto 라이브러리",
     HOME / "Pictures",                    "*.iPhoto"),
    ("사진 원본 파일",         "~/Pictures 안 이미지 파일",
     HOME / "Pictures",                    None),
    ("Photos 캐시",            "Photos 앱 분석/썸네일 캐시",
     HOME / "Library/Caches",              "com.apple.Photos*"),
    ("photoanalysisd 캐시",    "얼굴/장면 인식 캐시",
     HOME / "Library/Caches",              "com.apple.photoanalysisd*"),
    ("QuickLook 썸네일",       "파일 미리보기 캐시",
     HOME / "Library/Caches",              "com.apple.QuickLook*"),
    ("Photos App Support",    "Photos 앱 지원 데이터",
     HOME / "Library/Application Support", "com.apple.Photos*"),
    ("iCloud 사진 캐시",        "iCloud Photo Library 임시 데이터",
     HOME / "Library/Application Support", "iCloud Photos*"),
    ("Photos 데이터 디렉토리",  "~/Library/Photos 메타데이터",
     HOME / "Library",                     "Photos"),
    ("mediaanalysisd 캐시",    "미디어 분석 캐시",
     HOME / "Library/Caches",              "com.apple.mediaanalysisd*"),
]

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".raw", ".cr2", ".cr3",
    ".nef", ".arw", ".dng", ".tif", ".tiff", ".gif", ".bmp", ".webp",
    ".avif", ".psd", ".ai", ".svg",
}


def human(size: int) -> str:
    """바이트 수를 "13.5 GB" 처럼 읽기 쉬운 문자열로 변환합니다."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def get_size_bytes(path: Path) -> int:
    """디렉토리나 번들의 전체 크기를 바이트 단위로 반환합니다.

    Python 의 os.path.getsize() 는 디렉토리 자체만 측정해 내용물을 포함하지 않습니다.
    macOS 내장 du 명령으로 측정해야 실제 사용량이 나옵니다.
    `du -sk` 는 킬로바이트 단위로 출력하므로 1024 를 곱해 바이트로 변환합니다.
    """
    try:
        r = subprocess.run(["du", "-sk", str(path)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return int(r.stdout.split()[0]) * 1024
    except Exception:
        pass
    return 0


def find_image_files(folder: Path) -> list[Path]:
    """폴더를 재귀 탐색하여 이미지 파일 목록을 반환합니다.

    PermissionError 는 조용히 무시합니다.
    macOS 의 일부 시스템 폴더는 접근 권한이 없을 수 있기 때문입니다.
    """
    files = []
    try:
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                files.append(p)
    except PermissionError:
        pass
    return files


def do_scan(on_progress=None) -> list[dict]:
    """SCAN_TARGETS 를 순서대로 탐색하고 결과 dict 목록을 반환합니다.

    각 항목의 처리 방식:
    - pattern=None  : parent 안의 이미지 파일을 직접 수집 (file_list=True)
    - pattern=*.xxx : parent.glob(pattern) 으로 번들/폴더를 찾음 (file_list=False)

    on_progress: 진행 상황 콜백. 시그니처: (현재번호, 전체수, 항목이름)
    """
    results = []
    total = len(SCAN_TARGETS)
    for idx, (label, desc, parent, pattern) in enumerate(SCAN_TARGETS, 1):
        if on_progress:
            on_progress(idx, total, label)
        if not parent.exists():
            continue

        if pattern is None:
            # "사진 원본 파일" 항목: ~/Pictures 안의 이미지 파일을 직접 수집합니다.
            # Photos Library 번들(.photoslibrary, .iPhoto) 은 이미 별도 항목으로 처리하므로 건너뜁니다.
            items = []
            try:
                for child in parent.iterdir():
                    if child.suffix.lower() in {".photoslibrary", ".iphoto"}:
                        continue
                    if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                        items.append(child)
                    elif child.is_dir():
                        items.extend(find_image_files(child))
            except PermissionError:
                pass
            if items:
                size = sum(p.stat().st_size for p in items if p.exists())
                results.append({"label": label, "desc": desc,
                                 "paths": items, "size": size, "file_list": True})
        else:
            # Photos Library 나 캐시 폴더처럼 glob 패턴으로 찾는 항목입니다.
            found = sorted(parent.glob(pattern))
            for path in found:
                size = get_size_bytes(path)
                results.append({"label": label, "desc": desc,
                                 "paths": [path], "size": size, "file_list": False})
    return results


def move_to_trash(path: Path) -> bool:
    """Finder AppleScript 를 통해 파일/폴더를 휴지통으로 이동합니다.

    Python 의 send2trash 라이브러리 없이 macOS 네이티브 방식으로 처리합니다.
    반환값: 성공이면 True, 실패(Finder 없음 등)이면 False.
    """
    r = subprocess.run(
        ["osascript", "-e", f'tell app "Finder" to delete POSIX file "{path}"'],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def permanent_delete(path: Path) -> bool:
    """파일이나 폴더를 휴지통 없이 즉시 삭제합니다 (복구 불가).

    디렉토리는 shutil.rmtree 로, 파일은 Path.unlink 로 삭제합니다.
    예외가 발생하면 False 를 반환합니다.
    """
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 로거
# ---------------------------------------------------------------------------

LOG_DIR = HOME / "Library" / "Logs" / "photo_cleaner"


def setup_logger() -> Path:
    """로그 파일을 생성하고 Python logging 을 설정합니다.
    파일명에 PID 를 포함해 동시 실행 시 로그가 섞이지 않습니다.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"photo_cleaner_{ts}_{os.getpid()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    return log_path


# ---------------------------------------------------------------------------
# TUI 앱
# ---------------------------------------------------------------------------

# RichLog 에 출력할 도움말 텍스트입니다.
# [bold cyan]...[/] 같은 태그는 Textual 의 Rich 마크업 문법입니다.
HELP_TEXT = """\
[bold cyan]사용 가능한 명령어[/]

  [yellow]scan[/]                    사진 파일 스캔 (시작 시 자동 실행)
  [yellow]list[/]                    스캔 결과 다시 표시

  [yellow]delete <번호|all>[/]       휴지통으로 이동
  [yellow]rm <번호|all>[/]           완전 삭제 (복구 불가)

  [yellow]auth[/]                    Google Drive OAuth 인증
  [yellow]backup <번호|all>[/]       Google Drive 에 백업
  [yellow]backup-delete <번호|all>[/] 백업 후 휴지통 이동
  [yellow]backup-rm <번호|all>[/]    백업 후 완전 삭제

  [yellow]help[/]                    이 도움말 표시
  [yellow]quit[/] / [yellow]q[/]               종료\
"""


class StatusBar(Static):
    """화면 하단에 현재 상태를 한 줄로 표시하는 위젯입니다.

    `status` 는 reactive 변수입니다.
    값이 바뀌면 Textual 이 자동으로 render() 를 다시 호출해 화면을 갱신합니다.
    """
    status = reactive("준비")

    def render(self) -> str:
        return f" {self.status}"


class PhotoTUI(App):
    """사진 파일 정리 TUI 앱의 메인 클래스입니다.

    Textual 의 App 을 상속합니다.
    compose() 에서 위젯을 배치하고, on_mount() 에서 초기화를 수행합니다.
    """

    # CSS 는 위젯의 크기와 색상을 지정합니다.
    # $panel, $accent 같은 변수는 Textual 의 기본 테마 색상입니다.
    # height: 1fr 은 "남은 공간을 모두 차지"한다는 뜻입니다.
    CSS = """
    Screen { layout: vertical; }

    #table-label, #log-label, #cmd-label {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }

    #result-table {
        height: 10;
        border: solid $panel-lighten-2;
    }

    #log {
        height: 1fr;
        border: solid $panel-lighten-2;
        padding: 0 1;
    }

    #status-bar {
        height: 1;
        background: $primary-darken-2;
        color: $text;
    }

    #cmd-input {
        height: 3;
        border: solid $accent;
    }
    """

    # 키보드 단축키 바인딩입니다.
    # Footer 위젯이 이 목록을 읽어 화면 하단에 단축키 힌트를 표시합니다.
    BINDINGS = [
        Binding("ctrl+c", "quit",  "종료"),
        Binding("ctrl+r", "scan",  "재스캔"),
        Binding("f1",     "help",  "도움말"),
    ]

    def __init__(self):
        super().__init__()
        # 스캔 결과를 저장합니다. 각 원소는 result dict (ARCHITECTURE.md 참고).
        self._results: list[dict] = []

        # 동시 실행 방지 플래그입니다.
        # _scanning: 스캔이 이미 돌고 있을 때 "scan" 명령을 또 입력하면 무시합니다.
        # _busy: 백업이나 삭제가 진행 중일 때 새 작업 시작을 막습니다.
        self._scanning  = False
        self._busy      = False

        # Google OAuth 자격증명입니다. None 이면 Drive 기능을 쓸 수 없습니다.
        self._creds     = None

        self._log_path  = setup_logger()

        # 삭제 전 백업 여부 확인 대기 상태입니다.
        # None: 대기 중이 아님
        # (items, permanent): y/n/c 응답을 기다리는 중
        # on_input_submitted 에서 이 값이 있으면 명령 처리보다 먼저 응답 처리를 합니다.
        self._pending_delete: tuple | None = None

    # ── 레이아웃 ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """화면에 배치할 위젯을 위에서 아래 순서로 yield 합니다.

        Textual 은 이 메서드를 호출해 위젯 트리를 구성합니다.
        CSS 의 `layout: vertical` 이 위젯을 위아래로 쌓아줍니다.
        """
        yield Header(show_clock=True)
        with Vertical():
            yield Label(" [bold]스캔 결과[/]", id="table-label", markup=True)
            yield DataTable(id="result-table", cursor_type="row")
            yield Label(" [bold]로그[/]", id="log-label", markup=True)
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield StatusBar(id="status-bar")
        yield Label(
            " 명령 입력  (F1: 도움말  Ctrl+R: 재스캔  Ctrl+C: 종료)",
            id="cmd-label", markup=True,
        )
        yield Input(placeholder="> 명령어를 입력하세요...", id="cmd-input")
        yield Footer()

    def on_mount(self) -> None:
        """앱이 처음 열릴 때 한 번 실행됩니다.

        위젯이 모두 생성된 뒤 호출되므로 여기서 위젯에 접근해 초기화합니다.
        """
        self.title = "macOS 사진 파일 정리"

        # DataTable 에 컬럼 헤더를 추가합니다.
        table = self.query_one("#result-table", DataTable)
        table.add_columns("번호", "항목", "크기", "경로/파일 수")

        # 입력창에 포커스를 줍니다.
        # DataTable 이 기본 포커스를 받으면 키 입력이 DataTable 에만 전달됩니다.
        # on_mount 에서 명시적으로 Input 에 포커스를 줘야 명령 입력이 바로 됩니다.
        self.query_one("#cmd-input", Input).focus()
        self._log(f"로그 파일: {self._log_path}")

        # 이전 세션에서 저장된 token.json 이 있으면 자동으로 Drive 인증을 시도합니다.
        # 없으면 사용자에게 auth 명령 안내 메시지를 표시합니다.
        if gd.token_exists():
            try:
                self._creds = gd.authenticate()
                self._log("[green]Google Drive 인증 완료 (저장된 토큰 사용)[/]")
            except Exception:
                self._log("[yellow]Google Drive 토큰 갱신 실패 - auth 명령으로 재인증하세요[/]")
        elif gd.credentials_exist():
            self._log("[yellow]Google Drive: auth 명령으로 인증하세요[/]")
        else:
            self._log(
                f"[dim]Google Drive 백업 미설정 - "
                f"{gd.CREDS_FILE} 에 credentials.json 를 저장하세요[/]"
            )

        # 앱이 열리자마자 자동으로 스캔을 시작합니다.
        self.action_scan()

    # ── 명령 파싱 ─────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """사용자가 Enter 를 누를 때마다 호출됩니다.

        _pending_delete 가 있으면 명령이 아니라 y/n/c 응답으로 처리합니다.
        그렇지 않으면 _dispatch 로 명령을 파싱합니다.
        이 순서가 중요합니다: 백업 확인 대기 중에 "scan" 을 입력하면
        스캔이 시작되는 것이 아니라 "올바른 응답이 아닙니다" 메시지가 나와야 합니다.
        """
        raw = event.value.strip()
        self.query_one("#cmd-input", Input).clear()
        if not raw:
            return
        self._log(f"[dim]> {raw}[/]")

        # 백업 여부 대기 중이면 응답 처리를 먼저 합니다.
        if self._pending_delete is not None:
            self._handle_backup_confirm(raw)
            return

        self._dispatch(raw)

    def _dispatch(self, raw: str) -> None:
        """입력 문자열을 파싱해 해당 동작을 실행합니다.

        Python 3.10+ 의 match 문으로 명령을 라우팅합니다.
        `case "delete" if arg:` 처럼 가드 조건을 붙일 수 있습니다.
        인자 없이 "delete" 만 입력하면 case _ 로 떨어져 도움말을 안내합니다.
        """
        parts = raw.lower().split()
        cmd   = parts[0] if parts else ""
        arg   = parts[1] if len(parts) >= 2 else None

        match cmd:
            case "q" | "quit" | "exit":
                self.exit()
            case "scan":
                self.action_scan()
            case "list":
                self._show_results()
            case "help":
                self.action_help()
            case "auth":
                self._do_auth()
            case "delete" if arg:
                self._guard_busy(lambda: self._do_delete(arg, permanent=False))
            case "rm" if arg:
                self._guard_busy(lambda: self._do_delete(arg, permanent=True))
            case "backup" if arg:
                self._guard_busy(lambda: self._do_backup(arg, after=None))
            case "backup-delete" if arg:
                self._guard_busy(lambda: self._do_backup(arg, after="trash"))
            case "backup-rm" if arg:
                self._guard_busy(lambda: self._do_backup(arg, after="permanent"))
            case _:
                self._log(f"[red]알 수 없는 명령:[/] {raw}  (F1: 도움말)")

    def _guard_busy(self, fn) -> None:
        """_busy 가 True 이면 fn 실행을 막고 경고 메시지를 표시합니다.

        백업/삭제 중에 또 다른 작업이 시작되면 파일 상태가 꼬일 수 있습니다.
        """
        if self._busy:
            self._log("[yellow]다른 작업이 진행 중입니다. 잠시 기다려주세요.[/]")
            return
        fn()

    # ── 스캔 ──────────────────────────────────────────────────────────────────

    def action_scan(self) -> None:
        """스캔을 시작합니다. BINDINGS 의 Ctrl+R 과 "scan" 명령 모두 여기로 옵니다.

        _scanning 플래그로 중복 실행을 막습니다.
        실제 스캔은 _run_scan 워커 스레드에서 실행합니다.
        """
        if self._scanning:
            self._log("[yellow]이미 스캔 중입니다.[/]")
            return
        self._scanning = True
        self._set_status("스캔 중...")
        self._log("[cyan]스캔 시작...[/]")
        logging.info("스캔 시작")
        self._run_scan()

    @work(thread=True)
    def _run_scan(self) -> None:
        """백그라운드 스레드에서 실제 파일 시스템 스캔을 수행합니다.

        @work(thread=True): Textual 이 이 메서드를 자동으로 새 스레드에서 실행합니다.
        UI 업데이트는 call_from_thread() 로 메인 스레드에 위임합니다.
        스레드 안에서 직접 위젯에 접근하면 충돌이 발생합니다.
        """
        def progress(idx, total, label):
            # 스레드 안이므로 UI 접근은 call_from_thread 를 통해서만 합니다.
            self.call_from_thread(
                self._set_status, f"스캔 중... [{idx}/{total}] {label}"
            )
        results = do_scan(on_progress=progress)
        self.call_from_thread(self._on_scan_done, results)

    def _on_scan_done(self, results: list[dict]) -> None:
        """스캔 완료 시 메인 스레드에서 호출됩니다 (call_from_thread 경유).

        결과를 _results 에 저장하고 테이블과 상태바를 업데이트합니다.
        """
        self._scanning = False
        self._results  = results
        total = sum(r["size"] for r in results)
        self._show_results()
        self._log(f"[green]스캔 완료[/] - {len(results)}개 항목, 합계 [bold]{human(total)}[/]")
        self._set_status(f"스캔 완료  |  {len(results)}개 항목  |  합계 {human(total)}")
        logging.info(f"스캔 완료: {len(results)}개 항목, {human(total)}")

    def _show_results(self) -> None:
        """_results 를 DataTable 에 다시 렌더링합니다.

        100MB 이상 항목은 노란색으로 강조해 주의를 끕니다.
        file_list=True 항목은 파일 개수를, False 항목은 경로를 표시합니다.
        """
        table = self.query_one("#result-table", DataTable)
        table.clear()
        if not self._results:
            self._log("[yellow]스캔 결과 없음 - scan 명령으로 스캔하세요[/]")
            return
        for i, r in enumerate(self._results, 1):
            if r["file_list"]:
                path_info = f"{len(r['paths'])}개 파일"
            else:
                path_info = str(r["paths"][0])
            big = r["size"] > 100_000_000
            label = f"[yellow]{r['label']}[/]" if big else r["label"]
            table.add_row(str(i), label, human(r["size"]), path_info, key=str(i))

    # ── Google Drive 인증 ──────────────────────────────────────────────────────

    def _do_auth(self) -> None:
        if not gd.credentials_exist():
            self._log(
                f"[red]credentials.json 없음[/]\n"
                f"  Google Cloud Console에서 OAuth 클라이언트 ID를 생성하고\n"
                f"  [bold]{gd.CREDS_FILE}[/] 에 저장한 뒤 다시 실행하세요."
            )
            return
        self._log("[cyan]브라우저에서 Google 로그인 창이 열립니다...[/]")
        self._set_status("Google Drive 인증 중...")
        self._run_auth()

    @work(thread=True)
    def _run_auth(self) -> None:
        try:
            creds = gd.authenticate()
            self.call_from_thread(self._on_auth_done, creds, None)
        except Exception as e:
            self.call_from_thread(self._on_auth_done, None, str(e))

    def _on_auth_done(self, creds, error: str | None) -> None:
        if error:
            self._log(f"[red]인증 실패:[/] {error}")
            self._set_status("Google Drive 인증 실패")
            logging.error(f"Drive 인증 실패: {error}")
        else:
            self._creds = creds
            self._log("[green]Google Drive 인증 완료[/]")
            self._set_status("Google Drive 인증 완료")
            logging.info("Drive 인증 완료")

    # ── 백업 ──────────────────────────────────────────────────────────────────

    def _do_backup(self, target: str, after: str | None) -> None:
        if not self._results:
            self._log("[yellow]먼저 scan 명령으로 스캔하세요.[/]")
            return
        if not self._creds:
            self._log("[red]Google Drive 인증이 필요합니다. auth 명령을 먼저 실행하세요.[/]")
            return

        items = self._resolve_target(target)
        if items is None:
            return

        # 백업할 이미지 파일 미리 계산
        all_images: list[Path] = []
        for _, r in items:
            all_images.extend(gd.collect_images(r))
        seen: set[Path] = set()
        unique = [p for p in all_images if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]

        if not unique:
            self._log("[yellow]백업할 이미지 파일이 없습니다.[/]")
            self._log("[dim](캐시/메타데이터 항목은 백업 대상이 아닙니다)[/]")
            return

        after_label = {"trash": "휴지통 이동", "permanent": "완전 삭제", None: "없음"}[after]
        self._log(
            f"[cyan]Google Drive 백업 시작[/] - "
            f"{len(unique)}개 파일  |  삭제 후처리: {after_label}"
        )
        logging.info(f"Drive 백업 시작: {len(unique)}개 파일, after={after}")
        self._set_status(f"Drive 백업 중... 0/{len(unique)}")
        self._busy = True
        self._run_backup(items, unique, after)

    @work(thread=True)
    def _run_backup(
        self,
        items: list[tuple],
        unique: list[Path],
        after: str | None,
    ) -> None:
        date_str  = datetime.now().strftime("%Y-%m-%d")
        total     = len(unique)
        success   = 0
        fail      = 0

        def on_file(name, idx, _total):
            self.call_from_thread(
                self._set_status, f"Drive 업로드 [{idx}/{_total}] {name}"
            )
            self.call_from_thread(
                self._log, f"  [{idx}/{_total}] {name}"
            )

        def on_progress(uploaded, total_bytes, name):
            if total_bytes:
                pct = int(uploaded / total_bytes * 100)
                bar = "#" * (pct // 5) + "." * (20 - pct // 5)
                self.call_from_thread(
                    self._set_status,
                    f"Drive 업로드 [{bar}] {pct}%  {name}"
                )

        def on_done(_success, _fail, folder_url):
            nonlocal success, fail
            success, fail = _success, _fail
            self.call_from_thread(
                self._on_backup_done, success, fail, folder_url, items, after
            )

        def on_error(name, msg):
            nonlocal fail
            fail += 1
            label = f"[red]업로드 실패[/]: {name} - {msg}" if name else f"[red]오류[/]: {msg}"
            self.call_from_thread(self._log, label)
            logging.error(f"UPLOAD FAIL {name}: {msg}")

        session = gd.BackupSession(
            creds    = self._creds,
            date_str = date_str,
            on_file  = on_file,
            on_progress = on_progress,
            on_done  = on_done,
            on_error = on_error,
        )
        session.run([r for _, r in items])

    def _on_backup_done(
        self,
        success: int,
        fail: int,
        folder_url: str,
        items: list[tuple],
        after: str | None,
    ) -> None:
        self._busy = False

        if fail == 0:
            self._log(f"[bold green]백업 완료[/] - {success}개 파일 업로드 성공")
        else:
            self._log(
                f"[yellow]백업 부분 완료[/] - 성공 {success}개 / 실패 {fail}개"
            )
        self._log(f"  Drive 폴더: [link={folder_url}]{folder_url}[/link]")
        logging.info(f"Drive 백업 완료: 성공 {success} / 실패 {fail}")

        if after and success > 0:
            permanent = (after == "permanent")
            mode_str  = "완전 삭제" if permanent else "휴지통 이동"
            self._log(f"[cyan]백업 완료 후 {mode_str} 시작...[/]")
            self._busy = True
            self._run_delete(items, permanent)

        self._set_status(f"Drive 백업 완료 - {success}개 업로드")

    # ── 삭제 ──────────────────────────────────────────────────────────────────

    def _do_delete(self, target: str, permanent: bool) -> None:
        if not self._results:
            self._log("[yellow]먼저 scan 명령으로 스캔하세요.[/]")
            return

        items = self._resolve_target(target)
        if items is None:
            return

        # Google Drive 인증이 된 경우 백업 여부 확인
        if self._creds:
            self._pending_delete = (items, permanent)
            mode_label = "완전 삭제 (복구 불가)" if permanent else "휴지통 이동"
            self._log("")
            self._log(f"[bold yellow]삭제 전 Google Drive 백업을 할까요?[/]")
            self._log(f"  대상: {len(items)}개 항목  |  방식: {mode_label}")
            self._log(
                "  [green]y[/] = 백업 후 삭제   "
                "[yellow]n[/] = 백업 없이 바로 삭제   "
                "[red]c[/] = 취소"
            )
            self._set_status("백업 여부 확인 중 (y/n/c)")
            return

        self._start_delete(items, permanent)

    def _handle_backup_confirm(self, raw: str) -> None:
        """백업 여부 확인 응답 처리."""
        pending = self._pending_delete
        self._pending_delete = None
        self._set_status("")

        if pending is None:
            return

        items, permanent = pending
        ans = raw.strip().lower()

        if ans in ("y", "yes"):
            after = "permanent" if permanent else "trash"
            self._log("[cyan]백업 후 삭제를 시작합니다...[/]")
            self._guard_busy(lambda: self._do_backup_from_items(items, after))
        elif ans in ("n", "no"):
            self._log("[cyan]백업 없이 삭제를 시작합니다.[/]")
            self._guard_busy(lambda: self._start_delete(items, permanent))
        elif ans in ("c", "cancel"):
            self._log("[dim]취소됨.[/]")
        else:
            # 잘못된 입력 → 다시 대기
            self._pending_delete = (items, permanent)
            self._log(f"[red]'{raw}'[/]은 올바른 응답이 아닙니다.  y / n / c 중 하나를 입력하세요.")
            self._set_status("백업 여부 확인 중 (y/n/c)")

    def _do_backup_from_items(self, items: list[tuple], after: str) -> None:
        """items 가 이미 결정된 상태에서 바로 백업 진행."""
        all_images: list[Path] = []
        for _, r in items:
            all_images.extend(gd.collect_images(r))
        seen: set[Path] = set()
        unique = [p for p in all_images if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]

        if not unique:
            self._log("[yellow]백업할 이미지 파일이 없습니다 - 바로 삭제합니다.[/]")
            self._start_delete(items, after == "permanent")
            return

        after_label = "완전 삭제" if after == "permanent" else "휴지통 이동"
        self._log(
            f"[cyan]Google Drive 백업 시작[/] - "
            f"{len(unique)}개 파일  |  완료 후 {after_label}"
        )
        logging.info(f"Drive 백업 시작: {len(unique)}개 파일, after={after}")
        self._set_status(f"Drive 백업 중... 0/{len(unique)}")
        self._busy = True
        self._run_backup(items, unique, after)

    def _start_delete(self, items: list[tuple], permanent: bool) -> None:
        mode_str = "완전 삭제" if permanent else "휴지통 이동"
        self._log(f"[cyan]{mode_str} 시작 - {len(items)}개 항목[/]")
        logging.info(f"{mode_str} 시작: {len(items)}개 항목")
        self._set_status(f"{mode_str} 중...")
        self._busy = True
        self._run_delete(items, permanent)

    @work(thread=True)
    def _run_delete(self, items: list[tuple], permanent: bool) -> None:
        action   = permanent_delete if permanent else move_to_trash
        mode_str = "완전 삭제" if permanent else "휴지통 이동"
        freed    = 0

        for num, r in items:
            self.call_from_thread(
                self._set_status, f"{mode_str} 중... {r['label']}"
            )
            ok = 0
            for p in r["paths"]:
                if action(p):
                    ok += 1
                    logging.info(f"DEL {p}")
                else:
                    logging.warning(f"FAIL {p}")
                    self.call_from_thread(self._log, f"[red]실패:[/] {p.name}")
            freed += r["size"]
            self.call_from_thread(
                self._log,
                f"[green]완료[/] [{num}] {r['label']} - "
                f"{ok}/{len(r['paths'])}개  ({human(r['size'])})",
            )
            logging.info(f"DONE [{num}] {r['label']}: {ok}/{len(r['paths'])}개")

        deleted_nums = {str(num) for num, _ in items}
        self._results = [
            r for i, r in enumerate(self._results, 1)
            if str(i) not in deleted_nums
        ]
        self.call_from_thread(self._on_delete_done, freed, mode_str)

    def _on_delete_done(self, freed: int, mode_str: str) -> None:
        self._busy = False
        self._show_results()
        remaining = sum(r["size"] for r in self._results)
        self._log(
            f"[bold green]{mode_str} 완료[/] - "
            f"확보 [bold]{human(freed)}[/]  |  "
            f"남은 {len(self._results)}개 ({human(remaining)})"
        )
        if mode_str == "휴지통 이동":
            self._log("[dim]* 휴지통을 비워야 디스크 공간이 실제로 확보됩니다[/]")
        suffix = "  (* 휴지통 비우기 필요)" if mode_str == "휴지통 이동" else ""
        self._set_status(f"{mode_str} 완료 - {human(freed)} 확보{suffix}")
        logging.info(f"{mode_str} 완료: {human(freed)} 확보")

    # ── 공통 헬퍼 ─────────────────────────────────────────────────────────────

    def _resolve_target(self, target: str) -> list[tuple] | None:
        if target == "all":
            return list(enumerate(self._results, 1))
        try:
            idx = int(target)
            if not (1 <= idx <= len(self._results)):
                self._log(f"[red]번호 범위 초과:[/] 1~{len(self._results)}")
                return None
            return [(idx, self._results[idx - 1])]
        except ValueError:
            self._log(f"[red]숫자 또는 all 을 입력하세요:[/] {target}")
            return None

    def action_help(self) -> None:
        self.query_one("#log", RichLog).write(HELP_TEXT)

    def _log(self, msg: str) -> None:
        self.query_one("#log", RichLog).write(msg)

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-bar", StatusBar).status = msg


if __name__ == "__main__":
    PhotoTUI().run()
