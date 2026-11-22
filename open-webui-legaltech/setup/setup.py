"""Configure a running Open WebUI for Taiwan legal research.

Idempotent: safe to run on every `docker compose up`. It
  1. points Open WebUI at the Claude API,
  2. registers the legaltech MCP server (Streamable HTTP) as an external tool,
  3. creates/updates the "台灣法律助理" model preset (system prompt + MCP tools attached),
  4. makes that preset the default model.

Only the Python standard library is used so it runs inside the Open WebUI image.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WEBUI_URL = os.environ.get("WEBUI_URL", "http://open-webui:8080").rstrip("/")
LLM_API_BASE_URL = os.environ.get("LLM_API_BASE_URL", "https://api.anthropic.com/v1").rstrip("/")
LLM_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_MODEL = os.environ.get("BASE_MODEL", "claude-opus-5-5")
MCP_URL = os.environ.get("MCP_URL", "https://legaltech.org.tw/mcp")
# With WEBUI_AUTH=false Open WebUI signs every caller in as this built-in admin.
ADMIN_EMAIL = os.environ.get("WEBUI_ADMIN_EMAIL", "admin@localhost")
ADMIN_PASSWORD = os.environ.get("WEBUI_ADMIN_PASSWORD", "admin")

MCP_SERVER_ID = "legaltech"
MCP_TOOL_ID = f"server:mcp:{MCP_SERVER_ID}"
PRESET_ID = "taiwan-legal-assistant"
PRESET_NAME = "台灣法律助理"
SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8").strip()


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status


def call(method, path, body=None, token=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(WEBUI_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise ApiError(error.code, error.read().decode(errors="replace")[:500]) from None
    return json.loads(raw) if raw else None


def wait_until_ready():
    for _ in range(120):
        try:
            call("GET", "/health", timeout=5)
            return
        except (ApiError, OSError):
            time.sleep(2)
    sys.exit(f"Open WebUI 沒有在 {WEBUI_URL} 啟動，請查看 `docker compose logs open-webui`。")


def sign_in():
    try:
        return call("POST", "/api/v1/auths/signin", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})["token"]
    except ApiError as error:
        sys.exit(
            f"無法以管理員登入 Open WebUI（{error}）。\n"
            "若你已改成 WEBUI_AUTH=true，請用 WEBUI_ADMIN_EMAIL / WEBUI_ADMIN_PASSWORD 提供管理員帳密。"
        )


def configure_llm_connection(token):
    config = call("GET", "/openai/config", token=token)
    urls = list(config.get("OPENAI_API_BASE_URLS") or [])
    keys = list(config.get("OPENAI_API_KEYS") or [])
    keys += [""] * (len(urls) - len(keys))
    # A fresh install lists api.openai.com with no key; drop it so the model list stays clean.
    kept = [(u, k) for u, k in zip(urls, keys) if u.rstrip("/") != LLM_API_BASE_URL and (k or "api.openai.com" not in u)]
    kept.append((LLM_API_BASE_URL, LLM_API_KEY))
    call(
        "POST",
        "/openai/config/update",
        {
            "ENABLE_OPENAI_API": True,
            "OPENAI_API_BASE_URLS": [u for u, _ in kept],
            "OPENAI_API_KEYS": [k for _, k in kept],
            "OPENAI_API_CONFIGS": {},
        },
        token=token,
    )
    print(f"✓ 模型連線：{LLM_API_BASE_URL}")


def mcp_connection():
    return {
        "type": "mcp",
        "url": MCP_URL,
        "path": "",
        "auth_type": "none",
        "key": "",
        "config": {"enable": True},
        "info": {
            "id": MCP_SERVER_ID,
            "name": "Legaltech 台灣法律資料",
            "description": "查詢台灣法條、裁判書、大法官解釋、法務部函釋與立法院法案（官方來源）",
        },
    }


def configure_mcp_server(token):
    current = call("GET", "/api/v1/configs/tool_servers", token=token).get("TOOL_SERVER_CONNECTIONS") or []
    others = [c for c in current if (c.get("info") or {}).get("id") != MCP_SERVER_ID]
    call(
        "POST",
        "/api/v1/configs/tool_servers",
        {"TOOL_SERVER_CONNECTIONS": others + [mcp_connection()]},
        token=token,
    )
    print(f"✓ MCP 伺服器：{MCP_URL}（Streamable HTTP）")

    try:
        result = call("POST", "/api/v1/configs/tool_servers/verify", mcp_connection(), token=token, timeout=90)
    except (ApiError, OSError) as error:
        print(f"⚠ 無法連上 MCP 伺服器並列出工具（{error}）。設定已寫入，但請確認這台機器連得到 {MCP_URL}。")
        return False
    names = [spec.get("name") for spec in result.get("specs") or []]
    print(f"✓ MCP 連線測試成功，取得 {len(names)} 個工具：{', '.join(names)}")
    return True


def pick_base_model(token):
    models = call("GET", "/api/models", token=token, timeout=90).get("data") or []
    # Presets appear in this list too; only real provider models can be a base.
    ids = [m["id"] for m in models if not (m.get("info") or {}).get("base_model_id")]
    if BASE_MODEL in ids:
        return BASE_MODEL
    if not ids:
        sys.exit(
            f"從 {LLM_API_BASE_URL} 取不到任何模型。請確認 .env 的 ANTHROPIC_API_KEY 正確、帳戶有額度，"
            "再重跑 `docker compose up -d`。"
        )
    claude = sorted(i for i in ids if i.startswith("claude-"))
    fallback = (claude or ids)[0]
    print(f"⚠ 找不到模型 {BASE_MODEL}，改用 {fallback}。可用模型：{', '.join(ids)}")
    return fallback


def upsert_preset(token, base_model):
    form = {
        "id": PRESET_ID,
        "name": PRESET_NAME,
        "base_model_id": base_model,
        "meta": {
            "description": "先查法條與判決、附官方來源再回答的台灣法律研究助理",
            "toolIds": [MCP_TOOL_ID],
            "suggestion_prompts": [
                {"content": "房東可以在租約到期前要求我搬走嗎？押金最多能收幾個月？"},
                {"content": "被公司資遣，資遣費和預告期間怎麼算？"},
                {"content": "網購商品收到後反悔，可以無條件退貨嗎？"},
            ],
        },
        "params": {"system": SYSTEM_PROMPT},
        "access_grants": [],
        "is_active": True,
    }
    try:
        call("GET", f"/api/v1/models/model?id={PRESET_ID}", token=token)
        exists = True
    except ApiError:
        exists = False
    call("POST", "/api/v1/models/model/update" if exists else "/api/v1/models/create", form, token=token)
    print(f"✓ 模型「{PRESET_NAME}」已{'更新' if exists else '建立'}（底層模型：{base_model}）")


def set_default_model(token):
    config = call("GET", "/api/v1/configs/models", token=token)
    config["DEFAULT_MODELS"] = PRESET_ID
    config["DEFAULT_PINNED_MODELS"] = PRESET_ID
    call("POST", "/api/v1/configs/models", config, token=token)
    print(f"✓ 預設模型設為「{PRESET_NAME}」")


def main():
    if not LLM_API_KEY:
        sys.exit("ANTHROPIC_API_KEY 是空的，請在 .env 填入後重跑。")
    wait_until_ready()
    token = sign_in()
    configure_llm_connection(token)
    mcp_ok = configure_mcp_server(token)
    upsert_preset(token, pick_base_model(token))
    set_default_model(token)
    print("\n設定完成。打開 http://localhost:%s 開始提問。" % os.environ.get("WEBUI_PORT", "3000"))
    if not mcp_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
