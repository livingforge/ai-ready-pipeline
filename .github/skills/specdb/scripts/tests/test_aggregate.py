# -*- coding: utf-8 -*-
"""横断集計 aggregate.py — 共通台帳・型ゆらぎ検出・extensible enum の丸めを固定する。

設計: .specdb/docs/standard-pack-design.md §6.1（その他丸め）/ Phase 4。
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aggregate  # noqa: E402
from engine import Store  # noqa: E402

PACK_DIR = Path(__file__).resolve().parents[1] / "packs" / "jp-sier-std"


def write(base: Path, rel: str, text: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def project(name: str, data_items: str, screens: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"agg-{name}-"))
    shutil.copytree(PACK_DIR, root / "packs" / "jp-sier-std")
    write(root, "metamodel.yaml", "version: 1\nextends: jp-sier-std@1.1\n")
    write(root, "items/data-item/core.yaml", data_items)
    write(root, "items/screen/core.yaml", screens)
    return root


def make_projects():
    a = project(
        "A",
        "- { id: di-0001, name: 顧客コード, type: 文字列, length: 8, status: approved }\n"
        "- { id: di-0002, name: 受注日, type: 日付, status: approved }\n",
        "- { id: scr-0001, name: 一覧, screen_id: S1, screen_type: 一覧, description: x, status: approved }\n")
    b = project(
        "B",
        "- { id: di-0001, name: 顧客コード, type: 文字列, length: 10, status: approved }\n"
        "- { id: di-0009, name: 出荷日, type: 日付, status: approved }\n",
        "- { id: scr-0001, name: DB, screen_id: S1, screen_type: ダッシュボード, description: x, status: approved }\n")
    return [(a, Store.load(a)), (b, Store.load(b))]


def test_shared_item_detected_and_conflict_flagged():
    report = aggregate.build_report(make_projects())
    # 顧客コードは両プロジェクトに現れる = 共通、かつ length 不一致で要確認
    line = [l for l in report.splitlines() if "顧客コード" in l][0]
    assert "○" in line and "不一致" in line


def test_unique_item_not_marked_shared():
    report = aggregate.build_report(make_projects())
    line = [l for l in report.splitlines() if "出荷日" in l][0]
    assert "○" not in line


def test_extensible_enum_rolled_to_other_with_breakdown():
    report = aggregate.build_report(make_projects())
    # 標準値はそのまま集計、独自値 ダッシュボード は「その他」に丸め + 内訳
    assert "| 一覧 | 1 |" in report
    assert "| その他 | 1 |" in report
    assert "ダッシュボード" in report          # 内訳に元値
    # 内訳は昇格候補として提示される
    assert "昇格候補" in report


def test_type_filter_limits_census_and_skips_enum():
    report = aggregate.build_report(make_projects(), only="data-item")
    assert "データ項目" in report
    assert "画面" not in report                 # 種別で絞られる
    assert "拡張列挙の全社集計" not in report    # --type 指定時は enum 集計を出さない


def test_pack_chain_shown_per_project():
    report = aggregate.build_report(make_projects())
    assert report.count("jp-sier-std@1.1.0") >= 2
