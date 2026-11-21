"""下线命运：悬挂边只许落一种命运，并提供目录指纹与死边判定。

两种命运互斥，且不得同开：
- 影子草稿：把悬挂边收纳进影子草稿表（登记指纹），影响分析禁止读取；
- 旁路账册：把悬挂边指纹写入独立凭证行供日后核齐，影响分析仍可读取。

同一节点只能有一条命运行，同一条边只能落在一种命运里（见 schema 的
UNIQUE(orig_edge_id) 与 nodes.retire_node 的已落定校验）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

FATES = ("影子草稿", "旁路账册")
LIVE_NODE_STATUSES = ("登记", "在用")
DEAD_NODE_STATUSES = ("停用", "下线")

# 边状态语义
S_ACTIVE = "活"        # 两端节点都在用，参与影响
S_BYPASS = "旁路"      # 悬挂边的旁路账册命运，影响仍可见
S_SHADOW = "影子"      # 悬挂边的影子草稿命运，影响禁止读取
S_DANGLING = "悬挂"    # 挂在停用/下线节点、尚无命运落定，影响剔除
S_INACTIVE = "非发布"  # 草稿/作废


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def edge_fingerprint(
    edge_code: str,
    upstream_code: str,
    downstream_code: str,
    transform_kind: str,
) -> str:
    raw = f"{edge_code}|{upstream_code}|{downstream_code}|{transform_kind}"
    return _sha256(raw)


def retire_run_fingerprint(
    node_code: str, fate: str, edge_fingerprints: list[str]
) -> str:
    raw = "|".join([node_code, fate, *sorted(edge_fingerprints)])
    return _sha256(raw)


def node_status_map(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT id, status FROM lineage_nodes").fetchall()
    return {int(r["id"]): str(r["status"]) for r in rows}


def shadow_edge_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT orig_edge_id FROM shadow_draft_edges").fetchall()
    return {int(r["orig_edge_id"]) for r in rows if r["orig_edge_id"] is not None}


def bypass_edge_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT orig_edge_id FROM bypass_ledger").fetchall()
    return {int(r["orig_edge_id"]) for r in rows if r["orig_edge_id"] is not None}


def shadow_fate_by_edge(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(r["orig_edge_id"]): str(r["fate"])
        for r in conn.execute(
            "SELECT b.orig_edge_id, r.fate FROM shadow_draft_edges b "
            "JOIN retire_fate_runs r ON r.id=b.retire_id"
        ).fetchall()
        if r["orig_edge_id"] is not None
    }


def bypass_fate_by_edge(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(r["orig_edge_id"]): str(r["fate"])
        for r in conn.execute(
            "SELECT b.orig_edge_id, r.fate FROM bypass_ledger b "
            "JOIN retire_fate_runs r ON r.id=b.retire_id"
        ).fetchall()
        if r["orig_edge_id"] is not None
    }


def edge_state(
    conn: sqlite3.Connection,
    edge,
    statuses: dict[int, str] | None = None,
    shadow_ids: set[int] | None = None,
    bypass_ids: set[int] | None = None,
) -> str:
    """返回 S_ACTIVE/S_BYPASS/S_SHADOW/S_DANGLING/S_INACTIVE 之一。"""
    if edge["status"] != "已发布":
        return S_INACTIVE
    edge_id = int(edge["id"])
    bypass_ids = bypass_ids if bypass_ids is not None else bypass_edge_ids(conn)
    if edge_id in bypass_ids:
        return S_BYPASS
    # 影子草稿边已移出版边表，正常不会出现在这里；防御性保留判定。
    shadow_ids = shadow_ids if shadow_ids is not None else shadow_edge_ids(conn)
    if edge_id in shadow_ids:
        return S_SHADOW
    statuses = statuses if statuses is not None else node_status_map(conn)
    up = statuses.get(int(edge["upstream_id"]))
    down = statuses.get(int(edge["downstream_id"]))
    if up in DEAD_NODE_STATUSES or down in DEAD_NODE_STATUSES:
        return S_DANGLING
    return S_ACTIVE


def impact_visible(state: str) -> bool:
    return state in (S_ACTIVE, S_BYPASS)


def catalog_fingerprint(conn: sqlite3.Connection) -> dict:
    """目录指纹 = 节点 + 已发布边 + 影子草稿 + 旁路账册 + 命运行。

    任一悬挂边处理被改动，指纹都会变，清洗入库据此拦住陈旧解析结果。
    """
    nodes = [
        {"code": r["node_code"], "status": r["status"], "layer": r["layer"]}
        for r in conn.execute(
            "SELECT node_code, status, layer FROM lineage_nodes ORDER BY node_code"
        ).fetchall()
    ]
    edges = [
        {
            "code": r["edge_code"],
            "up": r["up_code"],
            "down": r["down_code"],
            "status": r["status"],
        }
        for r in conn.execute(
            "SELECT e.edge_code, u.node_code AS up_code, d.node_code AS down_code, e.status "
            "FROM lineage_edges e "
            "JOIN lineage_nodes u ON u.id=e.upstream_id "
            "JOIN lineage_nodes d ON d.id=e.downstream_id "
            "ORDER BY e.edge_code"
        ).fetchall()
    ]
    shadow = [
        {"code": r["edge_code"], "fp": r["fingerprint"]}
        for r in conn.execute(
            "SELECT edge_code, fingerprint FROM shadow_draft_edges ORDER BY edge_code"
        ).fetchall()
    ]
    bypass = [
        {"code": r["edge_code"], "fp": r["fingerprint"]}
        for r in conn.execute(
            "SELECT edge_code, fingerprint FROM bypass_ledger ORDER BY edge_code"
        ).fetchall()
    ]
    fates = [
        {"node": r["node_code"], "fate": r["fate"], "n": int(r["dangling_count"])}
        for r in conn.execute(
            "SELECT n.node_code, r.fate, r.dangling_count FROM retire_fate_runs r "
            "JOIN lineage_nodes n ON n.id=r.node_id ORDER BY n.node_code"
        ).fetchall()
    ]
    payload = {
        "v": 1,
        "nodes": nodes,
        "edges": edges,
        "shadow": shadow,
        "bypass": bypass,
        "fates": fates,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "fingerprint": _sha256(raw),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "shadow_count": len(shadow),
        "bypass_count": len(bypass),
        "fate_count": len(fates),
    }


def fate_run_for_node(conn: sqlite3.Connection, node_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM retire_fate_runs WHERE node_id=?", (node_id,)
    ).fetchone()
    return dict(row) if row else None


def list_fate_runs(conn: sqlite3.Connection) -> list[dict]:
    """命运行台账，供核芯片与影响剔除结果对账。"""
    rows = conn.execute(
        "SELECT r.*, n.node_code, n.table_name, n.status AS node_status "
        "FROM retire_fate_runs r JOIN lineage_nodes n ON n.id=r.node_id "
        "ORDER BY r.id DESC"
    ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["edges"] = [
            dict(x)
            for x in conn.execute(
                "SELECT edge_code, fingerprint, note FROM shadow_draft_edges "
                "WHERE retire_id=? ORDER BY edge_code",
                (r["id"],),
            ).fetchall()
        ] or [
            dict(x)
            for x in conn.execute(
                "SELECT edge_code, fingerprint, note FROM bypass_ledger "
                "WHERE retire_id=? ORDER BY edge_code",
                (r["id"],),
            ).fetchall()
        ]
        items.append(item)
    return items


def fate_summary(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT fate, COUNT(*) AS c, SUM(dangling_count) AS n "
        "FROM retire_fate_runs GROUP BY fate"
    ).fetchall()
    by_fate = {str(r["fate"]): int(r["c"]) for r in rows}
    dangling = sum(int(r["n"] or 0) for r in rows)
    return {
        "total": sum(by_fate.values()),
        "shadow": by_fate.get("影子草稿", 0),
        "bypass": by_fate.get("旁路账册", 0),
        "dangling": dangling,
        "chip": (
            "无下线节点"
            if not by_fate
            else f"下线节点 {sum(by_fate.values())} · 悬挂边 {dangling}"
        ),
    }
