# -*- coding: utf-8 -*-
"""標準パック — 継承チェーンの解決・メタモデルマージ・準拠検証・文書カタログ

設計は .specdb/docs/standard-pack-design.md。ここで実装するのは:
  Phase 1:
  - metamodel.yaml の `extends` から継承チェーン（単一親）を解決する
  - パックの templates/ を多層テンプレート検索・std/ プレフィックス参照に供する
  - パックの文書カタログとプロジェクト文書の from_standard マージ
  - テンプレート上書きの可視化（STD-W301 / STD-W303）
  Phase 2:
  - 実効メタモデル = チェーンをルートから重ねたマージ結果（merge_and_check）
  - L1 準拠検証（緩和の禁止。STD-E101〜E131）
  - L2 準拠検証（conformance/rules.yaml。STD-E201/E211/E221）
  - pack.lock の生成・照合（STD-W003）

engine はパックの存在を知らない（「メタモデルの出所」非依存の原則）。
Store.load は extends があれば merge_and_check を呼び、マージ済み dict を
Metamodel に渡すだけ。conform コマンドが L2・lock を追加で回す。
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engine import Problem, _parse_card

TOOL_DIR = Path(__file__).resolve().parent

# extends の「パック名@major.minor」形式。これ以外はパス直接参照（開発モード）
_SPEC_RE = re.compile(r"[a-z0-9-]+@\d+\.\d+")


@dataclass
class Pack:
    """解決済みのパック 1 層。meta は pack.yaml の内容そのまま。"""
    name: str
    version: str
    dir: Path
    meta: dict = field(default_factory=dict)

    @property
    def templates_dir(self) -> Path:
        return self.dir / self.meta.get("templates", "templates")

    @property
    def documents_dir(self) -> Path:
        return self.dir / self.meta.get("documents", "documents")

    @property
    def conformance_file(self) -> Path:
        return self.dir / self.meta.get("conformance", "conformance/rules.yaml")

    def metamodel(self) -> dict:
        """パックのメタモデル宣言。`metamodel:` はファイル or リスト（順にマージ）。"""
        spec = self.meta.get("metamodel", "metamodel/core.yaml")
        merged: dict = {}
        for rel in (spec if isinstance(spec, list) else [spec]):
            f = self.dir / rel
            if f.is_file():
                with open(f, encoding="utf-8") as fh:
                    merged = _shallow_merge_mm(merged, yaml.safe_load(fh) or {})
        return merged

    def reserved_namespaces(self) -> dict:
        ns = self.meta.get("reserved_namespaces") or {}
        return {n: n for n in ns} if isinstance(ns, list) else dict(ns)


def read_extends(root: Path) -> str | None:
    """metamodel.yaml の extends 宣言（無ければ None）。engine はこのキーを無視する。"""
    mm_file = root / "metamodel.yaml"
    if not mm_file.is_file():
        return None
    with open(mm_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("extends")


def resolve_chain(root: Path, problems: list[Problem]) -> list[Pack]:
    """継承チェーンを近い層から順に解決して返す（[事業部, 全社] の順）。

    extends が無ければ空リスト（スタンドアロン。従来動作そのまま）。
    解決失敗・バージョン不一致・循環は error を積み、解決できた層までを返す。
    """
    spec = read_extends(root)
    packs: list[Pack] = []
    seen: set[str] = set()
    base = root                       # パス形式 extends の相対基準（宣言した層）
    while spec:
        pack = _resolve_one(str(spec).strip(), root, base, problems)
        if pack is None:
            break
        if pack.name in seen:
            problems.append(Problem("error", f"pack:{pack.name}",
                                    "STD-E003 継承チェーンが循環している"))
            break
        seen.add(pack.name)
        packs.append(pack)
        spec, base = pack.meta.get("extends"), pack.dir
    return packs


def _resolve_one(spec: str, project_root: Path, base: Path,
                 problems: list[Problem]) -> Pack | None:
    """extends 1 段分を解決する。spec は 'name@major.minor' かパス。"""
    if not _SPEC_RE.fullmatch(spec):
        # パス直接参照（開発モード）。宣言した層のディレクトリからの相対
        return _load_pack((base / spec).resolve(), None, spec, problems)
    name, ver = spec.split("@")
    want = tuple(ver.split("."))
    candidates = [project_root / "packs" / name]          # vendored
    for p in os.environ.get("SPECDB_PACK_PATH", "").split(os.pathsep):
        if p:
            candidates.append(Path(p) / name)             # 追加検索パス
    candidates.append(TOOL_DIR / "packs" / name)          # ツール/スキル同梱
    for d in candidates:
        if (d / "pack.yaml").is_file():
            return _load_pack(d, want, spec, problems)
    problems.append(Problem("error", f"pack:{name}",
                            "STD-E001 パックを解決できない（探索: "
                            + ", ".join(str(c) for c in candidates) + "）"))
    return None


def _load_pack(d: Path, want: tuple | None, spec: str,
               problems: list[Problem]) -> Pack | None:
    f = d / "pack.yaml"
    if not f.is_file():
        problems.append(Problem("error", f"pack:{spec}",
                                f"STD-E001 pack.yaml が無い: {d}"))
        return None
    with open(f, encoding="utf-8") as fh:
        meta = yaml.safe_load(fh) or {}
    name, version = meta.get("pack"), str(meta.get("version") or "")
    if not name or not version:
        problems.append(Problem("error", f"pack:{spec}",
                                f"STD-E001 pack.yaml に pack / version が無い: {f}"))
        return None
    if want is not None and tuple(version.split(".")[:2]) != want:
        problems.append(Problem("error", f"pack:{name}",
                                f"STD-E002 extends '{spec}' と解決されたパックの "
                                f"version '{version}' が不一致"))
        return None
    return Pack(name, version, d, meta)


# ---------- メタモデルのマージと L1 準拠検証 ----------

_MM_SECTIONS = ("item_types", "relation_types")


def _shallow_merge_mm(base: dict, over: dict) -> dict:
    """パック内の複数メタモデルファイルを浅くマージする（item_types 等を結合）。"""
    out = dict(base)
    for k, v in over.items():
        if k in _MM_SECTIONS and isinstance(v, dict):
            out[k] = {**(base.get(k) or {}), **v}
        else:
            out[k] = v
    return out


def _card(spec):
    return None if spec is None else _parse_card(spec)


def _card_relaxed(base, over) -> bool:
    """多重度 over が base より緩いか（min を下げる or max を上げる/外す）。"""
    if base is None or over is None:
        return over is None and base is not None   # 宣言を消す = 緩和
    (bmin, bmax), (omin, omax) = base, over
    if omin < bmin:
        return True
    if bmax is not None and (omax is None or omax > bmax):
        return True
    return False


def merge_and_check(root: Path, project_data: dict, packs: list[Pack],
                    problems: list[Problem]) -> dict:
    """実効メタモデル = チェーンをルート（全社）から順に重ねた結果。

    各層は「その層より下をマージした結果」に対して §6 の緩和禁止規則に従う
    （L1 準拠検証）。違反は problems に STD-E1xx を積み、マージ自体は続行する
    （engine が後続の検証を回せるよう、可能な限り実効モデルを組み立てる）。
    """
    layers: list[tuple[str, dict]] = [(p.name, p.metamodel()) for p in reversed(packs)]
    project_mm = {k: v for k, v in project_data.items() if k != "extends"}
    layers.append(("(プロジェクト)", project_mm))

    reserved: dict[str, str] = {}         # 予約名前空間 名前 -> 予約した層
    for p in reversed(packs):
        for n in p.reserved_namespaces():
            reserved.setdefault(n, p.name)

    eff: dict = {"version": project_mm.get("version", 1),
                 "item_types": {}, "relation_types": {}, "namespaces": {}}
    origin = {"item": {}, "attr": {}, "rel": {}, "relattr": {}, "ns": {}}
    first = True
    for label, mm in layers:
        if not first:
            _check_layer(mm, eff, origin, label, problems)
        _merge_layer(eff, origin, mm, label, reserved, problems)
        first = False
    return eff


def _merge_layer(eff, origin, mm, label, reserved, problems) -> None:
    for tname, tdef in (mm.get("item_types") or {}).items():
        tdef = tdef or {}
        base = eff["item_types"].get(tname)
        if base is None:
            eff["item_types"][tname] = _copy_type(tdef)
            origin["item"][tname] = label
            for aname in (tdef.get("attributes") or {}):
                origin["attr"][(tname, aname)] = label
        else:
            for opt in ("label", "label_field", "id_prefix", "sequence",
                        "warn_if_unreferenced"):
                if opt in tdef:
                    base[opt] = tdef[opt]
            attrs = base.setdefault("attributes", {})
            for aname, spec in (tdef.get("attributes") or {}).items():
                attrs[aname] = {**(attrs.get(aname) or {}), **(spec or {})}
                origin["attr"][(tname, aname)] = label
    for rname, rdef in (mm.get("relation_types") or {}).items():
        rdef = rdef or {}
        base = eff["relation_types"].get(rname)
        if base is None:
            eff["relation_types"][rname] = _copy_type(rdef)
            origin["rel"][rname] = label
            for aname in (rdef.get("attributes") or {}):
                origin["relattr"][(rname, aname)] = label
        else:
            for opt in ("from", "to", "cardinality", "ordered", "embedded", "label"):
                if opt in rdef:
                    base[opt] = rdef[opt]
            attrs = base.setdefault("attributes", {})
            for aname, spec in (rdef.get("attributes") or {}).items():
                attrs[aname] = {**(attrs.get(aname) or {}), **(spec or {})}
                origin["relattr"][(rname, aname)] = label
    _merge_namespaces(eff, mm, label, reserved, problems)


def _copy_type(tdef: dict) -> dict:
    out = {k: v for k, v in tdef.items() if k != "attributes"}
    out["attributes"] = {a: dict(s or {}) for a, s in (tdef.get("attributes") or {}).items()}
    return out


def _merge_namespaces(eff, mm, label, reserved, problems) -> None:
    ns = mm.get("namespaces") or {}
    ns = {n: n for n in ns} if isinstance(ns, list) else dict(ns)
    for n, disp in ns.items():
        owner = reserved.get(n)
        if owner is not None and owner != label:
            problems.append(Problem("error", f"pack:{label}",
                                    f"STD-E131 名前空間 '{n}' は {owner} が予約している"
                                    "（再宣言は不可）"))
            continue
        if n in eff["namespaces"] and eff["namespaces"][n] != disp:
            problems.append(Problem("warn", f"pack:{label}",
                                    f"STD-E131? 名前空間 '{n}' の表示名が下位層と異なる"))
        eff["namespaces"][n] = disp


def _tag(label, base_label) -> str:
    return f"[{label} → {base_label}]"


def _check_layer(mm, eff, origin, label, problems) -> None:
    """overlay 層 mm を、下位をマージ済みの eff に対して §6 の緩和禁止で検査する。"""
    for tname, tdef in (mm.get("item_types") or {}).items():
        base = eff["item_types"].get(tname)
        if base is None:
            continue                      # 新種別の追加は自由
        tdef = tdef or {}
        tag = _tag(label, origin["item"].get(tname, "?"))
        for opt in ("id_prefix", "sequence"):
            if opt in tdef and opt in base and tdef[opt] != base[opt]:
                problems.append(Problem("error", f"metamodel:{tname}",
                                        f"STD-E121 {tag} {opt} の変更は不可"
                                        "（横断の ID 一貫性を壊す）"))
        _check_attrs(base.get("attributes") or {}, tdef.get("attributes") or {},
                     origin["attr"], tname, label, problems, kind="metamodel")
    for rname, rdef in (mm.get("relation_types") or {}).items():
        base = eff["relation_types"].get(rname)
        if base is None:
            continue                      # 新関係の追加は自由
        rdef = rdef or {}
        tag = _tag(label, origin["rel"].get(rname, "?"))
        for side in ("from", "to"):
            if side in rdef:                       # endpoint 種別の削除 = 緩和
                removed = set(_as_list(base.get(side))) - set(_as_list(rdef.get(side)))
                if removed:
                    problems.append(Problem("error", f"metamodel:{rname}",
                                            f"STD-E111 {tag} {side} から種別 "
                                            f"{sorted(removed)} を削除している（緩和）"))
            if "cardinality" in rdef and side in (rdef.get("cardinality") or {}):
                bc = _card((base.get("cardinality") or {}).get(side))
                oc = _card((rdef.get("cardinality") or {}).get(side))
                if _card_relaxed(bc, oc):
                    problems.append(Problem("error", f"metamodel:{rname}",
                                            f"STD-E112 {tag} {side} の多重度を緩めている"))
        if "ordered" in rdef and base.get("ordered") and not rdef["ordered"]:
            problems.append(Problem("error", f"metamodel:{rname}",
                                    f"STD-E113 {tag} ordered の解除は不可"))
        if "embedded" in rdef and "embedded" in base and rdef["embedded"] != base["embedded"]:
            problems.append(Problem("error", f"metamodel:{rname}",
                                    f"STD-E113 {tag} embedded 宣言の変更は不可"))
        _check_attrs(base.get("attributes") or {}, rdef.get("attributes") or {},
                     origin["relattr"], rname, label, problems, kind="metamodel")


def _check_attrs(base_attrs, over_attrs, origin_map, owner, label, problems, kind) -> None:
    for aname, over in over_attrs.items():
        base = base_attrs.get(aname)
        if base is None:
            continue                      # 属性の追加は自由（required でも厳格化）
        over = over or {}
        tag = _tag(label, origin_map.get((owner, aname), "?"))
        where = f"{kind}:{owner}.{aname}"
        if "kind" in over and "kind" in base and over["kind"] != base["kind"]:
            problems.append(Problem("error", where, f"STD-E101 {tag} kind の変更は不可"))
        if base.get("required") and over.get("required") is False:
            problems.append(Problem("error", where, f"STD-E102 {tag} required の緩和は不可"))
        if base.get("unique") and over.get("unique") is False:
            problems.append(Problem("error", where, f"STD-E103 {tag} unique の除去は不可"))
        if base.get("kind") == "enum" and "values" in over:
            added = [v for v in (over.get("values") or []) if v not in (base.get("values") or [])]
            if added and not base.get("extensible"):
                problems.append(Problem("error", where,
                                        f"STD-E104 {tag} extensible でない enum に値 "
                                        f"{added} を追加している"))


def _as_list(v):
    return v if isinstance(v, list) else ([] if v is None else [v])


# ---------- テンプレートの多層検索 ----------

def template_search_dirs(root: Path, packs: list[Pack]) -> list[Path]:
    """テンプレート検索パス: プロジェクト → 近い層のパック → …（近い者勝ち）。"""
    return [root / "templates", *(p.templates_dir for p in packs)]


def prefix_map(packs: list[Pack]) -> dict[str, Path]:
    """親層版を明示参照するプレフィックス: std/（直近層）・std2/（その親）…。

    同名テンプレートを部分上書きする際の {% extends "std/…" %} が使う。
    """
    return {("std" if i == 0 else f"std{i + 1}"): p.templates_dir
            for i, p in enumerate(packs)}


def check_template_overrides(root: Path, packs: list[Pack],
                             problems: list[Problem]) -> None:
    """プロジェクト層によるパックテンプレートの上書きを可視化する。

    - `_` 始まり（ハウススタイル部品）の上書き = STD-W301（様式逸脱）
    - {% extends "std/…" %} を使わない同名全置換 = STD-W303（fork によるドリフト）
    パック層どうしの上書きは統制下のカスタマイズなので対象外（設計メモ §6.3）。
    """
    tdir = root / "templates"
    if not tdir.is_dir() or not packs:
        return
    pack_names = {f.name for p in packs if p.templates_dir.is_dir()
                  for f in p.templates_dir.glob("*.j2")}
    for f in sorted(tdir.glob("*.j2")):
        if f.name not in pack_names:
            continue
        if f.name.startswith("_"):
            problems.append(Problem("warn", f"templates/{f.name}",
                                    "STD-W301 ハウススタイル部品をプロジェクト層で"
                                    "上書きしている（様式逸脱）"))
        elif not re.search(r"""{%-?\s*extends\s+["']std""", f.read_text(encoding="utf-8")):
            problems.append(Problem("warn", f"templates/{f.name}",
                                    "STD-W303 標準テンプレートの全置換"
                                    "（{% extends \"std/…\" %} + block 上書きを推奨）"))


# ---------- 文書カタログと from_standard マージ ----------

def document_catalog(packs: list[Pack]) -> dict[str, tuple[dict, Pack]]:
    """チェーン全層の文書カタログ {名前: (定義, パック)}。近い層が優先。"""
    catalog: dict[str, tuple[dict, Pack]] = {}
    for pack in reversed(packs):      # ルート層から重ね、近い層で上書き
        if not pack.documents_dir.is_dir():
            continue
        for f in sorted(pack.documents_dir.glob("*.yaml")):
            with open(f, encoding="utf-8") as fh:
                catalog[f.stem] = (yaml.safe_load(fh) or {}, pack)
    return catalog


class _KeepMissing(dict):
    """format_map 用: 未定義の {名前} は展開せずそのまま残す。"""
    def __missing__(self, key):
        return "{" + key + "}"


def _dig(d: dict, path: str):
    """'preface.purpose' のようなドット区切りで入れ子の値を引く。"""
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def merge_document(doc: dict, catalog: dict[str, tuple[dict, Pack]],
                   problems: list[Problem], where: str) -> dict | None:
    """プロジェクト文書定義の from_standard を標準カタログとマージする。

    from_standard が無ければそのまま返す。error を積んだら None
    （その文書は生成対象から外れ、error なので生成自体も止まる）。
    カタログ側の title / output に書いた {パラメータ名} はマージ後の
    トップレベル値で展開される（例: 基本設計書_{system_name}.html）。
    """
    name = doc.get("from_standard")
    if not name:
        return doc
    if name not in catalog:
        problems.append(Problem("error", where,
                                f"from_standard '{name}' が標準文書カタログに無い"
                                f"（候補: {', '.join(sorted(catalog)) or 'なし'}）"))
        return None
    base = dict(catalog[name][0])
    params = base.pop("params", None) or {}
    doc_no_spec = base.pop("doc_no", None)
    base.pop("abstract", None)
    merged = {**base, **{k: v for k, v in doc.items() if k != "from_standard"}}
    ok = True
    for p in params.get("required") or []:
        if _dig(merged, p) in (None, ""):
            problems.append(Problem("error", where,
                                    f"STD-E202 必須パラメータ '{p}' が未指定"))
            ok = False
    # doc_no: カタログ側が {pattern: …} なら採番規則として検査、素の値なら既定値
    if isinstance(doc_no_spec, dict) and doc_no_spec.get("pattern"):
        got = merged.get("doc_no")
        if got is not None and not re.fullmatch(doc_no_spec["pattern"], str(got)):
            problems.append(Problem("error", where,
                                    f"STD-E203 doc_no '{got}' が採番規則 "
                                    f"'{doc_no_spec['pattern']}' に不一致"))
            ok = False
    elif doc_no_spec is not None:
        merged.setdefault("doc_no", doc_no_spec)
    if not ok:
        return None
    ctx = _KeepMissing((k, v) for k, v in merged.items()
                       if isinstance(v, (str, int, float)))
    return {k: (v.format_map(ctx) if isinstance(v, str) else v)
            for k, v in merged.items()}


def collect_documents(root: Path, packs: list[Pack],
                      problems: list[Problem]) -> list[tuple[str, dict]]:
    """生成対象の文書定義を (名前, マージ済み定義) で列挙する。

    = プロジェクト documents/（from_standard はカタログとマージ）
      + プロジェクトが実体化していない非 abstract の標準文書。
    abstract: true の標準文書は実体化されない限り生成対象に入らない（§6.4）。
    """
    catalog = document_catalog(packs)
    docs: list[tuple[str, dict]] = []
    project_stems: set[str] = set()
    docs_dir = root / "documents"
    if docs_dir.is_dir():
        for f in sorted(docs_dir.glob("*.yaml")):
            with open(f, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
            project_stems.add(f.stem)
            merged = merge_document(doc, catalog, problems, f"documents/{f.stem}")
            if merged is not None:
                docs.append((f.stem, merged))
    for stem, (doc, _pack) in sorted(catalog.items()):
        if stem not in project_stems and not doc.get("abstract"):
            docs.append((stem, dict(doc)))
    return sorted(docs, key=lambda d: d[0])


# ---------- L2 準拠検証（conformance/rules.yaml） ----------

def load_conformance_rules(packs: list[Pack]) -> dict:
    """チェーン全層の準拠規則をまとめる。

    require_documents / attribute_rules は加法、status_rules は近い層が優先。
    """
    require: list = []
    attr_rules: list = []
    status_rules: dict = {}
    for pack in reversed(packs):          # ルート→近い層。status_rules は近い層で上書き
        f = pack.conformance_file
        if not f.is_file():
            continue
        with open(f, encoding="utf-8") as fh:
            rules = yaml.safe_load(fh) or {}
        for d in rules.get("require_documents") or []:
            if d not in require:
                require.append(d)
        attr_rules.extend(rules.get("attribute_rules") or [])
        status_rules.update(rules.get("status_rules") or {})
    return {"require_documents": require, "attribute_rules": attr_rules,
            "status_rules": status_rules}


def check_conformance_rules(root: Path, packs: list[Pack], store,
                            problems: list[Problem], for_baseline: bool = False) -> None:
    """L2: データ・文書に対する準拠規則を検査する（conform コマンドが呼ぶ）。"""
    if not packs:
        return
    rules = load_conformance_rules(packs)

    generated = {name for name, _ in collect_documents(root, packs, problems)}
    for name in rules["require_documents"]:
        if name not in generated:
            problems.append(Problem("error", f"documents/{name}",
                                    f"STD-E201 必須の標準文書 '{name}' が実体化されていない"))

    for rule in rules["attribute_rules"]:
        t, attr = rule.get("type"), rule.get("attribute")
        when = rule.get("when_status") or []
        level = rule.get("level", "error")
        if not t or not attr:
            continue
        for item in store.items_of(t):
            if item.status in when and not item.attrs.get(attr):
                problems.append(Problem(level, f"{item.type}:{item.id}",
                                        f"STD-E211 status={item.status} では属性 "
                                        f"'{attr}' の記載が必須"))

    if for_baseline:
        need = rules["status_rules"].get("baseline_requires")
        if need == "approved":
            for item in store.items.values():
                if item.status in ("draft", "review"):
                    problems.append(Problem("error", f"{item.type}:{item.id}",
                                            f"STD-E221 ベースライン前提: status={item.status} "
                                            "が残っている（approved が必要）"))
            for r in store.relations:
                if r.status in ("draft", "review"):
                    problems.append(Problem("error", f"relation:{r.type}",
                                            f"STD-E221 ベースライン前提: 関係 "
                                            f"{r.src}->{r.dst} の status={r.status}"))


# ---------- pack.lock ----------

def _rel(root: Path, d: Path) -> str:
    """パックの所在を root からの相対パスで表す（情報用）。

    絶対パス（ドライブ名・ユーザ名などローカルな情報）は決して残さない —
    lock はコミット・配布される成果物であり、resolved_from は照合には使わない
    （verify_lock は pack/version/content_hash のみ比較する）。相対化できない
    （別ドライブ等）ときはパック名だけにフォールバックする。"""
    root, d = root.resolve(), d.resolve()
    try:
        return d.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return Path(os.path.relpath(d, root)).as_posix()
    except ValueError:
        return d.name


def _hash_dir(d: Path) -> str:
    """パックディレクトリ全ファイルの正規化ハッシュ（パス + 内容）。"""
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        if "__pycache__" in f.parts or f.suffix == ".pyc":
            continue
        h.update(f.relative_to(d).as_posix().encode("utf-8") + b"\0")
        h.update(f.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()


def chain_lock(root: Path, packs: list[Pack]) -> dict:
    return {"chain": [{"pack": p.name, "resolved_version": p.version,
                       "content_hash": _hash_dir(p.dir),
                       "resolved_from": _rel(root, p.dir)} for p in packs]}


def write_lock(root: Path, packs: list[Pack]) -> Path:
    lock = root / "pack.lock"
    lock.write_text(
        "# 機械生成。直接編集しない（specdb pack lock で更新する）。\n"
        + yaml.safe_dump(chain_lock(root, packs), allow_unicode=True, sort_keys=False),
        encoding="utf-8", newline="\n")
    return lock


def verify_lock(root: Path, packs: list[Pack], problems: list[Problem],
                frozen: bool = False) -> None:
    """pack.lock と解決結果を照合する。lock 未作成なら何もしない（lock は明示運用）。"""
    lock = root / "pack.lock"
    if not lock.is_file():
        return
    with open(lock, encoding="utf-8") as f:
        locked = yaml.safe_load(f) or {}
    # 照合は移植可能な同一性（pack / 版 / 内容ハッシュ）だけで行う。resolved_from は
    # 所在の情報にすぎず、レイアウト（開発リポ / 消費側の同梱パック）で変わるため
    # 比較に含めない。含めると同じパックでも環境違いで frozen が誤検知する。
    def _identity(chain):
        return [{"pack": e.get("pack"), "resolved_version": e.get("resolved_version"),
                 "content_hash": e.get("content_hash")} for e in (chain or [])]
    if _identity(locked.get("chain")) != _identity(chain_lock(root, packs).get("chain")):
        problems.append(Problem("error" if frozen else "warn", "pack.lock",
                                "STD-W003 pack.lock と解決結果が一致しない"
                                "（specdb pack lock で更新）"))
