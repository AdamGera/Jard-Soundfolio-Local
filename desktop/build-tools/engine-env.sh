# Development entry points use the source checkout's prepared dependencies.
helper_dir=$(cd -- "$(dirname -- "$0")" && pwd)
export JARD_ENGINE_ROOT=$(cd -- "$helper_dir/../.." && pwd)
jard_python="$JARD_ENGINE_ROOT/.venv/bin/python"
jard_model_setup="$helper_dir/model_setup.py"
export JARD_LLAMA_SERVER="$JARD_ENGINE_ROOT/model/qwen/llama-b12521/llama-server"
export PATH="/opt/homebrew/bin:$PATH"
export JARD_MODEL_DIR=$("$jard_python" -EsB "$jard_model_setup" model-path)
export PYTHONDONTWRITEBYTECODE=1
