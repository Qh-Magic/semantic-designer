"""End-to-end Query AST -> StarRocks integration test.

The database is the official StarRocks all-in-one Docker image. The test uses
the image's MySQL-compatible client so the host needs only Python and Docker.
"""

import json
import subprocess
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[2]
CONTAINER = "semantic-designer-starrocks"


def sql(sql_text: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "mysql",
            "-h127.0.0.1",
            "-P9030",
            "-uroot",
            "--batch",
            "--skip-column-names",
            "-e",
            sql_text,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def quote(value):
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def materialize(compiled):
    query = compiled["sql"]
    for key, value in compiled["parameters"].items():
        query = query.replace(f":{key}", quote(value))
    return query


def main():
    sql("CREATE DATABASE IF NOT EXISTS mart")
    sql("DROP TABLE IF EXISTS mart.dim_customers")
    sql("DROP TABLE IF EXISTS mart.fct_orders")
    sql(
        "CREATE TABLE mart.dim_customers (customer_id INT, region VARCHAR(20)) "
        "ENGINE=OLAP DISTRIBUTED BY HASH(customer_id) BUCKETS 1 "
        "PROPERTIES ('replication_num' = '1')"
    )
    sql(
        "CREATE TABLE mart.fct_orders (order_id INT, customer_id INT, "
        "net_amount DECIMAL(12,2), order_date DATE) ENGINE=OLAP "
        "DISTRIBUTED BY HASH(order_id) BUCKETS 1 "
        "PROPERTIES ('replication_num' = '1')"
    )
    sql("INSERT INTO mart.dim_customers VALUES (1, 'TW'), (2, 'JP')")
    sql(
        "INSERT INTO mart.fct_orders VALUES "
        "(101, 1, 100.00, '2026-01-05'), (102, 1, 50.00, '2026-01-12'), "
        "(103, 2, 80.00, '2026-01-20')"
    )

    model = server.read_model(ROOT / "model.yaml")
    request = {
        "model": {"name": "sales", "release": "v1"},
        "principal": {"subject": "integration-test", "authn": "internal"},
        "select": [
            {"kind": "dimension", "name": "customers.region"},
            {"kind": "measure", "name": "net_sales"},
        ],
        "time": {
            "dimension": "orders.order_date",
            "from": "2026-01-01",
            "to": "2026-02-01",
            "bounds": "inclusive_exclusive",
        },
        "limit": 10,
    }
    compiled = server.compile_query(request, model)
    result = sql(materialize(compiled))
    rows = [line.split("\t") for line in result.splitlines() if line]
    assert sorted(rows) == [["JP", "80.00"], ["TW", "150.00"]], rows
    print(json.dumps({"sql": compiled["sql"], "rows": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
