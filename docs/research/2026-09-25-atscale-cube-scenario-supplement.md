# AtScale Universal Semantic Layer 與 Cube 的場景分工補充

研究日期：2026-09-25。本文補充 AtScale 與 Cube 官方文件的場景差異，供既有 AtScale 重新評估文件與架構 ADR 使用。

## 核心判斷

AtScale 的產品重心是「企業共用語意平台」：Design Center 提供模型編輯體驗，SML 將物件存成 YAML，Git 是來源控制，部署後透過 BI protocol、Python/AI 與 MCP 供多種消費者使用。[SML](https://documentation.atscale.com/container/creating-and-sharing-cubes/working-with-models-programmatically/sml) [Git integration](https://documentation.atscale.com/container/managing-atscale/managing-git/about-git) [Universal Semantic Layer](https://www.atscale.com/use-cases/universal-semantic-layer/)

Cube Core 的產品重心是「可嵌入的 headless semantic runtime」：模型以 YAML 或 JavaScript 定義 cubes、views、measures、dimensions、joins、pre-aggregations 與 access policies，然後由 REST、SQL、GraphQL 等 API 供自有 BI、產品與 AI 應用使用。[Cube model reference](https://docs.cube.dev/reference/data-modeling/cube) [Cube Core repository](https://github.com/cube-js/cube)

因此應該採場景分工，而不是讓兩個平台同時成為同一套 KPI 的權威來源。

## 適用場景比較

| 決策面 | AtScale 較適合 | Cube 較適合 |
|---|---|---|
| Universe 式建模 | 需要資料分析師在視覺化 canvas 建 dataset、dimension、relationship，再由工程師以 SML/Git 管理 | 團隊接受 code-first；自建 editor、schema registry 或由 dbt/metadata 產生模型 |
| 多種 BI 客戶端 | 企業同時使用 Power BI、Excel、Tableau、Looker、Google Sheets，且希望取得現成 JDBC/ODBC/XMLA/連線描述 | 產品團隊掌握前端與查詢 API，主要需要 REST/SQL/GraphQL，而非完整企業 BI connection portal |
| Python / AI | 需要 AI-Link、MCP 或將已治理的語意直接提供給 AI agent | 需要把語意查詢嵌入 Python service、應用程式或自有 agent，並自行包裝 client 與治理流程 |
| 預聚合與企業查詢 | 大型企業希望由 semantic aggregation 與需求驅動 aggregate 管理跨 BI 查詢 | 需要工程師明確控制 pre-aggregations、Cube Store、refresh keys、快取與 query API |
| Embedded analytics | 平台治理與 BI 連接優先，接受較重的 Kubernetes 平台 | SaaS 內嵌、每租戶 security context、API 查詢與自有 UI 優先 |
| 完全自管／開源 | 只有在授權、離線安裝、影像來源與支援合約通過審查後才適合 | Cube Core repository 宣稱 backend Apache 2.0、client MIT；可自行 Docker/Kubernetes 部署，但 UI、治理與平台運維要自行組裝 |
| Trino + Iceberg | 必須先做 connector POC；AtScale 的現行官方支援矩陣列出 Databricks、BigQuery、Snowflake、Redshift，未列 Trino 或 Iceberg | Cube 文件／官方資料源涵蓋 Presto 類引擎，但實際 Trino、Iceberg catalog、MinIO 與 pre-aggregation 目的地仍需在目標版本驗證 |

AtScale 官方 BI 文件顯示，部署 catalog 後可以取得 ODBC、JDBC、XMLA 與 SQL 連線資訊，並提供 Tableau、Excel、Power BI 等連線方式。[BI connection information](https://documentation.atscale.com/container/connect-integrate/connect-with-bi-tools/getting-cube-connection-information) Cube 的模型 reference 則把 access policy、pre-aggregation、refresh key 和 public visibility 放在 runtime model 內，適合由工程團隊控制查詢服務行為。[Cube cubes](https://docs.cube.dev/reference/data-modeling/cube)

## 需要重新調整的架構

### 方案 A：AtScale 作企業 Universal Layer

適用於組織最在意「一次定義、跨 BI/Excel/Python/AI 共用」、Universe 式建模、集中權限與企業查詢治理的情境。

```text
Airflow -> dbt-trino -> Iceberg -> Trino
                    \-> Git semantic contract -> AtScale SML/Design Center -> BI/Python/MCP
OpenMetadata <- published model, owner, glossary, lineage, quality
```

在此方案中，自建的模型 UI 應收斂成契約編輯、差異檢視、驗證、審核與發布控制；不要再複製一套完整 Universe Designer。AtScale 官方也提供 `sml-cli`，可由 Git repository 以 CI/CD 方式部署模型。[SML CLI](https://documentation.atscale.com/container/creating-and-sharing-cubes/working-with-models-programmatically/sml-cli)

方案 A 的硬性 gate 是：目標 AtScale 版本是否支援 Trino/Iceberg、是否允許完全內網運行、license validation/billing egress 是否可由內部代理或離線授權處理，以及 S3-compatible aggregate storage 是否被支援。官方 system requirements 也顯示這是一個重量級 Kubernetes 平台：POC 最低 16 CPU/64 GB RAM，production 建議三節點；支援頁同時列出 license 與 billing 的 egress URI。[System requirements](https://documentation.atscale.com/container/product-requirements/minimum-system-requirements) [Supported tools and platforms](https://documentation.atscale.com/container/product-requirements/supported-tools-and-platforms)

### 方案 B：Cube Core 作自管 embedded runtime

適用於開源、自行掌控部署、產品內嵌、租戶隔離、API-first 與 Trino/lakehouse 工程整合優先的情境。

```text
Airflow -> dbt-trino -> Iceberg -> Trino
                    \-> Git semantic contract -> Cube adapter -> Cube Core/Cube Store -> REST/SQL/GraphQL/embedded BI
OpenMetadata <- published model, owner, glossary, lineage, quality
```

Cube Core 官方 repository 明確說明它是 open-source semantic layer，可自架並供 BI、embedded analytics、AI agent 使用；Cube Store 在 production 需獨立部署，代表快取與預聚合是平台運維的一部分。[Cube Core](https://github.com/cube-js/cube) [Running Cube in production](https://github.com/cube-js/cube/blob/master/docs-mintlify/cube-core/running-in-production.mdx)

方案 B 的代價是需要自己補上 AtScale 已提供的產品面：圖形化 relationship editor、企業 BI connection UX、模型審核流程、catalog metadata sync、Python 語意 client、MCP/AI context、RBAC 與跨租戶治理。這些能力應由前述自建 semantic designer 和 CI/CD pipeline 提供。

### 方案 C：分層雙 runtime，但單一語意契約

若既需要企業 BI/AI 共用，也需要 SaaS embedded analytics，可以採用：

```text
                 Git business semantic contract
                    /                    \
             AtScale adapter          Cube adapter
                    |                    |
        enterprise BI/Python/AI     embedded REST/SQL/API
```

這不是兩份可獨立編輯的 metric model。metric、relationship、time semantics、security policy 與 golden tests 只在 Git contract 定義；AtScale SML 與 Cube model 是兩個 runtime binding。CI 必須對同一組案例執行結果比對，至少涵蓋 fanout、退款、日期邊界、半可加指標、row-level security 與租戶隔離。

## 對現有設計的具體修改

1. 將「選擇 Cube 或 AtScale」改成 decision gate：先以 `enterprise_semantic` 與 `embedded_runtime` 兩種消費場景拆分需求，再決定是否需要一個或兩個 runtime。
2. 把 `semantic_contract` 定為唯一權威；AtScale SML 與 Cube YAML 不直接由不同人維護公式。
3. 保留模型編輯 UI，但 UI 產生 domain-neutral contract；adapter 再產生 AtScale SML 或 Cube model。這樣 AtScale POC 失敗時，可回退 Cube 而不重做業務定義。
4. 在 POC checklist 加入「官方支援矩陣是否列出 Trino/Iceberg」與「是否有 license/billing egress」。不能將 AtScale 的 cloud-native/container 文件直接推論為 air-gapped 可行。
5. 對 Cube 只驗證真正需要的範圍：Trino query correctness、Iceberg snapshot/time travel、MinIO/S3 endpoint、Cube Store aggregate persistence、security context 與 BI connector，而不是先建造全套 AtScale 替代品。

## 建議選型門檻

- 若 AtScale 通過 connector、air-gap、授權與成本 gate：AtScale 作企業 Universal Layer；Cube 只保留給 embedded analytics 或特定低延遲 API。
- 若 AtScale 的 Trino/Iceberg 或離線授權 gate 失敗：Cube Core 作主要 runtime，自建 UI/治理層補足 Universe 類體驗。
- 若兩者均通過：維持單一契約與雙 adapter，並以 golden query 結果、延遲、成本、部署複雜度與故障回滾做量化決策。

