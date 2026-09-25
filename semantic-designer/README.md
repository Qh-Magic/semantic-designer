# Semantic Designer（內網自管 MVP）

這是一個 dependency-free 的本地 Designer MVP，實作「模型編輯 UI → YAML/Git 文件 → 驗證 → 審核 → 發布 → Cube adapter」流程。`model.yaml` 使用 JSON 形式，因為 JSON 是 YAML 1.2 的合法子集；可由標準 YAML 工具載入，避免 MVP 對外部 parser 產生依賴。

## 啟動

```bash
cd semantic-designer
python3 server.py
```

打開 `http://127.0.0.1:8787`。在 UI 中編輯關聯、指標與 view，然後依序儲存、送審、核准、發布。發布會寫入 `published/v<version>.json` 與 `published/v<version>.cube.yaml`；這些檔案應由 Git review 與部署流程管理。

## API seams

`GET /api/model` 讀取模型；`PUT /api/model` 儲存並執行 schema／關聯／指標驗證；`POST /api/review`、`POST /api/approve` 和 `POST /api/publish` 管理狀態；`POST /api/rollback` 以已發布版本回復；`GET /api/cube` 產出 Cube-compatible YAML-shaped 文件。此 MVP 不會直接連線 Trino 或啟動 Cube；部署環境應將產物交給既有的 Cube runtime，並在 CI／Airflow 執行查詢與 UAT。

目前驗證的是模型結構與關聯引用；production gate 還應加入 Trino 查詢、fanout、權限、golden dataset 與 OpenMetadata 同步測試。
