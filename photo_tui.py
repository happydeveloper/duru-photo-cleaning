#!/usr/bin/env python3
"""
photo_tui.py - macOS 사진 파일 정리 TUI
"""

import os
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Label, RichLog, Static
from textual.reactive import reactive

import gdrive_backup as gd

# ── 스캔 로직 ─────────────────────────────────────────────────────────────────

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
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def get_size_bytes(path: Path) -> int:
    try:
        r = subprocess.run(["du", "-sk", str(path)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return int(r.stdout.split()[0]) * 1024
    except Exception:
        pass
    return 0


def find_image_files(folder: Path) -> list[Path]:
    files = []
    try:
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                files.append(p)
    except PermissionError:
        pass
    return files


def do_scan(on_progress=None) -> list[dict]:
    results = []
    total = len(SCAN_TARGETS)
    for idx, (label, desc, parent, pattern) in enumerate(SCAN_TARGETS, 1):
        if on_progress:
            on_progress(idx, total, label)
        if not parent.exists():
            continue
        if pattern is None:
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
            found = sorted(parent.glob(pattern))
            for path in found:
                size = get_size_bytes(path)
                results.append({"label": label, "desc": desc,
                                 "paths": [path], "size": size, "file_list": False})
    return results


def move_to_trash(path: Path) -> bool:
    r = subprocess.run(
        ["osascript", "-e", f'tell app "Finder" to delete POSIX file "{path}"'],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def permanent_delete(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception:
        return False


# ── 로거 ──────────────────────────────────────────────────────────────────────

LOG_DIR = HOME / "Library" / "Logs" / "photo_cleaner"


def setup_logger() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"photo_cleaner_{ts}_{os.getpid()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    return log_path


# ── TUI ───────────────────────────────────────────────────────────────────────

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
    status = reactive("준비")

    def render(self) -> str:
        return f" {self.status}"


class PhotoTUI(App):
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

    BINDINGS = [
        Binding("ctrl+c", "quit",  "종료"),
        Binding("ctrl+r", "scan",  "재스캔"),
        Binding("f1",     "help",  "도움말"),
    ]

    def __init__(self):
        super().__init__()
        self._results: list[dict] = []
        self._scanning  = False
        self._busy      = False          # 백업/삭제 진행 중 플래그
        self._creds     = None           # Google OAuth 자격증명
        self._log_path  = setup_logger()
        # 삭제 전 백업 확인 대기 상태: (items, permanent) 또는 None
        self._pending_delete: tuple | None = None

    # ── 레이아웃 ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
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
        self.title = "macOS 사진 파일 정리"
        table = self.query_one("#result-table", DataTable)
        table.add_columns("번호", "항목", "크기", "경로/파일 수")
        self.query_one("#cmd-input", Input).focus()
        self._log(f"로그 파일: {self._log_path}")

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

        self.action_scan()

    # ── 명령 파싱 ─────────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        self.query_one("#cmd-input", Input).clear()
        if not raw:
            return
        self._log(f"[dim]> {raw}[/]")

        # 백업 여부 대기 중이면 응답 처리
        if self._pending_delete is not None:
            self._handle_backup_confirm(raw)
            return

        self._dispatch(raw)

    def _dispatch(self, raw: str) -> None:
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
        if self._busy:
            self._log("[yellow]다른 작업이 진행 중입니다. 잠시 기다려주세요.[/]")
            return
        fn()

    # ── 스캔 ──────────────────────────────────────────────────────────────────

    def action_scan(self) -> None:
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
        def progress(idx, total, label):
            self.call_from_thread(
                self._set_status, f"스캔 중... [{idx}/{total}] {label}"
            )
        results = do_scan(on_progress=progress)
        self.call_from_thread(self._on_scan_done, results)

    def _on_scan_done(self, results: list[dict]) -> None:
        self._scanning = False
        self._results  = results
        total = sum(r["size"] for r in results)
        self._show_results()
        self._log(f"[green]스캔 완료[/] - {len(results)}개 항목, 합계 [bold]{human(total)}[/]")
        self._set_status(f"스캔 완료  |  {len(results)}개 항목  |  합계 {human(total)}")
        logging.info(f"스캔 완료: {len(results)}개 항목, {human(total)}")

    def _show_results(self) -> None:
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
