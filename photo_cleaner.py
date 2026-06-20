#!/usr/bin/env python3
"""
photo_cleaner.py — macOS 사진 파일 정리 CLI

TUI 없이 터미널에서 대화식으로 사용하는 단순 버전입니다.
외부 패키지가 필요 없어 어느 Mac 에서도 바로 실행됩니다.

실행:
    python3 photo_cleaner.py

흐름:
    1. 스캔  — 사진 관련 파일/라이브러리를 찾아 목록과 크기를 보여줍니다.
    2. 선택  — 삭제할 항목 번호를 입력합니다.
    3. 확인  — 휴지통/완전삭제 선택 후 "yes"로 최종 승인합니다.
    4. 삭제  — 진행 바와 함께 삭제하고 결과를 출력합니다.
"""

import os
import signal
import sys
import subprocess
import shutil
import threading
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 시그널 핸들러 — 커서 복원
# ---------------------------------------------------------------------------
# 스피너가 실행 중일 때 Ctrl+C 로 종료하면 커서가 숨겨진 채로 터미널이
# 남을 수 있습니다. SIGINT/SIGTERM 을 받으면 커서를 반드시 복원합니다.

def _restore_cursor(signum=None, frame=None):
    sys.stdout.write("\033[?25h")   # 커서 표시 (ANSI 이스케이프 코드)
    sys.stdout.flush()
    if signum is not None:
        sys.exit(1)

signal.signal(signal.SIGINT,  _restore_cursor)
signal.signal(signal.SIGTERM, _restore_cursor)


# ---------------------------------------------------------------------------
# 색상 출력 헬퍼
# ---------------------------------------------------------------------------
# ANSI 이스케이프 코드로 터미널 색상을 지정합니다.
# c(text, COLOR) 형태로 사용하면 색상을 씌운 뒤 자동으로 원래대로 돌립니다.

BOLD   = "\033[1m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"   # 색상/스타일 초기화
DIM    = "\033[2m"
CLR    = "\033[2K\r" # 현재 줄 전체 지우고 커서를 줄 맨 앞으로 이동


def c(text, color):
    """텍스트에 색상을 적용합니다. 색상 코드 이후 RESET 을 자동으로 붙입니다."""
    return f"{color}{text}{RESET}"


# ---------------------------------------------------------------------------
# 로거 설정
# ---------------------------------------------------------------------------
# 파일에만 기록하고 터미널에는 출력하지 않습니다.
# 파일명에 PID 를 포함해 동시에 여러 번 실행해도 로그가 섞이지 않습니다.

LOG_DIR  = Path.home() / "Library" / "Logs" / "photo_cleaner"
LOG_FILE: Optional[Path] = None


def setup_logger() -> Path:
    """로그 파일을 생성하고 Python logging 을 설정합니다."""
    global LOG_FILE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_FILE = LOG_DIR / f"photo_cleaner_{ts}_{os.getpid()}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )
    return LOG_FILE


def log(msg: str, level: str = "info"):
    """로그 파일에 메시지를 기록합니다. level 은 "info"/"warning"/"error" 등."""
    getattr(logging, level)(msg)


# ---------------------------------------------------------------------------
# 스피너 — 백그라운드 애니메이션
# ---------------------------------------------------------------------------
# 스캔처럼 시간이 걸리는 작업 중에 사용자가 진행 상황을 알 수 있도록
# 별도 스레드에서 회전하는 문자를 출력합니다.
#
# 사용법:
#     with Spinner("스캔 중... ") as sp:
#         sp.set_label("[1/10] Photos Library")
#         do_something()
#
# `with` 블록을 나오면 스피너가 자동으로 멈추고 커서가 복원됩니다.

class Spinner:
    _FRAMES = ["|", "/", "-", "\\", "|", "/", "-", "\\"]  # 회전 문자 순서

    def __init__(self, prefix: str = ""):
        self.prefix  = prefix            # 스피너 앞에 항상 표시할 고정 문자열
        self._stop   = threading.Event() # 스레드에게 "이제 멈춰" 신호를 보내는 이벤트
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._label  = ""                # 현재 표시 중인 진행 메시지
        self._lock   = threading.Lock()  # _label 읽기/쓰기를 동기화

    def set_label(self, label: str):
        """스피너 옆에 표시할 텍스트를 실시간으로 바꿉니다 (스레드 안전)."""
        with self._lock:
            self._label = label

    def _spin(self):
        """백그라운드 스레드가 실행하는 루프. 매 0.08초마다 한 프레임씩 출력합니다."""
        i = 0
        while not self._stop.is_set():
            with self._lock:
                label = self._label
            frame = self._FRAMES[i % len(self._FRAMES)]
            # CLR 로 이전 줄을 지운 뒤 새 프레임을 같은 줄에 씁니다 (깜빡임 없이 갱신).
            sys.stdout.write(f"{CLR}  {c(frame, CYAN)} {self.prefix}{label}")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1

    def __enter__(self):
        sys.stdout.write("\033[?25l")  # 커서 숨김 — 스피너와 커서가 겹치면 지저분함
        sys.stdout.flush()
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()       # 스레드에게 종료 신호
        self._thread.join()    # 스레드가 실제로 끝날 때까지 기다림
        sys.stdout.write(CLR)
        sys.stdout.write("\033[?25h")  # 커서 복원
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# 진행 바
# ---------------------------------------------------------------------------

def progress_bar(current: int, total: int, width: int = 30) -> str:
    """[####......] 형태의 진행 바 문자열을 반환합니다."""
    pct    = current / total if total else 0
    filled = int(width * pct)
    bar    = "#" * filled + "." * (width - filled)
    return f"[{c(bar, GREEN)}] {c(f'{pct*100:5.1f}%', BOLD)} ({current}/{total})"


# ---------------------------------------------------------------------------
# 크기 계산
# ---------------------------------------------------------------------------

def get_size_bytes(path: Path) -> int:
    """파일 또는 디렉토리의 실제 디스크 사용량(bytes)을 반환합니다.

    Python 의 os.path.getsize() 는 디렉토리 내부를 합산하지 않아서
    macOS 내장 du 명령을 대신 사용합니다.
    -s: 합계만 출력  -k: 킬로바이트 단위
    """
    try:
        result = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return int(result.stdout.split()[0]) * 1024  # KB -> bytes
    except Exception:
        pass
    return 0


def human(size: int) -> str:
    """바이트 수를 사람이 읽기 쉬운 단위(KB/MB/GB)로 변환합니다."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


# ---------------------------------------------------------------------------
# 스캔 대상 목록
# ---------------------------------------------------------------------------
# 각 항목은 (표시이름, 설명, 탐색경로, glob패턴) 튜플입니다.
# pattern 이 None 이면 glob 대신 직접 이미지 파일을 수집합니다.
#
# 새 경로를 추가하려면 이 목록에 튜플을 하나 더 붙이면 됩니다.

HOME = Path.home()

SCAN_TARGETS = [
    ("Photos Library",
     "macOS 사진 앱 라이브러리 (원본 + 썸네일 포함)",
     HOME / "Pictures", "*.photoslibrary"),

    ("iPhoto Library",
     "구형 iPhoto 라이브러리",
     HOME / "Pictures", "*.iPhoto"),

    # pattern=None 이면 ~/Pictures 안을 직접 순회해 이미지 파일을 수집합니다.
    # .photoslibrary 와 .iPhoto 는 위에서 따로 처리하므로 여기서는 건너뜁니다.
    ("사진 원본 파일 (Pictures)",
     "~/Pictures 안의 이미지 파일",
     HOME / "Pictures", None),

    ("Photos 캐시",
     "Photos 앱 분석/썸네일 캐시",
     HOME / "Library/Caches", "com.apple.Photos*"),

    ("photoanalysisd 캐시",
     "얼굴/장면 인식 캐시",
     HOME / "Library/Caches", "com.apple.photoanalysisd*"),

    ("QuickLook 썸네일",
     "파일 미리보기 캐시 (사진 포함)",
     HOME / "Library/Caches", "com.apple.QuickLook*"),

    ("Photos App Support",
     "Photos 앱 지원 데이터",
     HOME / "Library/Application Support", "com.apple.Photos*"),

    ("iCloud 사진 캐시",
     "iCloud Photo Library 임시 데이터",
     HOME / "Library/Application Support", "iCloud Photos*"),

    ("Photos 데이터 디렉토리",
     "~/Library/Photos (내부 메타데이터)",
     HOME / "Library", "Photos"),

    ("mediaanalysisd 캐시",
     "미디어 분석 캐시",
     HOME / "Library/Caches", "com.apple.mediaanalysisd*"),
]

# 이미지로 인식할 확장자 목록
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".raw", ".cr2", ".cr3",
    ".nef", ".arw", ".dng", ".tif", ".tiff", ".gif", ".bmp", ".webp",
    ".avif", ".psd", ".ai", ".svg"
}


def find_image_files(folder: Path) -> list[Path]:
    """폴더 안의 이미지 파일을 재귀적으로 찾아 목록을 반환합니다."""
    files = []
    try:
        for p in folder.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                files.append(p)
    except PermissionError:
        # 접근 권한이 없는 폴더는 조용히 건너뜁니다.
        pass
    return files


def scan(spinner: Spinner) -> list[dict]:
    """SCAN_TARGETS 를 순서대로 탐색해 결과 목록을 반환합니다.

    반환 값 예시:
        [
            {
                "label": "Photos Library",
                "desc":  "macOS 사진 앱 라이브러리",
                "paths": [Path(".../Photos Library.photoslibrary")],
                "size":  14_500_000_000,  # bytes
                "is_file_list": False,    # 번들/디렉토리 단위
            },
            ...
        ]

    is_file_list=True 이면 paths 가 개별 이미지 파일 목록,
    is_file_list=False 이면 paths[0] 이 번들/디렉토리 하나입니다.
    """
    results = []
    total   = len(SCAN_TARGETS)

    for idx, (label, desc, parent, pattern) in enumerate(SCAN_TARGETS, 1):
        spinner.set_label(f"[{idx}/{total}] {label}")
        log(f"SCAN  [{idx}/{total}] {label}  ({parent})")

        if not parent.exists():
            log(f"SKIP  {label} - 경로 없음: {parent}")
            continue

        if pattern is None:
            # 이미지 파일을 직접 수집합니다 (사진 원본 파일 항목 전용).
            items = []
            try:
                for child in parent.iterdir():
                    # .photoslibrary 와 .iPhoto 는 위에서 따로 처리합니다.
                    if child.suffix.lower() in {".photoslibrary", ".iphoto"}:
                        continue
                    if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
                        items.append(child)
                    elif child.is_dir():
                        items.extend(find_image_files(child))
            except PermissionError:
                pass

            if items:
                total_size = sum(p.stat().st_size for p in items if p.exists())
                log(f"FOUND {label}: {len(items)}개 파일, {human(total_size)}")
                results.append({
                    "label": label, "desc": desc,
                    "paths": items, "size": total_size,
                    "is_file_list": True,
                })
        else:
            # glob 패턴으로 번들/디렉토리를 찾습니다.
            found = sorted(parent.glob(pattern))
            if not found:
                log(f"SKIP  {label} - 매칭 없음 ({pattern})")
                continue
            for path in found:
                size = get_size_bytes(path)
                log(f"FOUND {label}: {path}  ({human(size)})")
                results.append({
                    "label": label, "desc": desc,
                    "paths": [path], "size": size,
                    "is_file_list": False,
                })

    return results


# ---------------------------------------------------------------------------
# 삭제 함수
# ---------------------------------------------------------------------------

def move_to_trash(path: Path) -> bool:
    """파일/디렉토리를 macOS Finder 휴지통으로 이동합니다.

    AppleScript 로 Finder 에게 삭제 명령을 보냅니다.
    Finder 가 실행 중이 아니면 실패할 수 있습니다.
    """
    script = f'tell app "Finder" to delete POSIX file "{path}"'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0


def permanent_delete(path: Path) -> bool:
    """파일/디렉토리를 즉시 완전 삭제합니다 (복구 불가).

    디렉토리는 shutil.rmtree 로, 파일은 Path.unlink 로 삭제합니다.
    """
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception as e:
        log(f"ERROR 삭제 실패 {path}: {e}", "error")
        print(c(f"  오류: {e}", RED))
        return False


def delete_with_progress(r: dict, mode: str) -> tuple[int, int]:
    """항목 하나를 진행 바와 함께 삭제하고 (성공 수, 전체 수)를 반환합니다.

    is_file_list=True 이면 파일마다 진행 바를 업데이트합니다.
    is_file_list=False 이면 번들/디렉토리 통째로 한 번에 삭제합니다.
    """
    paths  = r["paths"]
    label  = r["label"]
    action = move_to_trash if mode == "trash" else permanent_delete
    total  = len(paths)
    ok     = 0

    if r["is_file_list"]:
        # 파일이 많을 때: 진행 바를 업데이트하며 하나씩 삭제합니다.
        for i, p in enumerate(paths, 1):
            bar  = progress_bar(i, total)
            name = p.name[:35].ljust(35)   # 긴 파일명은 35자로 자름
            sys.stdout.write(f"{CLR}    {bar}  {c(name, DIM)}")
            sys.stdout.flush()

            success = action(p)
            if success:
                ok += 1
                log(f"DEL   {'trash' if mode=='trash' else 'perm'}  {p}")
            else:
                log(f"FAIL  {p}", "warning")

        sys.stdout.write(CLR)
        sys.stdout.flush()
        result_line = (
            f"    {c('완료', GREEN)}  {ok}/{total}개 파일 "
            f"{'휴지통 이동' if mode == 'trash' else '삭제'}"
        )
        print(result_line)
        log(f"DONE  {label}: {ok}/{total}개 파일")

    else:
        # 번들/디렉토리: 경로 하나를 통째로 삭제합니다.
        for p in paths:
            sys.stdout.write(f"{CLR}    처리 중: {c(p.name, DIM)}")
            sys.stdout.flush()
            success = action(p)
            status  = c("완료", GREEN) if success else c("실패", RED)
            sys.stdout.write(CLR)
            print(f"    {p.name}  ->  {status}")
            if success:
                ok += 1
                log(f"DEL   {'trash' if mode=='trash' else 'perm'}  {p}")
            else:
                log(f"FAIL  {p}", "warning")
        log(f"DONE  {label}: {ok}/{total}")

    return ok, total


# ---------------------------------------------------------------------------
# 출력 헬퍼
# ---------------------------------------------------------------------------

def print_header():
    print()
    print(c("=" * 62, CYAN))
    print(c("  macOS 사진 파일 정리 도구", BOLD))
    print(c("=" * 62, CYAN))
    print()


def print_results(results: list[dict]):
    """스캔 결과를 번호/항목/크기/설명 형태의 표로 출력합니다."""
    if not results:
        print(c("  스캔 결과 없음 - 사진 관련 파일을 찾지 못했습니다.", DIM))
        return

    total = sum(r["size"] for r in results)
    print(c(f"  {'번호':<4} {'항목':<28} {'크기':>10}  설명", BOLD))
    print("  " + "-" * 66)
    for i, r in enumerate(results, 1):
        big      = r["size"] > 100_000_000  # 100MB 이상이면 노란색으로 강조
        size_str = c(human(r["size"]), YELLOW if big else RESET)
        extra    = f" ({len(r['paths'])}개 파일)" if r["is_file_list"] else ""
        path_hint = ""
        if not r["is_file_list"] and len(r["paths"]) == 1:
            path_hint = f"\n       {c(str(r['paths'][0]), DIM)}"
        print(f"  {i:<4} {r['label']:<28} {size_str:>10}  {r['desc']}{extra}{path_hint}")
    print("  " + "-" * 66)
    print(f"  {'합계':>33} {c(human(total), BOLD + YELLOW)}")
    print()


def ask_choice(results: list[dict]) -> Optional[list[int]]:
    """삭제할 항목 번호를 입력 받아 0-based 인덱스 목록을 반환합니다.
    취소하면 None 을 반환합니다.
    """
    print("삭제할 항목 번호를 입력하세요.")
    print(c("  예) 1       -> 1번만", DIM))
    print(c("  예) 1 3 5   -> 여러 항목", DIM))
    print(c("  예) all     -> 전체", DIM))
    print(c("  예) q       -> 종료", DIM))
    print()

    raw = input("  선택: ").strip().lower()

    if raw in ("q", "quit", "exit", ""):
        return None
    if raw == "all":
        return list(range(len(results)))
    try:
        # 입력값을 공백으로 분리해 각각 정수로 변환합니다.
        # 화면에는 1-based 로 표시했으므로 1 을 빼서 0-based 인덱스로 변환합니다.
        nums  = [int(x) - 1 for x in raw.split()]
        valid = [n for n in nums if 0 <= n < len(results)]
        if not valid:
            print(c("  유효하지 않은 번호입니다.", RED))
            return None
        return valid
    except ValueError:
        print(c("  숫자를 입력해주세요.", RED))
        return None


def ask_delete_mode() -> str:
    """'trash' / 'permanent' / 'cancel' 중 하나를 반환합니다."""
    print()
    print("삭제 방식을 선택하세요:")
    print("  1) 휴지통으로 이동  (안전, 나중에 복구 가능)")
    print("  2) 완전 삭제        (복구 불가 - 디스크 공간 즉시 확보)")
    print("  q) 취소")
    print()
    mode = input("  선택 [1/2/q]: ").strip().lower()
    if mode == "1":
        return "trash"
    if mode == "2":
        return "permanent"
    return "cancel"


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    log_path = setup_logger()
    print_header()

    log("=" * 50)
    log("세션 시작")

    # 1단계: 스캔
    # Spinner 컨텍스트 매니저가 스피너 시작/종료와 커서 숨김/복원을 담당합니다.
    with Spinner("스캔 중... ") as sp:
        results = scan(sp)

    print(c(f"  스캔 완료 - {len(results)}개 항목 발견", GREEN))
    print(c(f"  로그 파일: {log_path}", DIM))
    print()

    if not results:
        log("결과 없음. 종료.")
        sys.exit(0)

    print_results(results)
    log(f"스캔 결과: {len(results)}개 항목, 합계 {human(sum(r['size'] for r in results))}")

    # 2단계: 항목 선택
    indices = ask_choice(results)
    if indices is None:
        log("사용자가 취소함.")
        print(c("\n  취소됨.", DIM))
        sys.exit(0)

    selected       = [results[i] for i in indices]
    total_selected = sum(r["size"] for r in selected)

    print()
    print(c(f"  선택된 항목: {len(selected)}개  ({human(total_selected)} 확보 예정)", BOLD))
    for r in selected:
        print(c(f"  - {r['label']}", YELLOW))
        log(f"SELECT {r['label']}  ({human(r['size'])})")

    # 3단계: 삭제 방식 선택
    mode = ask_delete_mode()
    if mode == "cancel":
        log("사용자가 삭제 방식 선택에서 취소함.")
        print(c("\n  취소됨.", DIM))
        sys.exit(0)

    # 4단계: 최종 확인 — "yes" 를 정확히 입력해야 진행합니다.
    print()
    warn    = "완전 삭제 (복구 불가)" if mode == "permanent" else "휴지통으로 이동"
    confirm = input(
        c(f"  [{warn}] 정말 진행할까요? (yes 입력 시 실행): ",
          RED if mode == "permanent" else YELLOW)
    ).strip().lower()

    if confirm != "yes":
        log("최종 확인에서 취소됨.")
        print(c("\n  취소됨.", DIM))
        sys.exit(0)

    log(f"삭제 시작 - 방식: {mode}")

    # 5단계: 삭제 실행
    print()
    freed = 0
    start = time.time()

    for idx, r in enumerate(selected, 1):
        print(f"  {c(f'[{idx}/{len(selected)}]', CYAN)} {c(r['label'], BOLD)}")
        log(f"START [{idx}/{len(selected)}] {r['label']}")

        ok, total = delete_with_progress(r, mode)

        if ok > 0:
            freed += r["size"]

    elapsed = time.time() - start
    action  = "삭제" if mode == "permanent" else "휴지통 이동"

    print()
    print(c("=" * 62, GREEN))
    print(c(f"  {action} 완료!", BOLD + GREEN))
    print(c(f"  확보 용량: {human(freed)}", GREEN))
    print(c(f"  소요 시간: {elapsed:.1f}초", GREEN))
    if mode == "trash":
        print(c("  * 휴지통을 비워야 디스크 공간이 실제로 확보됩니다", DIM))
    print(c(f"  로그 파일: {log_path}", DIM))
    print(c("=" * 62, GREEN))
    print()

    log(f"완료 - {action}, 확보 용량: {human(freed)}, 소요: {elapsed:.1f}초")
    log("=" * 50)


if __name__ == "__main__":
    main()
