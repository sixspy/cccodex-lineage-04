"""影响分析：从根节点沿活边向下展开；悬挂边按命运剔除或保留。"""
from __future__ import annotations

import sqlite3

from app.db import get_db
from app.services import fates as fates_svc
from app.services.errors import ServiceError


def list_jobs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id ORDER BY j.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def job_detail(job_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        job = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        if not job:
            raise ServiceError("作业不存在", 404)
        hits = conn.execute(
            "SELECT h.*, n.node_code, n.table_name, n.layer, n.status AS node_status "
            "FROM impact_hits h "
            "JOIN lineage_nodes n ON n.id=h.node_id WHERE h.job_id=? ORDER BY h.hop, h.id",
            (job_id,),
        ).fetchall()
        excluded = conn.execute(
            "SELECT * FROM impact_excluded_edges WHERE job_id=? ORDER BY id",
            (job_id,),
        ).fetchall()
        item = dict(job)
        item["dangling_excluded"] = bool(item.get("dangling_excluded"))
        return {"job": item, "hits": [dict(x) for x in hits], "excluded": [dict(x) for x in excluded]}


def _record_excluded(
    conn: sqlite3.Connection,
    job_id: int,
    edge_code: str,
    upstream_code: str,
    downstream_code: str,
    fate: str,
    reason: str,
) -> None:
    conn.execute(
        "INSERT INTO impact_excluded_edges(job_id, edge_code, upstream_code, "
        "downstream_code, fate, reason) VALUES (?,?,?,?,?,?)",
        (job_id, edge_code, upstream_code, downstream_code, fate, reason),
    )


def start_impact(root_node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        root = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (root_node_id,)
        ).fetchone()
        if not root:
            raise ServiceError("根节点不存在", 404)
        if root["status"] in fates_svc.DEAD_NODE_STATUSES:
            raise ServiceError("停用/下线节点不可开影响分析", 409)
        running = conn.execute(
            "SELECT id FROM impact_jobs WHERE root_node_id=? AND status IN ('待执行','执行中')",
            (root_node_id,),
        ).fetchone()
        if running:
            raise ServiceError("该根节点已有在途影响作业", 409)
        seq = conn.execute("SELECT COUNT(*) AS c FROM impact_jobs").fetchone()["c"] + 1
        job_code = f"IMP-{seq:04d}"
        cur = conn.execute(
            "INSERT INTO impact_jobs(job_code, root_node_id, status, message, started_at) "
            "VALUES (?,?,'执行中','展开中',datetime('now'))",
            (job_code, root_node_id),
        )
        job_id = int(cur.lastrowid)

        statuses = fates_svc.node_status_map(conn)
        shadow_ids = fates_svc.shadow_edge_ids(conn)
        bypass_ids = fates_svc.bypass_edge_ids(conn)
        bypass_fate = fates_svc.bypass_fate_by_edge(conn)

        seen = {root_node_id: 0}
        queue = [root_node_id]
        bypass_traversed = 0
        excluded_dangling = 0
        touched_fates: set[str] = set()
        while queue:
            cur_id = queue.pop(0)
            hop = seen[cur_id]
            conn.execute(
                "INSERT OR IGNORE INTO impact_hits(job_id, node_id, hop) VALUES (?,?,?)",
                (job_id, cur_id, hop),
            )
            outgoing = conn.execute(
                "SELECT e.*, u.node_code AS up_code, d.node_code AS down_code "
                "FROM lineage_edges e "
                "JOIN lineage_nodes u ON u.id=e.upstream_id "
                "JOIN lineage_nodes d ON d.id=e.downstream_id "
                "WHERE e.upstream_id=? AND e.status='已发布'",
                (cur_id,),
            ).fetchall()
            for edge in outgoing:
                state = fates_svc.edge_state(
                    conn, edge, statuses, shadow_ids, bypass_ids
                )
                if state == fates_svc.S_SHADOW:
                    # 防御性：影子收纳边已移出活边表，正常不会走到这里。
                    excluded_dangling += 1
                    touched_fates.add("影子草稿")
                    _record_excluded(
                        conn,
                        job_id,
                        edge["edge_code"],
                        edge["up_code"],
                        edge["down_code"],
                        "影子草稿",
                        "影子草稿收纳，影响分析禁止读取",
                    )
                    continue
                if state == fates_svc.S_DANGLING:
                    # 未落定命运的悬挂边：不得当成活依赖，一律剔除。
                    excluded_dangling += 1
                    _record_excluded(
                        conn,
                        job_id,
                        edge["edge_code"],
                        edge["up_code"],
                        edge["down_code"],
                        "未落定",
                        "悬挂边无命运落定，影响分析剔除",
                    )
                    continue
                if state == fates_svc.S_BYPASS:
                    bypass_traversed += 1
                    fate = bypass_fate.get(int(edge["id"]), "旁路账册")
                    touched_fates.add(fate)
                kid_id = int(edge["downstream_id"])
                if kid_id not in seen:
                    seen[kid_id] = hop + 1
                    queue.append(kid_id)

        affected = len(seen)
        # 影子草稿收纳的悬挂边已不在活边表；若其上游在本次命中范围内，
        # 仍要在详情里亮出“已剔除”，让两名值班可核对剔除结果。
        shadow_rows = conn.execute(
            "SELECT s.*, n.node_code AS retired_code FROM shadow_draft_edges s "
            "JOIN retire_fate_runs r ON r.id=s.retire_id "
            "JOIN lineage_nodes n ON n.id=r.node_id"
        ).fetchall()
        node_codes = {
            int(r["id"]): r["node_code"]
            for r in conn.execute("SELECT id, node_code FROM lineage_nodes").fetchall()
        }
        for s in shadow_rows:
            if int(s["upstream_id"]) in seen:
                excluded_dangling += 1
                touched_fates.add("影子草稿")
                _record_excluded(
                    conn,
                    job_id,
                    s["edge_code"],
                    node_codes.get(int(s["upstream_id"]), ""),
                    node_codes.get(int(s["downstream_id"]), ""),
                    "影子草稿",
                    "影子草稿收纳，影响分析禁止读取",
                )
        fate_label = "、".join(sorted(touched_fates))
        message = f"影响到 {affected} 个节点"
        if excluded_dangling:
            message += f"，剔除 {excluded_dangling} 条悬挂边"
        if bypass_traversed:
            message += f"，保留 {bypass_traversed} 条旁路凭证边"
        conn.execute(
            "UPDATE impact_jobs SET status='成功', affected_count=?, "
            "dangling_excluded=?, excluded_count=?, bypass_count=?, fate=?, "
            "message=?, finished_at=datetime('now') WHERE id=?",
            (
                affected,
                1 if excluded_dangling else 0,
                excluded_dangling,
                bypass_traversed,
                fate_label,
                message,
                job_id,
            ),
        )
        return dict(
            conn.execute(
                "SELECT * FROM impact_jobs WHERE id=?", (job_id,)
            ).fetchone()
        )
