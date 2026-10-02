# Source from the repository root before running Node or Wrangler.
NODE_BIN="${NODE_BIN:-$(.venv/bin/python -c 'import config, os; print(os.environ.get("NODE_BIN", ""))')}"
export PATH="${NODE_BIN:-$HOME/.local/share/pi-node/node-v22.23.1-linux-x64/bin}:$PATH"
