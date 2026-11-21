"""清洗入库：目录指纹与悬挂边处理不一致就拦下解析结果。"""
from __future__ import annotations

from app.db import get_db
from app.services import fates as fates_svc
from app.services.errors import ServiceError


def current_fingerprint(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        return fates_svc.catalog_fingerprint(conn)


def list_ingest_logs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ingest_logs ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def run_ingest(
    fingerprint: str,
    fingerprint_node_count: int | None = None,
    fingerprint_edge_count: int | None = None,
    db_path: str | None = None,
) -> dict:
    """按解析结果携带的目录指纹入库。

    若解析时看到的指纹与当下目录指纹不一致（例如期间有节点下线、悬挂边
    被收纳或转旁路），说明解析结果已陈旧，拒绝入库并说明原因。
    """
    fingerprint = (fingerprint or "").strip()
    if not fingerprint:
        raise ServiceError("清洗入库须携带目录指纹")
    with get_db(db_path) as conn:
        current = fates_svc.catalog_fingerprint(conn)
        if fingerprint != current["fingerprint"]:
            msg = (
                "目录指纹与悬挂边处理不一致，已拦下本次解析入库："
                "解析指纹 "
                f"{fingerprint[:12]}…，当前指纹 {current['fingerprint'][:12]}…。"
                "请重新解析后再入库。"
            )
            conn.execute(
                "INSERT INTO ingest_logs(fingerprint, requested, ingested, blocked, message) "
                "VALUES (?,0,0,1,?)",
                (fingerprint, msg),
            )
            raise ServiceError(msg, 409)
        # 指纹一致：登记一次成功入库（本台只做校验与留痕）
        msg = (
            f"指纹一致，解析入库通过：节点 {current['node_count']}，"
            f"已发布边 {current['edge_count']}，影子 {current['shadow_count']}，"
            f"旁路 {current['bypass_count']}"
        )
        cur = conn.execute(
            "INSERT INTO ingest_logs(fingerprint, requested, ingested, blocked, message) "
            "VALUES (?,1,1,0,?)",
            (fingerprint, msg),
        )
        return dict(
            conn.execute(
                "SELECT * FROM ingest_logs WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )
