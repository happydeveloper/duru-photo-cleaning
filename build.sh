#!/usr/bin/env bash
# photo_tui 독립 실행 바이너리 빌드 스크립트
# 실행: ./build.sh
# 결과: dist/photo_tui  (Python 없이 실행 가능)
#
# Universal2 (arm64 + Intel) 빌드:
#   python.org에서 macOS용 Python 3.13을 설치하면 자동으로 universal2로 빌드됩니다.
#   https://www.python.org/downloads/macos/
#   설치 후 이 스크립트를 다시 실행하세요.

set -e

PYINSTALLER_FLAGS=(
  --onefile
  --name photo_tui
  --hidden-import "textual"
  --hidden-import "textual.app"
  --hidden-import "textual.widgets"
  --hidden-import "textual.widgets._data_table"
  --hidden-import "textual.widgets._rich_log"
  --hidden-import "textual.widgets._input"
  --hidden-import "textual.widgets._static"
  --hidden-import "textual.widgets._label"
  --hidden-import "textual.widgets._header"
  --hidden-import "textual.widgets._footer"
  --hidden-import "textual.reactive"
  --hidden-import "textual.containers"
  --hidden-import "textual.binding"
  --hidden-import "textual.work"
  --hidden-import "google.auth.transport.requests"
  --hidden-import "google.oauth2.credentials"
  --hidden-import "google_auth_oauthlib.flow"
  --hidden-import "googleapiclient.discovery"
  --hidden-import "googleapiclient.errors"
  --hidden-import "googleapiclient.http"
  --hidden-import "googleapiclient._helpers"
  --hidden-import "httplib2"
  --collect-all "textual"
  --collect-all "rich"
)

# python.org universal2 Python 감지 (3.13 → 3.9 순으로 탐색)
UNIVERSAL2_PYTHON=""
for ver in 3.13 3.12 3.11 3.10 3.9; do
  candidate="/Library/Frameworks/Python.framework/Versions/${ver}/bin/python${ver}"
  if [ -f "$candidate" ]; then
    arch_info=$(lipo -info "$candidate" 2>/dev/null || true)
    if echo "$arch_info" | grep -q "x86_64"; then
      UNIVERSAL2_PYTHON="$candidate"
      echo "universal2 Python 발견: $candidate"
      break
    fi
  fi
done

echo "=== photo_tui 빌드 시작 ==="

if [ -n "$UNIVERSAL2_PYTHON" ]; then
  echo "빌드 타입: universal2 (arm64 + Intel)"

  # universal2 Python 전용 venv 생성
  VENV_DIR=".venv_universal2"
  if [ ! -d "$VENV_DIR" ]; then
    "$UNIVERSAL2_PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet \
      textual \
      google-api-python-client \
      google-auth-oauthlib \
      google-auth-httplib2 \
      pyinstaller
  fi

  "$VENV_DIR/bin/pyinstaller" \
    "${PYINSTALLER_FLAGS[@]}" \
    --target-arch universal2 \
    photo_tui.py
else
  echo "빌드 타입: arm64 (Intel Mac 지원 불가)"
  echo "  → Intel Mac도 지원하려면 python.org에서 Python 3.13을 설치하세요."
  echo "  → https://www.python.org/downloads/macos/"
  echo ""
  pyinstaller "${PYINSTALLER_FLAGS[@]}" photo_tui.py
fi

echo ""
echo "=== 빌드 완료 ==="
echo "실행 파일: dist/photo_tui"
echo "아키텍처: $(lipo -archs dist/photo_tui 2>/dev/null || file dist/photo_tui)"
echo "크기: $(du -sh dist/photo_tui | cut -f1)"
echo ""
echo "실행 방법: ./dist/photo_tui"
