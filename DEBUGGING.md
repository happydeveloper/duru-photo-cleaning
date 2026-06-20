# 디버깅 가이드

이 문서는 문제가 생겼을 때 원인을 찾고 해결하는 방법을 단계별로 설명합니다.

---

## 1. 로그 파일 읽기

프로그램이 실행되면 항상 로그 파일이 자동 생성됩니다.

### 로그 파일 위치

```
~/Library/Logs/photo_cleaner/
```

### 최신 로그 파일 열기

```bash
# 가장 최근 로그 파일을 터미널에 출력
cat $(ls -t ~/Library/Logs/photo_cleaner/*.log | head -1)

# 실시간으로 로그 따라가기 (프로그램 실행 중에 다른 터미널에서)
tail -f $(ls -t ~/Library/Logs/photo_cleaner/*.log | head -1)
```

### 로그에서 오류만 찾기

```bash
grep -E "ERROR|WARNING|FAIL" $(ls -t ~/Library/Logs/photo_cleaner/*.log | head -1)
```

---

## 2. 자주 발생하는 오류와 해결법

### 오류: `ModuleNotFoundError: No module named 'textual'`

TUI를 실행할 때 필요한 패키지가 설치되지 않은 경우입니다.

```bash
pip3 install textual google-api-python-client google-auth-oauthlib google-auth-httplib2
```

설치 후에도 같은 오류가 나면 Python 버전이 여러 개인 환경일 수 있습니다.

```bash
# 현재 실행 중인 Python 이 어느 pip 를 쓰는지 확인
python3 -m pip install textual
```

---

### 오류: `credentials.json 파일이 없습니다`

Google Drive 백업 기능을 처음 쓸 때 나타납니다.

**해결 순서:**

1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. 새 프로젝트 생성 (이름은 아무거나)
3. 왼쪽 메뉴 -> **API 및 서비스** -> **라이브러리** -> `Google Drive API` 검색 -> **사용 설정**
4. **API 및 서비스** -> **사용자 인증 정보** -> **사용자 인증 정보 만들기** -> **OAuth 클라이언트 ID**
5. 애플리케이션 유형: **데스크톱 앱** 선택 -> **만들기**
6. **JSON 다운로드** 클릭
7. 다운받은 파일을 아래 경로로 이동:

```bash
mkdir -p ~/.config/photo_cleaner
mv ~/Downloads/client_secret_*.json ~/.config/photo_cleaner/credentials.json
```

8. TUI에서 `auth` 명령 실행 -> 브라우저에서 Google 로그인

---

### 오류: `Google Drive 토큰 갱신 실패`

저장된 `token.json`이 만료되었거나 손상된 경우입니다.

```bash
# token.json 삭제 후 재인증
rm ~/.config/photo_cleaner/token.json
```

이후 TUI에서 `auth` 명령으로 다시 로그인합니다.

---

### 오류: 스캔 후 아무것도 나오지 않음

사진 관련 파일이 기본 위치에 없을 때입니다.
Photos Library를 외장 드라이브나 다른 경로로 옮긴 경우에 발생합니다.

**확인 방법:**

```bash
# Photos Library 가 어디 있는지 찾기
find ~ -name "*.photoslibrary" -maxdepth 5 2>/dev/null

# iPhoto Library 찾기
find ~ -name "*.iPhoto" -maxdepth 5 2>/dev/null
```

현재 `SCAN_TARGETS`에 등록된 경로만 스캔합니다.
경로를 추가하려면 `photo_tui.py` 또는 `photo_cleaner.py`의
`SCAN_TARGETS` 리스트에 항목을 추가하세요.

---

### 오류: 삭제 후에도 용량이 줄지 않음

**휴지통 이동**(`delete`)을 선택한 경우, 휴지통을 비워야
디스크 공간이 실제로 확보됩니다.

```bash
# 터미널에서 휴지통 비우기
osascript -e 'tell app "Finder" to empty trash'
```

또는 Finder 사이드바에서 휴지통 우클릭 -> **휴지통 비우기**

---

### 오류: `PermissionError` 또는 `Operation not permitted`

macOS의 개인 정보 보호 설정 때문에 일부 경로에 접근이 막힌 경우입니다.

**해결 방법:**

1. **시스템 설정** -> **개인 정보 보호 및 보안** -> **전체 디스크 접근 권한**
2. `+` 버튼 클릭 -> **터미널**(또는 사용 중인 앱) 추가
3. 프로그램을 다시 실행

---

### 오류: `osascript` 관련 오류

Finder가 실행되지 않은 환경(터미널 전용 서버 등)에서 발생합니다.
휴지통 이동 대신 **완전 삭제**(`rm`)를 사용하세요.

---

## 3. 단계별 디버깅 방법

### 3-1. 스캔이 느릴 때

스캔이 특정 항목에서 멈춘 것처럼 보이면, 로그 파일을 실시간으로 확인합니다.

```bash
# 터미널 두 개를 열어서:

# 터미널 1: 프로그램 실행
python3 photo_tui.py

# 터미널 2: 로그 실시간 확인
tail -f $(ls -t ~/Library/Logs/photo_cleaner/*.log | head -1)
```

로그에 `SCAN [3/10] 사진 원본 파일` 이 출력되고 멈춰있다면
`~/Pictures` 안에 파일이 매우 많은 것입니다. 기다리면 완료됩니다.

---

### 3-2. 명령이 동작하지 않을 때

TUI에서 명령을 입력해도 반응이 없으면 입력창에 포커스가 없는 것일 수 있습니다.

- **Tab** 키로 포커스를 입력창으로 이동하거나
- 입력창을 마우스로 클릭한 뒤 다시 명령을 입력하세요.

---

### 3-3. Google Drive 업로드가 실패할 때

```bash
# 로그에서 업로드 오류 확인
grep -E "FAIL|ERROR|업로드" $(ls -t ~/Library/Logs/photo_cleaner/*.log | head -1)
```

**자주 나오는 원인:**

| 로그 메시지 | 원인 | 해결 |
|---|---|---|
| `HttpError 403` | Drive 용량 초과 또는 권한 없음 | Drive 용량 확인, auth 재실행 |
| `HttpError 401` | 토큰 만료 | `token.json` 삭제 후 재인증 |
| `HttpError 429` | API 호출 한도 초과 | 잠시 기다렸다가 재시도 |
| `timeout` | 네트워크 느림 | 인터넷 연결 확인 |

---

### 3-4. 특정 파일만 삭제에 실패할 때

로그에서 `FAIL` 로 시작하는 줄을 찾으면 어떤 파일이 실패했는지 알 수 있습니다.

```bash
grep "FAIL" $(ls -t ~/Library/Logs/photo_cleaner/*.log | head -1)
```

파일이 잠겨 있는 경우(다른 앱이 열어 두고 있는 경우):

```bash
# 파일을 사용 중인 프로세스 확인
lsof /path/to/file
```

Photos 앱이 열려 있으면 닫은 뒤 다시 시도하세요.

---

## 4. 개발 중 디버깅

코드를 수정하면서 테스트할 때 유용한 방법들입니다.

### 실제 파일 건드리지 않고 스캔 로직 테스트

임시 폴더를 만들어서 테스트합니다.

```python
# 테스트용 스크립트 예시
from pathlib import Path
import tempfile, shutil

# 임시 홈 디렉토리 만들기
tmpdir = Path(tempfile.mkdtemp())
(tmpdir / "Pictures").mkdir()
(tmpdir / "Pictures" / "test.heic").touch()
(tmpdir / "Pictures" / "test.jpg").touch()

# photo_tui.py 의 HOME 을 임시 경로로 교체
import photo_tui
photo_tui.HOME = tmpdir

# 스캔 실행
from photo_tui import do_scan
results = do_scan()
print(results)

# 정리
shutil.rmtree(tmpdir)
```

### Google Drive 인증 없이 백업 로직 테스트

```python
from unittest.mock import MagicMock
import gdrive_backup as gd

# 실제 Drive 호출 없이 mock 으로 대체
gd.authenticate = lambda: MagicMock()
gd.token_exists = lambda: True
```

### 로그를 터미널에도 출력하고 싶을 때

`setup_logger()`에 `StreamHandler`를 추가합니다.

```python
# photo_cleaner.py 또는 photo_tui.py 의 setup_logger() 수정
import logging, sys

def setup_logger():
    ...
    logging.basicConfig(
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),  # 이 줄 추가
        ],
        ...
    )
```

---

## 5. 문제 제보 시 첨부할 정보

아래 명령을 실행하고 출력 결과를 함께 보내주세요.

```bash
# Python 버전
python3 --version

# 설치된 패키지 버전
pip3 show textual google-api-python-client google-auth-oauthlib 2>/dev/null

# 최신 로그 파일 내용
cat $(ls -t ~/Library/Logs/photo_cleaner/*.log | head -1)

# macOS 버전
sw_vers
```
