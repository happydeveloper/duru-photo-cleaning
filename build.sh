#!/usr/bin/env bash
# photo_tui 독립 실행 바이너리 빌드 스크립트
# 실행: ./build.sh
# 결과: dist/photo_tui  (Python 없이 실행 가능)

set -e

echo "=== photo_tui 빌드 시작 ==="

pyinstaller \
  --onefile \
  --name photo_tui \
  --hidden-import "textual" \
  --hidden-import "textual.app" \
  --hidden-import "textual.widgets" \
  --hidden-import "textual.widgets._data_table" \
  --hidden-import "textual.widgets._rich_log" \
  --hidden-import "textual.widgets._input" \
  --hidden-import "textual.widgets._static" \
  --hidden-import "textual.widgets._label" \
  --hidden-import "textual.widgets._header" \
  --hidden-import "textual.widgets._footer" \
  --hidden-import "textual.reactive" \
  --hidden-import "textual.containers" \
  --hidden-import "textual.binding" \
  --hidden-import "textual.work" \
  --hidden-import "google.auth.transport.requests" \
  --hidden-import "google.oauth2.credentials" \
  --hidden-import "google_auth_oauthlib.flow" \
  --hidden-import "googleapiclient.discovery" \
  --hidden-import "googleapiclient.errors" \
  --hidden-import "googleapiclient.http" \
  --hidden-import "googleapiclient._helpers" \
  --hidden-import "httplib2" \
  --collect-all "textual" \
  --collect-all "rich" \
  photo_tui.py

echo ""
echo "=== 빌드 완료 ==="
echo "실행 파일: dist/photo_tui"
echo "크기: $(du -sh dist/photo_tui | cut -f1)"
echo ""
echo "실행 방법: ./dist/photo_tui"
