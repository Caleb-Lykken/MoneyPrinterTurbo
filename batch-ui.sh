#!/usr/bin/env sh

# 批量生成界面。与主 WebUI 相互独立，可以同时运行。
# Batch generation UI. Independent of the main WebUI; both can run at once.

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="$CURRENT_DIR${PYTHONPATH:+:$PYTHONPATH}"

MPT_BATCH_HOST="${MPT_BATCH_HOST:-127.0.0.1}"
MPT_BATCH_PORT="${MPT_BATCH_PORT:-8601}"

# 与 webui.sh 保持一致的解释器查找顺序：项目 venv 优先，其次 uv，最后 PATH。
if [ -x "$CURRENT_DIR/.venv/bin/python" ]; then
  set -- "$CURRENT_DIR/.venv/bin/python" -m streamlit
elif [ -x "$CURRENT_DIR/../../../.venv/bin/python" ]; then
  # git worktree 场景：虚拟环境位于主仓库目录。
  set -- "$CURRENT_DIR/../../../.venv/bin/python" -m streamlit
elif command -v uv >/dev/null 2>&1; then
  set -- uv run streamlit
elif command -v streamlit >/dev/null 2>&1; then
  set -- streamlit
else
  echo "***** Neither project Python, uv, nor streamlit was found. Please install dependencies first. *****"
  exit 1
fi

echo "***** Batch UI address: http://$MPT_BATCH_HOST:$MPT_BATCH_PORT *****"
"$@" run "$CURRENT_DIR/webui/Batch.py" \
  --server.address="$MPT_BATCH_HOST" \
  --server.port="$MPT_BATCH_PORT" \
  --browser.serverAddress="$MPT_BATCH_HOST" \
  --browser.gatherUsageStats=False \
  --client.toolbarMode=minimal \
  --logger.hideWelcomeMessage=True \
  --server.showEmailPrompt=False
