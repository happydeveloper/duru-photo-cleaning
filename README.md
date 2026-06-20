# macOS 사진 파일 정리 도구

macOS에서 사진 라이브러리, 캐시, 썸네일 등 사진 관련 저장공간을 스캔하고
Google Drive에 백업한 뒤 안전하게 삭제하는 도구입니다.

두 가지 인터페이스를 제공합니다.

- `photo_tui.py` — 대화형 TUI (권장)
- `photo_cleaner.py` — 단순 CLI

---

## 요구사항

- macOS
- Python 3.10 이상

```bash
pip3 install textual google-api-python-client google-auth-oauthlib google-auth-httplib2
```

---

## 실행

### TUI 모드 (권장)

```bash
python3 photo_tui.py
```

실행하면 자동으로 스캔을 시작하고 아래와 같은 화면이 나타납니다.

```
+----------------------------------------------------------+
| macOS 사진 파일 정리                            09:31:49 |
+----------------------------------------------------------+
|  스캔 결과                                               |
| +------------------------------------------------------+ |
| | 번호  항목                     크기     경로/파일 수  | |
| |  1   Photos Library           13.5 GB  ~/Pictures/.. | |
| |  2   Photos 데이터 디렉토리   156.9 MB ~/Library/... | |
| +------------------------------------------------------+ |
|  로그                                                    |
| +------------------------------------------------------+ |
| | 스캔 완료 - 2개 항목, 합계 13.7 GB                   | |
| +------------------------------------------------------+ |
| 스캔 완료 - 2개 항목  |  합계 13.7 GB                   |
| 명령 입력  (F1: 도움말  Ctrl+R: 재스캔  Ctrl+C: 종료)   |
| > _                                                      |
+----------------------------------------------------------+
```

### CLI 모드

```bash
python3 photo_cleaner.py
```

---

## TUI 명령어

하단 입력창에 명령어를 입력합니다.

### 스캔 / 탐색

| 명령 | 설명 |
|---|---|
| `scan` | 사진 파일 재스캔 (시작 시 자동 실행) |
| `list` | 스캔 결과 다시 표시 |

### 삭제

| 명령 | 설명 |
|---|---|
| `delete <번호\|all>` | 휴지통으로 이동 |
| `rm <번호\|all>` | 완전 삭제 (복구 불가) |

> Google Drive 인증이 된 상태에서 `delete` / `rm` 을 실행하면 삭제 전에 백업 여부를 묻습니다.
>
> ```
> 삭제 전 Google Drive 백업을 할까요?
>   대상: 1개 항목  |  방식: 휴지통 이동
>   y = 백업 후 삭제   n = 백업 없이 바로 삭제   c = 취소
> ```

### Google Drive 백업

| 명령 | 설명 |
|---|---|
| `auth` | Google Drive OAuth 인증 |
| `backup <번호\|all>` | 해당 항목의 사진을 Drive에 백업 |
| `backup-delete <번호\|all>` | Drive에 백업한 뒤 휴지통으로 이동 |
| `backup-rm <번호\|all>` | Drive에 백업한 뒤 완전 삭제 |

### 기타

| 명령 | 설명 |
|---|---|
| `help` | 도움말 표시 |
| `q` / `quit` | 종료 |

### 단축키

| 키 | 동작 |
|---|---|
| `F1` | 도움말 |
| `Ctrl+R` | 재스캔 |
| `Ctrl+C` | 종료 |

---

## Google Drive 백업 설정

백업 기능을 사용하려면 Google Cloud에서 OAuth 클라이언트를 발급받아야 합니다.
**최초 1회만** 필요한 작업입니다.

### 1단계 - Google Cloud 프로젝트 준비

1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. 새 프로젝트 생성 (예: `photo-cleaner`)
3. 왼쪽 메뉴 -> **API 및 서비스** -> **라이브러리**
4. `Google Drive API` 검색 후 **사용 설정**

### 2단계 - OAuth 클라이언트 ID 발급

1. **API 및 서비스** -> **사용자 인증 정보** -> **사용자 인증 정보 만들기**
2. **OAuth 클라이언트 ID** 선택
3. 애플리케이션 유형: **데스크톱 앱**
4. 이름 입력 후 **만들기**
5. **JSON 다운로드** 클릭

### 3단계 - credentials.json 저장

다운로드한 파일을 아래 경로에 저장합니다.

```
~/.config/photo_cleaner/credentials.json
```

```bash
mkdir -p ~/.config/photo_cleaner
mv ~/Downloads/client_secret_*.json ~/.config/photo_cleaner/credentials.json
```

### 4단계 - TUI에서 인증

```
> auth
```

브라우저에서 Google 로그인 창이 열립니다. 계정을 선택하고 권한을 허용하면 인증 완료입니다.
이후 인증 토큰이 `~/.config/photo_cleaner/token.json`에 저장되어
다음 실행부터는 자동으로 로그인됩니다.

### 백업 위치

업로드된 파일은 Google Drive의 다음 경로에 저장됩니다.

```
photo_cleaner_backup/
  YYYY-MM-DD/
    photo1.heic
    photo2.jpg
    ...
```

### 백업 대상

백업은 실제 사진 원본 파일만 대상으로 합니다.
캐시, 썸네일, 메타데이터는 백업하지 않습니다.

| 항목 | 백업 여부 | 설명 |
|---|---|---|
| Photos Library | O | 내부 `originals/` 폴더의 이미지만 추출 |
| iPhoto Library | O | 내부 이미지 파일 추출 |
| 사진 원본 파일 | O | 파일 그대로 업로드 |
| Photos 캐시 | X | 앱이 자동 재생성 |
| photoanalysisd 캐시 | X | 앱이 자동 재생성 |
| QuickLook 썸네일 | X | 앱이 자동 재생성 |
| Photos App Support | X | 앱이 자동 재생성 |
| iCloud 사진 캐시 | X | 앱이 자동 재생성 |
| Photos 데이터 | X | 메타데이터 |
| mediaanalysisd 캐시 | X | 앱이 자동 재생성 |

---

## 스캔 대상 경로

| 항목 | 위치 |
|---|---|
| Photos Library | `~/Pictures/*.photoslibrary` |
| iPhoto Library | `~/Pictures/*.iPhoto` |
| 사진 원본 파일 | `~/Pictures/` 내 이미지 파일 |
| Photos 캐시 | `~/Library/Caches/com.apple.Photos*` |
| photoanalysisd 캐시 | `~/Library/Caches/com.apple.photoanalysisd*` |
| QuickLook 썸네일 | `~/Library/Caches/com.apple.QuickLook*` |
| Photos App Support | `~/Library/Application Support/com.apple.Photos*` |
| iCloud 사진 캐시 | `~/Library/Application Support/iCloud Photos*` |
| Photos 데이터 | `~/Library/Photos/` |
| mediaanalysisd 캐시 | `~/Library/Caches/com.apple.mediaanalysisd*` |

---

## 삭제 방식

**휴지통으로 이동** (`delete`, `backup-delete`)
- Finder 휴지통으로 이동하므로 복구 가능
- 휴지통을 비워야 디스크 공간이 실제로 확보됨

**완전 삭제** (`rm`, `backup-rm`)
- 즉시 디스크에서 제거, 복구 불가
- 디스크 공간이 바로 확보됨

---

## 로그

모든 실행 기록은 자동으로 저장됩니다.

```
~/Library/Logs/photo_cleaner/photo_cleaner_YYYYMMDD_HHMMSS_<PID>.log
```

실행마다 새 파일이 생성되며 스캔 결과, 업로드/삭제 항목, 오류가 기록됩니다.
