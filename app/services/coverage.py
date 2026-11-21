"""覆盖对账 / 抽检：死边不得算作活依赖，抽检进度可追。"""
from __future__ import annotations

import threading
import time

from app.db import get_db
from app.services import fates as fates_svc


def list_reports(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM coverage_reports ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def _live_dependency_sets(conn) -> dict:
    """按边的命运状态切分：活依赖、旁路凭证、影子、悬挂、非发布。

    关键修正：悬挂（挂在停用/下线节点上的已发布边）不再计入“已发布边”，
    覆盖对账和抽检都不会把死边当成活依赖。
    """
    statuses = fates_svc.node_status_map(conn)
    shadow_ids = fates_svc.shadow_edge_ids(conn)
    bypass_ids = fates_svc.bypass_edge_ids(conn)
    edges = conn.execute("SELECT * FROM lineage_edges").fetchall()
    buckets = {
        fates_svc.S_ACTIVE: [],
        fates_svc.S_BYPASS: [],
        fates_svc.S_SHADOW: [],
        fates_svc.S_DANGLING: [],
        fates_svc.S_INACTIVE: [],
    }
    for e in edges:
        state = fates_svc.edge_state(conn, e, statuses, shadow_ids, bypass_ids)
        buckets[state].append(dict(e))
    return buckets


def run_coverage(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        nodes = conn.execute("SELECT id, status FROM lineage_nodes").fetchall()
        buckets = _live_dependency_sets(conn)
        live_nodes = [n for n in nodes if n["status"] not in fates_svc.DEAD_NODE_STATUSES]
        live_edges = buckets[fates_svc.S_ACTIVE]
        bypass_edges = buckets[fates_svc.S_BYPASS]
        # 影子收纳边已移出活边表，从影子表单独计数
        shadow_count = int(
            conn.execute("SELECT COUNT(*) AS c FROM shadow_draft_edges").fetchone()["c"]
        )
        dangling_edges = buckets[fates_svc.S_DANGLING]
        drafts = buckets[fates_svc.S_INACTIVE]

        # 只有两端都在用的活边才算活依赖；旁路凭证仍挂在已下线节点上，
        # 虽影响可见但不得计作活依赖，单独列账。
        linked = set()
        for e in live_edges:
            linked.add(int(e["upstream_id"]))
            linked.add(int(e["downstream_id"]))
        orphan = sum(1 for n in live_nodes if int(n["id"]) not in linked)
        uncovered = len(drafts)
        excluded_dead = len(dangling_edges) + shadow_count + len(bypass_edges)

        summary = (
            f"在用节点 {len(live_nodes)}，活依赖边 {len(live_edges)}，"
            f"旁路凭证边 {len(bypass_edges)}，影子草稿边 {shadow_count}，"
            f"孤儿节点 {orphan}，草稿边 {uncovered}，已剔除死边 {excluded_dead}"
        )
        if orphan:
            summary += "；存在未挂边节点须人工核对"
        if dangling_edges:
            summary += f"；仍有 {len(dangling_edges)} 条悬挂边未落定命运"
        cur = conn.execute(
            "INSERT INTO coverage_reports(summary, node_count, edge_count, "
            "orphan_count, uncovered_edges, dangling_count, excluded_edges) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                summary,
                len(live_nodes),
                len(live_edges),
                orphan,
                uncovered,
                len(dangling_edges),
                excluded_dead,
            ),
        )
        return dict(
            conn.execute(
                "SELECT * FROM coverage_reports WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )


def latest_chip(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT orphan_count, dangling_count FROM coverage_reports "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "未对账"
        if int(row["dangling_count"]) > 0:
            return "有悬挂边"
        return "有缺口" if int(row["orphan_count"]) > 0 else "已齐套"


def _sampling_step(conn, run_id: int, pause: float = 0.0) -> None:
    """逐条抽检活边与节点，边查边提交进度（供前端轮询）。"""
    statuses = fates_svc.node_status_map(conn)
    shadow_ids = fates_svc.shadow_edge_ids(conn)
    bypass_ids = fates_svc.bypass_edge_ids(conn)
    nodes = conn.execute("SELECT id, status FROM lineage_nodes ORDER BY id").fetchall()
    edges = conn.execute("SELECT * FROM lineage_edges ORDER BY id").fetchall()

    live_nodes = 0
    orphan_candidates: set[int] = set()
    for n in nodes:
        if n["status"] not in fates_svc.DEAD_NODE_STATUSES:
            live_nodes += 1
            orphan_candidates.add(int(n["id"]))
    conn.execute(
        "UPDATE coverage_sampling_runs SET total=?, live_nodes=? WHERE id=?",
        (len(edges), live_nodes, run_id),
    )

    live_edges = 0
    dangling = 0
    excluded_dead = 0
    linked: set[int] = set()
    total = len(edges)
    for idx, e in enumerate(edges, start=1):
        state = fates_svc.edge_state(conn, e, statuses, shadow_ids, bypass_ids)
        if state == fates_svc.S_ACTIVE:
            live_edges += 1
            linked.add(int(e["upstream_id"]))
            linked.add(int(e["downstream_id"]))
        elif state == fates_svc.S_DANGLING:
            dangling += 1
            excluded_dead += 1
        elif state == fates_svc.S_SHADOW:
            excluded_dead += 1
        elif state == fates_svc.S_BYPASS:
            # 旁路凭证影响可见，但挂在已下线节点上，仍按死边剔除计数
            excluded_dead += 1
        conn.execute(
            "UPDATE coverage_sampling_runs SET done=?, live_edges=?, "
            "dangling_edges=?, excluded_dead=? WHERE id=?",
            (idx, live_edges, dangling, excluded_dead, run_id),
        )
        conn.commit()
        if pause:
            time.sleep(pause)
    # 影子草稿边已移出活边表，从影子表补计剔除死边
    shadow_extra = int(
        conn.execute("SELECT COUNT(*) AS c FROM shadow_draft_edges").fetchone()["c"]
    )
    excluded_dead += shadow_extra
    orphan = sum(1 for nid in orphan_candidates if nid not in linked)
    summary = (
        f"抽检 {total} 条已发布边：活依赖 {live_edges}，悬挂剔除 {dangling}，"
        f"影子收纳 {shadow_extra}，剔除死边 {excluded_dead}，孤儿节点 {orphan}"
    )
    conn.execute(
        "UPDATE coverage_sampling_runs SET status='完成', done=?, total=?, "
        "live_nodes=?, live_edges=?, dangling_edges=?, excluded_dead=?, "
        "orphan_count=?, message=?, finished_at=datetime('now') WHERE id=?",
        (
            total,
            total,
            live_nodes,
            live_edges,
            dangling,
            excluded_dead,
            orphan,
            summary,
            run_id,
        ),
    )
    conn.commit()


def _run_sampling_worker(run_id: int, db_path: str | None, pause: float) -> None:
    try:
        with get_db(db_path) as conn:
            _sampling_step(conn, run_id, pause=pause)
    except Exception as exc:  # noqa: BLE001
        try:
            with get_db(db_path) as conn:
                conn.execute(
                    "UPDATE coverage_sampling_runs SET status='失败', message=?, "
                    "finished_at=datetime('now') WHERE id=?",
                    (f"抽检失败：{exc}", run_id),
                )
        except Exception:  # noqa: BLE001
            pass


def run_sampling(db_path: str | None = None, background: bool = True) -> dict:
    """启动一次覆盖抽检。

    默认后台线程推进，前端轮询 /api/coverage/samplings/<id> 看进度；
    background=False 时同步跑完（测试用）。
    """
    with get_db(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO coverage_sampling_runs(status, total, done, message) "
            "VALUES ('抽样中',0,0,'开始抽检')"
        )
        run_id = int(cur.lastrowid)
    if background:
        # 小步停留，让进度条在界面上真实推进；数据量小也不至于瞬间结束。
        t = threading.Thread(
            target=_run_sampling_worker,
            args=(run_id, db_path, 0.06),
            daemon=True,
        )
        t.start()
        with get_db(db_path) as conn:
            return dict(
                conn.execute(
                    "SELECT * FROM coverage_sampling_runs WHERE id=?", (run_id,)
                ).fetchone()
            )
    with get_db(db_path) as conn:
        _sampling_step(conn, run_id)
        return dict(
            conn.execute(
                "SELECT * FROM coverage_sampling_runs WHERE id=?", (run_id,)
            ).fetchone()
        )


def list_sampling_runs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM coverage_sampling_runs ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_sampling_run(run_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM coverage_sampling_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not row:
            from app.services.errors import ServiceError

            raise ServiceError("抽检运行不存在", 404)
        return dict(row)
