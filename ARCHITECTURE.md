# 아키텍처 문서

이 문서는 코드가 어떻게 구성되어 있고, 데이터가 어떤 경로로 흐르는지 설명합니다.
처음 코드를 읽는 사람이 전체 구조를 빠르게 파악할 수 있도록 작성했습니다.

---

## 1. 파일 구성

```
photo-cleaning/
  photo_cleaner.py   # CLI 모드 — 터미널에서 대화식으로 사용
  photo_tui.py       # TUI 모드 — 패널 화면으로 사용 (권장)
  gdrive_backup.py   # Google Drive 백업 로직 (TUI 에서만 사용)
  README.md          # 사용자용 설명서
  ARCHITECTURE.md    # 이 문서
  DEBUGGING.md       # 디버깅 가이드
```

### 모듈 의존 관계

```
photo_tui.py
    |
    +-- gdrive_backup.py   (백업 기능)
    |       |
    |       +-- google-api-python-client  (Drive API)
    |       +-- google-auth-oauthlib      (OAuth 인증)
    |
    +-- textual  (TUI 프레임워크)

photo_cleaner.py
    |
    +-- 표준 라이브러리만 사용 (subprocess, shutil, threading ...)
```

`photo_cleaner.py`는 외부 패키지 없이 독립적으로 동작합니다.
`photo_tui.py`는 `gdrive_backup.py`를 가져다 씁니다.

---

## 2. photo_cleaner.py — CLI 흐름

한 번 실행하면 아래 순서대로 진행되고 종료합니다.

```
main()
  |
  +-- setup_logger()         로그 파일 생성
  |
  +-- Spinner (with 블록)
  |     |
  |     +-- scan()           각 SCAN_TARGETS 를 순서대로 탐색
  |           |
  |           +-- get_size_bytes()   du 명령으로 디렉토리 크기 측정
  |           +-- find_image_files() 이미지 파일 재귀 탐색
  |
  +-- print_results()        스캔 결과 표 출력
  |
  +-- ask_choice()           사용자에게 항목 번호 입력 받기
  |
  +-- ask_delete_mode()      휴지통 / 완전삭제 선택
  |
  +-- 최종 확인 ("yes" 입력)
  |
  +-- delete_with_progress() 항목별 삭제 + 진행 바 출력
        |
        +-- move_to_trash()     osascript 로 Finder 휴지통 이동
        +-- permanent_delete()  shutil.rmtree / Path.unlink
```

### 스피너 동작 원리

```
메인 스레드         백그라운드 스레드 (Spinner._spin)
-----------         ----------------------------------
scan() 호출  ---->  |/- \ 애니메이션 계속 출력
                    set_label("[1/10] Photos Library")
                    set_label("[2/10] iPhoto Library")
                    ...
scan() 반환  ---->  _stop.set() -> 스레드 종료
```

`threading.Lock`으로 `_label` 을 보호합니다.
메인 스레드가 `set_label()`을 호출할 때, 스핀 스레드가 동시에 읽으면
중간에 잘린 문자열이 나올 수 있기 때문입니다.

---

## 3. photo_tui.py — TUI 구조

### 화면 레이아웃

```
+----------------------------------------------+
| Header (제목 + 시계)                          |
+----------------------------------------------+
| [스캔 결과] 레이블                            |
| DataTable  번호 | 항목 | 크기 | 경로         |
+----------------------------------------------+
| [로그] 레이블                                 |
| RichLog    실시간 메시지 스크롤               |
+----------------------------------------------+
| StatusBar  현재 상태 한 줄                    |
+----------------------------------------------+
| [명령 입력] 레이블                            |
| Input      > 커서                            |
+----------------------------------------------+
| Footer     단축키 목록                        |
+----------------------------------------------+
```

### 클래스 구조

```
PhotoTUI (textual.App)
  |
  +-- 상태 변수
  |     _results        스캔 결과 목록
  |     _scanning       스캔 진행 중 여부 (중복 실행 방지)
  |     _busy           백업/삭제 진행 중 여부
  |     _creds          Google OAuth 자격증명 (None = 미인증)
  |     _pending_delete 삭제 전 백업 확인 대기 중인 작업
  |
  +-- 위젯
        DataTable  (#result-table)
        RichLog    (#log)
        StatusBar  (#status-bar)
        Input      (#cmd-input)
```

### 명령 처리 흐름

```
사용자가 Enter 누름
       |
on_input_submitted()
       |
       +-- _pending_delete 가 있으면?
       |         YES -> _handle_backup_confirm(raw)  백업 여부 응답 처리
       |         NO  -> _dispatch(raw)               명령 파싱
       |
_dispatch()
       |
       +-- "scan"        -> action_scan()
       +-- "list"        -> _show_results()
       +-- "auth"        -> _do_auth()
       +-- "delete N"    -> _guard_busy() -> _do_delete(N, permanent=False)
       +-- "rm N"        -> _guard_busy() -> _do_delete(N, permanent=True)
       +-- "backup N"    -> _guard_busy() -> _do_backup(N, after=None)
       +-- "backup-delete N" -> _guard_busy() -> _do_backup(N, after="trash")
       +-- "backup-rm N" -> _guard_busy() -> _do_backup(N, after="permanent")
       +-- 그 외         -> "알 수 없는 명령" 로그 출력
```

### 삭제 전 백업 확인 상태 머신

`delete` / `rm` 실행 시 `_creds`(인증 정보)가 있으면 아래 흐름으로 진행합니다.

```
_do_delete() 호출
       |
  _creds 있음?
  YES          NO
   |            |
   |            +-- _start_delete() -> 바로 삭제
   |
_pending_delete = (items, permanent)  # 대기 상태 저장
로그에 "y / n / c ?" 출력
상태바 "백업 여부 확인 중"
       |
       | (다음 Enter)
       |
_handle_backup_confirm(raw)
       |
       +-- "y"  -> _do_backup_from_items() -> 백업 -> 삭제
       +-- "n"  -> _start_delete()         -> 바로 삭제
       +-- "c"  -> _pending_delete = None  -> 취소
       +-- 그 외 -> _pending_delete 유지   -> 재질문
```

### 워커 스레드 패턴

Textual의 UI는 단일 스레드에서 돌아갑니다.
스캔이나 업로드처럼 시간이 걸리는 작업을 메인 스레드에서 실행하면
화면이 멈춥니다. 이를 막기 위해 `@work(thread=True)` 데코레이터로
별도 스레드에서 실행합니다.

```python
# 스레드 안에서 UI 위젯을 직접 건드리면 충돌이 발생합니다.
# call_from_thread()를 통해 메인 스레드에 UI 업데이트를 위임합니다.

@work(thread=True)
def _run_scan(self) -> None:
    results = do_scan(...)              # 시간 걸리는 작업 (스레드 안)
    self.call_from_thread(             # UI 업데이트는 메인 스레드로
        self._on_scan_done, results
    )
```

---

## 4. gdrive_backup.py — Drive 연동 구조

### OAuth 인증 흐름

처음 한 번만 브라우저 로그인이 필요합니다.
이후에는 저장된 `token.json`으로 자동 재인증합니다.

```
authenticate() 호출
       |
token.json 있음?
  YES                       NO
   |                         |
Credentials 로드            credentials.json 확인
   |                         |
토큰 유효?            InstalledAppFlow.run_local_server()
  YES    NO                  |
   |      |            브라우저 열림 -> 구글 로그인
   |   refresh()             |
   |      |            토큰 발급
   +------+                  |
   |                   token.json 저장
   |
Credentials 반환
```

파일 위치:
```
~/.config/photo_cleaner/
  credentials.json   # Google Cloud 에서 발급받은 OAuth 클라이언트 정보
  token.json         # 로그인 후 자동 저장되는 액세스/리프레시 토큰
```

### 업로드 흐름 (청크 방식)

큰 파일을 한 번에 보내면 네트워크 오류 시 처음부터 다시 보내야 합니다.
청크(4MB) 단위로 나눠 보내면 중간에 실패해도 이어서 재시도할 수 있습니다.

```
upload_file()
       |
MediaFileUpload(resumable=True, chunksize=4MB)
       |
request.next_chunk() 반복 호출
       |
  status 있음?  ->  on_progress(업로드된 바이트, 전체 바이트, 파일명)
  response 있음? -> 업로드 완료
```

### BackupSession 콜백 구조

`BackupSession`은 백업 진행 상황을 밖으로 알려주기 위해
함수 콜백을 받습니다. `photo_tui.py`가 이 콜백에서 UI를 업데이트합니다.

```
BackupSession.run()
       |
       +-- on_file(파일명, 현재번호, 전체수)   파일 시작 시
       +-- on_progress(업로드, 전체, 파일명)   청크마다
       +-- on_done(성공수, 실패수, Drive URL)  완료 시
       +-- on_error(파일명, 오류메시지)         오류 시
```

---

## 5. 핵심 데이터 구조

### 스캔 결과 항목 (result dict)

스캔 함수가 반환하는 리스트의 각 원소입니다.
프로그램 전체에서 이 구조를 주고받습니다.

```python
{
    "label": "Photos Library",              # 화면에 표시할 이름
    "desc":  "macOS 사진 앱 라이브러리",      # 설명
    "paths": [Path("~/Pictures/Photos Library.photoslibrary")],  # 경로 목록
    "size":  14_500_000_000,                # 바이트 단위 크기
    "file_list": False,                     # True = 개별 파일 목록
                                            # False = 디렉토리/번들 하나
}
```

`file_list` 값에 따라 삭제/백업 방식이 달라집니다.

| file_list | 의미 | 삭제 방식 |
|---|---|---|
| `False` | Photos Library 같은 번들/디렉토리 하나 | `paths[0]` 통째로 삭제 |
| `True` | `~/Pictures` 안의 개별 이미지 파일들 | `paths` 를 하나씩 순회해 삭제 |

### SCAN_TARGETS 구조

```python
SCAN_TARGETS = [
    (
        "Photos Library",          # label   : 화면 표시 이름
        "macOS 사진 앱 라이브러리", # desc    : 설명
        HOME / "Pictures",          # parent  : 탐색 시작 경로
        "*.photoslibrary",          # pattern : glob 패턴 (None = 직접 탐색)
    ),
    ...
]
```

`pattern`이 `None`인 항목("사진 원본 파일")은 glob 대신
`parent` 안을 직접 순회하며 이미지 파일을 수집합니다.

---

## 6. 로그 파일 구조

```
~/Library/Logs/photo_cleaner/photo_cleaner_20260620_095011_16493.log
                                              YYYYMMDD_HHMMSS_PID
```

PID(프로세스 ID)가 파일명에 포함되기 때문에
동시에 여러 프로세스를 실행해도 로그 파일이 섞이지 않습니다.

로그 형식:
```
2026-06-20 09:50:11  INFO     세션 시작
2026-06-20 09:50:11  INFO     SCAN [1/10] Photos Library  (/Users/.../Pictures)
2026-06-20 09:50:12  INFO     FOUND Photos Library: /Users/.../Photos Library.photoslibrary  (13.5 GB)
2026-06-20 09:50:12  INFO     SKIP  iPhoto Library - 매칭 없음 (*.iPhoto)
2026-06-20 09:50:12  INFO     스캔 완료: 2개 항목, 13.7 GB
2026-06-20 09:51:00  INFO     Drive 백업 시작: 42개 파일, after=trash
2026-06-20 09:51:01  INFO     DEL /Users/.../photo.heic
2026-06-20 09:51:02  WARNING  FAIL /Users/.../locked.jpg
2026-06-20 09:52:00  INFO     Drive 백업 완료: 성공 41 / 실패 1
```
