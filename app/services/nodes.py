"""血缘节点：登记、列表、下线命运落定。"""
from __future__ import annotations

import sqlite3

from app.db import get_db, immediate
from app.services import fates as fates_svc
from app.services.errors import ServiceError


def _node_with_fate(conn: sqlite3.Connection, node_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
    ).fetchone()
    if not row:
        raise ServiceError("节点不存在", 404)
    item = dict(row)
    run = fates_svc.fate_run_for_node(conn, node_id)
    item["fate"] = run["fate"] if run else ""
    item["fate_run_id"] = run["id"] if run else None
    item["fate_dangling_count"] = run["dangling_count"] if run else 0
    item["fate_fingerprint"] = run["fingerprint"] if run else ""
    item["shadow_count"] = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM shadow_draft_edges WHERE retire_id=?",
            (run["id"],),
        ).fetchone()["c"]
        if run
        else 0
    )
    item["bypass_count"] = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM bypass_ledger WHERE retire_id=?",
            (run["id"],),
        ).fetchone()["c"]
        if run
        else 0
    )
    return item


def list_nodes(layer: str | None = None, db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        if layer:
            rows = conn.execute(
                "SELECT * FROM lineage_nodes WHERE layer=? ORDER BY id",
                (layer,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lineage_nodes ORDER BY id"
            ).fetchall()
        items = [dict(r) for r in rows]
        fates = {
            int(r["node_id"]): dict(r)
            for r in conn.execute("SELECT * FROM retire_fate_runs").fetchall()
        }
        for it in items:
            up = conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges "
                "WHERE downstream_id=? AND status='已发布'",
                (it["id"],),
            ).fetchone()["c"]
            down = conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges "
                "WHERE upstream_id=? AND status='已发布'",
                (it["id"],),
            ).fetchone()["c"]
            it["upstream_count"] = int(up)
            it["downstream_count"] = int(down)
            run = fates.get(int(it["id"]))
            it["fate"] = run["fate"] if run else ""
            it["fate_run_id"] = run["id"] if run else None
            it["fate_dangling_count"] = run["dangling_count"] if run else 0
            if run:
                it["shadow_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) AS c FROM shadow_draft_edges WHERE retire_id=?",
                        (run["id"],),
                    ).fetchone()["c"]
                )
                it["bypass_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) AS c FROM bypass_ledger WHERE retire_id=?",
                        (run["id"],),
                    ).fetchone()["c"]
                )
            else:
                it["shadow_count"] = 0
                it["bypass_count"] = 0
        return items


def get_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        return _node_with_fate(conn, node_id)


def register_node(
    node_code: str,
    table_name: str,
    layer: str,
    owner_team: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    node_code = (node_code or "").strip()
    table_name = (table_name or "").strip()
    layer = (layer or "").strip().upper()
    if not node_code or not table_name:
        raise ServiceError("节点编号与表名必填")
    if layer not in ("ODS", "DWD", "DWS", "ADS"):
        raise ServiceError("层级只许 ODS/DWD/DWS/ADS")
    with get_db(db_path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO lineage_nodes(node_code, table_name, layer, owner_team, status, note) "
                "VALUES (?,?,?,?,'登记',?)",
                (node_code, table_name, layer, owner_team or "", note or ""),
            )
        except Exception as exc:
            raise ServiceError(f"登记失败：编号可能重复（{exc}）") from exc
        return _node_with_fate(conn, int(cur.lastrowid))


def activate_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise ServiceError("节点不存在", 404)
        if row["status"] == "停用":
            raise ServiceError("已停用节点不可直接启用", 409)
        if row["status"] == "下线":
            raise ServiceError(
                "已下线节点命运已落定，不可直接启用；如需恢复请重新登记节点", 409
            )
        conn.execute(
            "UPDATE lineage_nodes SET status='在用' WHERE id=?", (node_id,)
        )
        return _node_with_fate(conn, node_id)


def dangling_published_edges(
    conn: sqlite3.Connection, node_id: int
) -> list[dict]:
    """该节点名下仍处于已发布、且尚未落定命运的悬挂边，含上下游编号。

    已进影子草稿或旁路账册的边不再返回：同一条边只许一种命运，
    避免另一端点后下线时把它二次落成另一套命运（禁止两套同开）。
    """
    settled = fates_svc.shadow_edge_ids(conn) | fates_svc.bypass_edge_ids(conn)
    rows = conn.execute(
        "SELECT e.*, u.node_code AS up_code, d.node_code AS down_code "
        "FROM lineage_edges e "
        "JOIN lineage_nodes u ON u.id=e.upstream_id "
        "JOIN lineage_nodes d ON d.id=e.downstream_id "
        "WHERE e.status='已发布' AND (e.upstream_id=? OR e.downstream_id=?) "
        "ORDER BY e.id",
        (node_id, node_id),
    ).fetchall()
    return [dict(r) for r in rows if int(r["id"]) not in settled]


def retire_node(
    node_id: int,
    fate: str = "",
    db_path: str | None = None,
) -> dict:
    """节点下线并原子落定所选命运。

    整个落定在一个 BEGIN IMMEDIATE 事务里完成：标记下线、搬边或记账
    （二选一）、写命运行。并发点同一节点时后到者会读到已落定的命运行，
    直接拒绝，绝不覆盖前一名值班写入的命运。
    """
    fate = (fate or "").strip()
    if fate not in fates_svc.FATES:
        raise ServiceError(
            "下线须先选一种命运：影子草稿（影响页剔除）或旁路账册（影响页仍可见）"
        )
    try:
        with immediate(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
            ).fetchone()
            if not row:
                raise ServiceError("节点不存在", 404)
            existing = fates_svc.fate_run_for_node(conn, node_id)
            if existing:
                raise ServiceError(
                    f"该节点已下线，命运已落定为「{existing['fate']}」，不可重复改写", 409
                )
            if row["status"] == "下线":
                raise ServiceError(
                    "该节点已下线，但库内没有命运行；请核对目录后重新登记", 409
                )
            edges = dangling_published_edges(conn, node_id)
            edge_fps = [
                fates_svc.edge_fingerprint(
                    e["edge_code"], e["up_code"], e["down_code"], e["transform_kind"]
                )
                for e in edges
            ]
            run_fp = fates_svc.retire_run_fingerprint(
                row["node_code"], fate, edge_fps
            )
            detail = "、".join(e["edge_code"] for e in edges) or "无悬挂边"
            cur = conn.execute(
                "INSERT INTO retire_fate_runs(node_id, fate, dangling_count, fingerprint, detail) "
                "VALUES (?,?,?,?,?)",
                (node_id, fate, len(edges), run_fp, detail),
            )
            retire_id = int(cur.lastrowid)
            for e, fp in zip(edges, edge_fps):
                if fate == "影子草稿":
                    # 影子草稿命运：悬挂边被影子草稿表收纳，并从活边表移出，
                    # 影响读取从此看不到；命运行保留全量指纹供核对。
                    conn.execute(
                        "INSERT INTO shadow_draft_edges(retire_id, orig_edge_id, edge_code, "
                        "upstream_id, downstream_id, transform_kind, fingerprint, note) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            retire_id,
                            int(e["id"]),
                            e["edge_code"],
                            int(e["upstream_id"]),
                            int(e["downstream_id"]),
                            e["transform_kind"],
                            fp,
                            f"随 {row['node_code']} 下线收纳",
                        ),
                    )
                    conn.execute("DELETE FROM lineage_edges WHERE id=?", (e["id"],))
                else:
                    # 旁路账册：边仍留在活边表供影响读取，指纹另立凭证行。
                    conn.execute(
                        "INSERT INTO bypass_ledger(retire_id, orig_edge_id, edge_code, "
                        "fingerprint, upstream_code, downstream_code, note) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            retire_id,
                            int(e["id"]),
                            e["edge_code"],
                            fp,
                            e["up_code"],
                            e["down_code"],
                            f"随 {row['node_code']} 下线转旁路凭证",
                        ),
                    )
            conn.execute(
                "UPDATE lineage_nodes SET status='下线' WHERE id=?", (node_id,)
            )
            item = _node_with_fate(conn, node_id)
            item["settled_edges"] = [
                {
                    "edge_code": e["edge_code"],
                    "upstream_code": e["up_code"],
                    "downstream_code": e["down_code"],
                    "fingerprint": fp,
                }
                for e, fp in zip(edges, edge_fps)
            ]
            item["message"] = (
                f"节点 {row['node_code']} 已下线，{len(edges)} 条悬挂边按「{fate}」落定"
                if edges
                else f"节点 {row['node_code']} 已下线，无悬挂边，命运已记为「{fate}」"
            )
            return item
    except sqlite3.IntegrityError as exc:
        # 唯一约束兜底：并发下已有一条命运行抢先写入。
        raise ServiceError(
            "另一名值班已抢先落定该节点命运，本次未写入；请刷新查看已落定的命运", 409
        ) from exc
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise ServiceError(
                "另一名值班正在落定该节点命运，请稍后刷新重试", 409
            ) from exc
        raise ServiceError(f"下线落定失败，已回滚：{exc}") from exc
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(f"下线落定失败，事务已回滚：{exc}") from exc
