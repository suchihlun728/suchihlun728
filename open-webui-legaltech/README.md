# Open WebUI × Legaltech MCP：台灣法律助理

用 Docker 在本機跑 [Open WebUI](https://github.com/open-webui/open-webui)，以 Claude 為模型，接上 `https://legaltech.org.tw/mcp`（Streamable HTTP）。內建的「台灣法律助理」會先查法條與判決、附官方來源再回答，查不到就直說查不到。

## 啟動

需要 Docker Desktop（或 Docker Engine + Compose v2）與一把 [Anthropic API 金鑰](https://platform.claude.com/)。

```bash
cd open-webui-legaltech
cp .env.example .env        # 編輯 .env，填入 ANTHROPIC_API_KEY
docker compose up -d
docker compose logs -f setup
```

`setup` 印出下面這幾行就完成了（第一次啟動要下載數 GB 的映像檔，並等 Open WebUI 初始化一兩分鐘）：

```
✓ 模型連線：https://api.anthropic.com/v1
✓ MCP 伺服器：https://legaltech.org.tw/mcp（Streamable HTTP）
✓ MCP 連線測試成功，取得 N 個工具：search_taiwan_laws, search_taiwan_judgments, …
✓ 模型「台灣法律助理」已建立（底層模型：claude-opus-5-5）
✓ 預設模型設為「台灣法律助理」
```

打開 <http://localhost:3000>，預設模型就是「台灣法律助理」，直接提問即可。回答上方會出現「檢視來自 legaltech_… 的結果」，點開可以看到每次工具查詢的原始回傳。

## 這套設定做了什麼

| 檔案 | 作用 |
| --- | --- |
| `docker-compose.yml` | `open-webui` 服務（只綁 `127.0.0.1:3000`、免登入）＋一次性的 `setup` 服務 |
| `setup/setup.py` | 透過 Open WebUI 的管理 API 寫入設定；每次 `up` 都會重跑，已存在就更新 |
| `setup/system_prompt.md` | 助理的系統提示詞：先查再答、附官方來源、查不到就說查不到 |

`setup.py` 做四件事，都可以在介面的管理員設定與工作區裡看到、也可以手動修改：

1. 模型連線：新增 `https://api.anthropic.com/v1` 與金鑰。
2. 外部工具：新增一個 MCP（Streamable HTTP）伺服器，網址 `https://legaltech.org.tw/mcp`、不需驗證、ID `legaltech`。
3. 工作區的模型：建立「台灣法律助理」，底層模型 `claude-opus-5-5`，寫入系統提示詞並掛上 Legaltech 工具。
4. 預設模型：設為「台灣法律助理」。

## 調整

- **改提示詞**：編輯 `setup/system_prompt.md` 後執行 `docker compose up -d`（`setup` 會重跑並更新）。在介面上直接改也可以，但下次 `up` 會被檔案內容覆蓋。
- **換模型**：在 `.env` 設 `BASE_MODEL`（例如 `claude-sonnet-5-5`、`claude-haiku-5-5`）。指定的模型不存在時，`setup` 會改用清單中第一個 Claude 模型並印出警告。
- **費用**：每個問題通常會觸發數次工具查詢，判決全文很長，token 用量不小。對話標題、標籤這類背景任務預設交給 `claude-haiku-5-5`（`TASK_MODEL`）。
- **升級 Open WebUI**：改 `.env` 的 `OPEN_WEBUI_TAG`。設定腳本是對 v0.13.4 的管理 API 驗證的，跨大版本升級後請留意 `setup` 的輸出。

## 安全性

- `WEBUI_AUTH=false` 代表免登入、開網頁的人就是管理員，所以埠只綁在 `127.0.0.1`。這份設定是給單人本機使用的；不要在維持免登入的情況下把埠開放到區網或網際網路。
- API 金鑰只存在 `.env`（已列入 `.gitignore`）與 Docker volume 裡的 Open WebUI 資料庫。
- 你的提問會送到 Anthropic API；工具查詢的關鍵字會送到 legaltech.org.tw。請不要輸入可識別個人的案件細節。

## 疑難排解

| 現象 | 處理 |
| --- | --- |
| `setup` 印出「無法連上 MCP 伺服器」 | 確認這台機器連得到 `https://legaltech.org.tw/mcp`（公司網路或代理伺服器可能擋掉），再 `docker compose up -d` 重跑 |
| `setup` 印出「取不到任何模型」 | 金鑰錯誤或帳戶沒有額度；修正 `.env` 後重跑 |
| 回答時沒有呼叫工具 | 確認輸入框的工具選單裡「Legaltech 台灣法律資料」是開啟的，且選的是「台灣法律助理」而不是底層的 Claude 模型 |
| 想整個重來 | `docker compose down -v`（會刪除所有對話與設定） |

## 驗證狀態

這份設定是在一個無法下載映像檔、也連不到 legaltech.org.tw 的雲端沙箱裡寫的，所以驗證分成兩半：

- **已實測**：`setup.py` 對真正的 Open WebUI v0.13.4（PyPI 版，與映像檔同一份程式）執行成功且可重複執行；在瀏覽器介面中，「台灣法律助理」會自動帶上 MCP 工具、透過 Streamable HTTP 執行工具呼叫、把結果交回模型並顯示答案。這一段用的是本機替身的 MCP 伺服器與替身模型端點。
- **已實測**：真正的 `https://legaltech.org.tw/mcp` 以同一份提示詞的流程查詢法條與判決，結果見 [`TEST.md`](TEST.md)。
- **尚未實測**：`docker compose up` 本身（映像檔下載被沙箱擋住），以及「Open WebUI 容器 → Anthropic API」與「Open WebUI 容器 → legaltech.org.tw」這兩段真實連線。第一次在本機啟動時，請以 `docker compose logs setup` 的輸出為準。
