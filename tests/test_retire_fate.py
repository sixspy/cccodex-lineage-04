"""下线命运、悬挂边剔除、覆盖抽检、指纹入库、并发落定。"""
import threading

import pytest

from app.db import get_db
from app.services import coverage, edges, fates, impact, ingest, nodes
from app.services.errors import ServiceError


def _node(db_path, code):
    return next(n for n in nodes.list_nodes(db_path=db_path) if n["node_code"] == code)


def _balance_alert(db_path):
    """把告警节点标成下线、旁路命运，制造 E-06/E-07 悬挂边。"""
    alert = _node(db_path, "N-ADS-ALERT")
    return nodes.retire_node(int(alert["id"]), "旁路账册", db_path=db_path)


def test_retire_requires_fate(db_path):
    n = _node(db_path, "N-ADS-ALERT")
    with pytest.raises(ServiceError) as exc:
        nodes.retire_node(int(n["id"]), "", db_path=db_path)
    assert "命运" in exc.value.message


def test_shadow_fate_hides_edges_from_impact(db_path):
    alert = _node(db_path, "N-ADS-ALERT")
    res = nodes.retire_node(int(alert["id"]), "影子草稿", db_path=db_path)
    assert res["fate"] == "影子草稿"
    assert res["status"] == "下线"
    assert res["fate_dangling_count"] >= 1
    shadow = edges.list_shadow_edges(db_path=db_path)
    assert any(s["edge_code"] == "E-06" for s in shadow)
    # 影子收纳边已移出活边表，边关系页不再显示为活边
    live_codes = {e["edge_code"] for e in edges.list_edges(db_path=db_path)}
    assert "E-06" not in live_codes
    # 影响分析从 DWS 根展开，影子边被剔除且记入剔除清单
    rev = _node(db_path, "N-DWS-REV")
    job = impact.start_impact(int(rev["id"]), db_path=db_path)
    assert job["dangling_excluded"] == 1
    detail = impact.job_detail(int(job["id"]), db_path=db_path)
    codes = {x["edge_code"] for x in detail["excluded"]}
    assert "E-06" in codes
    hit_codes = {h["node_code"] for h in detail["hits"]}
    assert "N-ADS-ALERT" not in hit_codes


def test_bypass_fate_keeps_edges_visible_in_impact(db_path):
    res = _balance_alert(db_path)
    assert res["fate"] == "旁路账册"
    assert res["bypass_count"] >= 1
    bypass = edges.list_bypass_edges(db_path=db_path)
    assert any(b["edge_code"] == "E-06" for b in bypass)
    rev = _node(db_path, "N-DWS-REV")
    job = impact.start_impact(int(rev["id"]), db_path=db_path)
    detail = impact.job_detail(int(job["id"]), db_path=db_path)
    hit_codes = {h["node_code"] for h in detail["hits"]}
    assert "N-ADS-ALERT" in hit_codes
    assert job["bypass_count"] >= 1


def test_only_one_fate_per_node(db_path):
    alert = _node(db_path, "N-ADS-ALERT")
    nodes.retire_node(int(alert["id"]), "影子草稿", db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        nodes.retire_node(int(alert["id"]), "旁路账册", db_path=db_path)
    assert exc.value.status_code == 409
    with get_db(db_path) as conn:
        runs = conn.execute(
            "SELECT COUNT(*) AS c FROM retire_fate_runs WHERE node_id=?", (alert["id"],)
        ).fetchone()["c"]
        assert runs == 1
        shadow = conn.execute("SELECT COUNT(*) AS c FROM shadow_draft_edges").fetchone()["c"]
        bypass = conn.execute("SELECT COUNT(*) AS c FROM bypass_ledger").fetchone()["c"]
        assert shadow >= 1 and bypass == 0


def test_coverage_does_not_count_dead_edges_as_live(db_path):
    # 先制造悬挂：直接停用一个节点而不落命运（用底层 SQL 模拟历史脏数据）
    alert = _node(db_path, "N-ADS-ALERT")
    with get_db(db_path) as conn:
        conn.execute("UPDATE lineage_nodes SET status='停用' WHERE id=?", (alert["id"],))
    rep = coverage.run_coverage(db_path=db_path)
    # E-06/E-07 悬挂，不得计入活依赖边
    assert rep["dangling_count"] >= 2
    assert "悬挂" in rep["summary"] or rep["dangling_count"] >= 2
    # 活依赖边必须少于“已发布边总数”（死边被剔除）
    with get_db(db_path) as conn:
        published = conn.execute(
            "SELECT COUNT(*) AS c FROM lineage_edges WHERE status='已发布'"
        ).fetchone()["c"]
    assert rep["edge_count"] < published
    run = coverage.run_sampling(db_path=db_path, background=False)
    assert run["status"] == "完成"
    assert run["dangling_edges"] >= 2
    assert run["live_edges"] <= rep["edge_count"]
    assert run["done"] == run["total"]


def test_ingest_blocked_when_fingerprint_stale(db_path):
    fp_before = ingest.current_fingerprint(db_path=db_path)
    alert = _node(db_path, "N-ADS-ALERT")
    nodes.retire_node(int(alert["id"]), "影子草稿", db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        ingest.run_ingest(fp_before["fingerprint"], db_path=db_path)
    assert exc.value.status_code == 409
    assert "指纹" in exc.value.message
    # 用当前指纹则通过
    fp_now = ingest.current_fingerprint(db_path=db_path)
    log = ingest.run_ingest(fp_now["fingerprint"], db_path=db_path)
    assert log["ingested"] == 1 and log["blocked"] == 0


def test_concurrent_retire_single_fate(db_path):
    alert = _node(db_path, "N-ADS-ALERT")
    results = []
    errors = []

    def worker(fate):
        try:
            results.append(nodes.retire_node(int(alert["id"]), fate, db_path=db_path))
        except ServiceError as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("影子草稿",)),
        threading.Thread(target=worker, args=("旁路账册",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 恰好一个成功、一个 409
    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 409
    with get_db(db_path) as conn:
        runs = conn.execute("SELECT * FROM retire_fate_runs").fetchall()
        assert len(runs) == 1
        shadow = conn.execute("SELECT COUNT(*) AS c FROM shadow_draft_edges").fetchone()["c"]
        bypass = conn.execute("SELECT COUNT(*) AS c FROM bypass_ledger").fetchone()["c"]
        # 不得两套同开
        assert (shadow > 0) != (bypass > 0)


def test_fingerprint_consistent_after_settlement(db_path):
    alert = _node(db_path, "N-ADS-ALERT")
    before = ingest.current_fingerprint(db_path=db_path)["fingerprint"]
    nodes.retire_node(int(alert["id"]), "旁路账册", db_path=db_path)
    after = ingest.current_fingerprint(db_path=db_path)["fingerprint"]
    assert before != after


def test_shared_edge_not_settled_by_two_fates(db_path):
    """两端节点先后下线：同一条边只许一种命运，不得两套同开。"""
    dash = _node(db_path, "N-ADS-DASH")
    alert = _node(db_path, "N-ADS-ALERT")
    # DASH 先下线，E-05/E-07 进影子草稿
    first = nodes.retire_node(int(dash["id"]), "影子草稿", db_path=db_path)
    assert first["shadow_count"] >= 2
    # ALERT 后下线走旁路：E-07 已被影子收纳，只应落 E-06
    second = nodes.retire_node(int(alert["id"]), "旁路账册", db_path=db_path)
    assert second["bypass_count"] == 1
    with get_db(db_path) as conn:
        shadow_codes = {
            r["edge_code"]
            for r in conn.execute("SELECT edge_code FROM shadow_draft_edges").fetchall()
        }
        bypass_codes = {
            r["edge_code"]
            for r in conn.execute("SELECT edge_code FROM bypass_ledger").fetchall()
        }
    assert "E-07" in shadow_codes
    assert "E-07" not in bypass_codes
    assert not (shadow_codes & bypass_codes)
