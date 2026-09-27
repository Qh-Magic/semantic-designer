# Semantic Designer（內網自管 MVP）

這是一個 dependency-free 的本地 Designer MVP，實作「模型編輯 UI → YAML/Git 文件 → 驗證 → 審核 → 發布 → Cube adapter」流程。`model.yaml` 使用 JSON 形式，因為 JSON 是 YAML 1.2 的合法子集；可由標準 YAML 工具載入，避免 MVP 對外部 parser 產生依賴。

## 啟動

```bash
cd semantic-designer
python3 server.py
```

打開 `http://127.0.0.1:8787`。在 UI 中編輯關聯、指標與 view，然後依序儲存、送審、核准、發布。發布會寫入 `published/v<version>.json` 與 `published/v<version>.cube.yaml`；這些檔案應由 Git review 與部署流程管理。

## API seams

`GET /api/model` 讀取模型；`PUT /api/model` 儲存並執行 schema／關聯／指標驗證；`POST /api/review`、`POST /api/approve` 和 `POST /api/publish` 管理狀態；`POST /api/rollback` 以已發布版本回復；`GET /api/cube` 產出 Cube-compatible YAML-shaped 文件；`POST /api/query` 接受受限 Query AST 並產出參數化 Trino SQL。此 MVP 不會直接連線 Trino 或啟動 Cube；部署環境應將產物交給既有的 Cube runtime，並在 CI／Airflow 執行查詢與 UAT。

Query AST 的第一版要求 `model.name`、`model.release`、`select`、`limit` 與由 gateway 注入的 `principal`。`select` 只能引用語意 dimension/measure；`where` 使用 typed expression tree；`time` 使用 `[from, to)` 邊界。預設 `options.target` 是 `trino`，也可以用同一份 AST 輸出受限的 Cube query。Trino 回傳 `sql`、`parameters`、`semantic_ir` 與 release／policy／lineage metadata；Cube 回傳 `query`、`semantic_ir` 與同一組 metadata。不接受任意 SQL。`semantic_ir_hash`、`query_ast_hash` 與 `policy_hash` 可用於查詢重現與稽核。

`load_sml_repository(path)` 可載入 SML-shaped YAML/JSON object files，解析 `dataset`、`metric`、`model` 等物件並轉成 designer 的 normalized contract。此 dependency-free parser 支援 mappings、scalar lists、list-of-maps 與 JSON（JSON 是 YAML 1.2 subset）；遇到未支援的 YAML 會明確回傳 `SmlParseError`，不會靜默猜測。

目前驗證的是模型結構與關聯引用；production gate 還應加入 Trino 查詢、fanout、權限、golden dataset 與 OpenMetadata 同步測試。
