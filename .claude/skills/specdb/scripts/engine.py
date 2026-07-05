# -*- coding: utf-8 -*-
"""汎用仕様データエンジン — メタスキーマ駆動のローダ/バリデータ

特定のアイテム種別（データ項目・エンティティ…）を一切知らない。
何が存在してよいかはすべて metamodel.yaml の宣言に従う。

    python specdb/engine.py                    # 検証レポートと統計を表示
    python engine.py --root <データディレクトリ>  # ツールとデータを分離して使う場合

レイアウト:
    metamodel.yaml                 # 種別・属性・関係の宣言
    items/<種別>/*.yaml            # アイテム（リストでも 1 件 1 ファイルでも可）
    relations/*.yaml               # 独立した関係レコード
                                   # （メタモデルで embedded 宣言した関係は
                                   #   アイテム内に埋め込み記述でき、読込時に正規化される）
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent

# --root 省略時に探す規約上のデータディレクトリ（カレントディレクトリ基準）。
# 見つからなければツール同梱データ (ROOT) にフォールバックする。
DEFAULT_DATA_DIR = ".specdb"

CORE_KEYS = {"id", "status", "source"}
STATUSES = ("draft", "review", "approved", "deprecated")

_KIND_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "int":    lambda v: isinstance(v, int) and not isinstance(v, bool),
    "bool":   lambda v: isinstance(v, bool),
    # list / map は入れ子構造をそのまま持つ（浅い型検査のみ）。パックの
    # 文書カタログ・準拠規則のような list/map 値の設定を正本化できるようにする。
    "list":   lambda v: isinstance(v, list),
    "map":    lambda v: isinstance(v, dict),
}


def _parse_card(spec) -> tuple[int, int | None] | None:
    """多重度 '1..*' / '0..1' / '2' を (min, max|上限なしなら None) に。不正なら None。"""
    s = str(spec)
    lo, sep, hi = s.partition("..")
    if not sep:
        lo = hi = s
    try:
        lo_n = int(lo)
        hi_n = None if hi == "*" else int(hi)
    except ValueError:
        return None
    if lo_n < 0 or (hi_n is not None and hi_n < lo_n):
        return None
    return lo_n, hi_n


def _qualify(ns: str, ref) -> str:
    """名前空間ディレクトリ配下の ID 参照を修飾する（既に修飾済みならそのまま）。"""
    return ref if not ns or ":" in str(ref) else f"{ns}:{ref}"


def _normalize_source(src, where: str, problems: list["Problem"]) -> list | None:
    """source を出典リストへ正規化する。単数のマップでもリストでも受け付ける。"""
    if src is None:
        return None
    entries = src if isinstance(src, list) else [src]
    out = []
    for e in entries:
        if not isinstance(e, dict) or "doc" not in e:
            problems.append(Problem(
                "error", where, "source は doc を持つマップ（またはそのリスト）で書く"))
            continue
        out.append(e)
    return out or None


@dataclass
class Problem:
    level: str      # "error" | "warn"
    where: str      # 問題の場所（ファイルや ID）
    message: str

    def __str__(self) -> str:
        return f"{self.level}: [{self.where}] {self.message}"


@dataclass
class Item:
    id: str
    type: str
    attrs: dict
    status: str = "draft"
    source: list | None = None   # 出典のリスト（単数で書かれても正規化される）

    def label(self, mm: "Metamodel") -> str:
        lf = mm.item_types[self.type].get("label_field")
        return str(self.attrs.get(lf, self.id)) if lf else self.id


@dataclass
class Relation:
    type: str
    src: str
    dst: str
    attrs: dict = field(default_factory=dict)
    status: str = "draft"
    source: list | None = None   # アイテムと同じコア属性を関係にも持たせる


class Metamodel:
    def __init__(self, data: dict, problems: list[Problem]):
        self.item_types: dict = data.get("item_types") or {}
        self.relation_types: dict = data.get("relation_types") or {}
        ns = data.get("namespaces") or {}
        # {名前: 表示名} のマップ。リストで書かれたら名前をそのまま表示名にする
        self.namespaces: dict = {n: n for n in ns} if isinstance(ns, list) else dict(ns)
        self._self_check(problems)

    @staticmethod
    def load(path: Path, problems: list[Problem]) -> "Metamodel":
        with open(path, encoding="utf-8") as f:
            return Metamodel(yaml.safe_load(f) or {}, problems)

    def _self_check(self, problems: list[Problem]) -> None:
        """メタモデル自体の整合性を検査する。"""
        for tname, tdef in self.item_types.items():
            for aname, spec in (tdef.get("attributes") or {}).items():
                kind = spec.get("kind")
                if kind not in (*_KIND_CHECKS, "enum"):
                    problems.append(Problem("error", f"metamodel:{tname}.{aname}",
                                            f"未知の kind '{kind}'"))
                if kind == "enum" and not spec.get("values"):
                    problems.append(Problem("error", f"metamodel:{tname}.{aname}",
                                            "enum に values がない"))
        for rname, rdef in self.relation_types.items():
            for side in ("from", "to"):
                types = rdef.get(side)
                types = types if isinstance(types, list) else [types]
                for t in types:
                    if t not in self.item_types:
                        problems.append(Problem("error", f"metamodel:{rname}",
                                                f"{side} が未定義の種別 '{t}' を参照"))
            for side, spec in (rdef.get("cardinality") or {}).items():
                if side not in ("from", "to"):
                    problems.append(Problem("error", f"metamodel:{rname}",
                                            f"cardinality のキーは from/to（'{side}' は不可）"))
                elif _parse_card(spec) is None:
                    problems.append(Problem("error", f"metamodel:{rname}",
                                            f"多重度 '{spec}' が不正（例: '1..*', '0..1', '1'）"))

    def endpoint_types(self, rname: str, side: str) -> list[str]:
        v = self.relation_types[rname].get(side)
        return v if isinstance(v, list) else [v]

    def cardinality(self, rname: str, side: str) -> tuple[int, int | None] | None:
        """関係 rname の side（from/to）に宣言された多重度。未宣言・不正なら None。"""
        spec = (self.relation_types[rname].get("cardinality") or {}).get(side)
        return None if spec is None else _parse_card(spec)

    def embedded_relations_of(self, item_type: str):
        """この種別のアイテムに埋め込み記述できる関係 [(関係名, 定義), ...]"""
        return [(rn, rd) for rn, rd in self.relation_types.items()
                if rd.get("embedded") and item_type in self.endpoint_types(rn, "from")]


class Store:
    """items/ と relations/ を読み込み、メタモデルに従って検証した結果。"""

    def __init__(self, mm: Metamodel):
        self.mm = mm
        self.items: dict[str, Item] = {}
        self.relations: list[Relation] = []
        self.problems: list[Problem] = []
        self.packs: list = []            # 標準パックの継承チェーン（extends 使用時のみ）

    # ---------- 読み込み ----------

    @staticmethod
    def load(root: Path = ROOT) -> "Store":
        problems: list[Problem] = []
        mm_file = root / "metamodel.yaml"
        with open(mm_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # 標準パック: extends があればチェーンを解決し、実効メタモデル（マージ済み）
        # を作る。engine 自体はパックを知らず、standard が返す dict を受けるだけ。
        packs: list = []
        if data.get("extends"):
            import standard
            packs = standard.resolve_chain(root, problems)
            data = standard.merge_and_check(root, data, packs, problems)
        mm = Metamodel(data, problems)
        store = Store(mm)
        store.problems = problems
        store.packs = packs
        store._load_items(root / "items")
        store._load_relations(root / "relations")
        store._validate_relations()
        store._check_cardinality()
        store._check_uniqueness()
        store._check_unreferenced()
        return store

    def _load_items(self, items_dir: Path, ns: str = "") -> None:
        if not items_dir.is_dir():
            return
        for tdir in sorted(p for p in items_dir.iterdir() if p.is_dir()):
            t = tdir.name
            if t in self.mm.item_types:
                for f in sorted(tdir.glob("*.yaml")):
                    for rec in _records(f):
                        self._add_item(t, rec, f, ns)
            elif not ns and t in self.mm.namespaces:
                self._load_items(tdir, ns=t)   # items/<名前空間>/<種別>/ の 1 階層
            else:
                self.problems.append(Problem(
                    "error", str(tdir),
                    f"メタモデルに無い種別ディレクトリ '{t}'"
                    "（名前空間として使うなら namespaces に宣言する）"))
        for stray in sorted(items_dir.glob("*.yaml")):
            self.problems.append(Problem(
                "warn", str(stray), "種別ディレクトリ直下でない YAML は読み込まない"))

    def _add_item(self, t: str, rec: dict, f: Path, ns: str = "") -> None:
        where = f"{f.name}"
        iid = rec.pop("id", None)
        if not iid:
            self.problems.append(Problem("error", where, "id がない"))
            return
        iid = _qualify(ns, iid)
        if iid in self.items:
            self.problems.append(Problem("error", iid, "ID が重複"))
            return
        status, source = self._pop_core(rec, iid)

        # 埋め込み関係を正規化して関係レコードへ
        for rname, rdef in self.mm.embedded_relations_of(t):
            emb = rdef["embedded"]
            entries = rec.pop(emb["field"], None) or []
            for e in entries:
                if isinstance(e, dict):
                    key = emb.get("target_key", "item")
                    dst = e.pop(key, None)
                    if dst is None:
                        self.problems.append(Problem(
                            "error", iid, f"{emb['field']} の要素に {key} がない"))
                        continue
                    dst = _qualify(ns, dst)
                    rstatus, rsource = self._pop_core(e, f"{rname}:{iid}->{dst}")
                    self.relations.append(Relation(rname, iid, dst, e, rstatus, rsource))
                else:
                    self.relations.append(Relation(rname, iid, _qualify(ns, str(e))))

        declared = self.mm.item_types[t].get("attributes") or {}
        self._validate_attrs(declared, rec, iid)
        self.items[iid] = Item(iid, t, rec, status, source)

    def _pop_core(self, rec: dict, where: str) -> tuple[str, list | None]:
        """レコードから共通コア属性 status / source を取り出して検証する。"""
        status = rec.pop("status", "draft")
        if status not in STATUSES:
            self.problems.append(Problem("error", where, f"未知の status '{status}'"))
        source = _normalize_source(rec.pop("source", None), where, self.problems)
        return status, source

    def _load_relations(self, rel_dir: Path, ns: str = "") -> None:
        if not rel_dir.is_dir():
            return
        for sub in sorted(p for p in rel_dir.iterdir() if p.is_dir()):
            if not ns and sub.name in self.mm.namespaces:
                self._load_relations(sub, ns=sub.name)   # relations/<名前空間>/
            else:
                self.problems.append(Problem(
                    "error", str(sub),
                    f"relations/ 配下のディレクトリ '{sub.name}' は宣言済みの名前空間のみ可"))
        for f in sorted(rel_dir.glob("*.yaml")):
            for rec in _records(f):
                rname = rec.pop("type", None)
                src, dst = rec.pop("from", None), rec.pop("to", None)
                if rname not in self.mm.relation_types:
                    self.problems.append(Problem(
                        "error", f.name, f"未知の関係種別 '{rname}'"))
                    continue
                if not src or not dst:
                    self.problems.append(Problem("error", f.name, "from/to がない"))
                    continue
                src, dst = _qualify(ns, src), _qualify(ns, dst)
                status, source = self._pop_core(rec, f"{rname}:{src}->{dst}")
                self.relations.append(Relation(rname, src, dst, rec, status, source))

    # ---------- 検証 ----------

    def _validate_attrs(self, declared: dict, given: dict, where: str) -> None:
        for k, spec in declared.items():
            if spec.get("required") and k not in given:
                self.problems.append(Problem("error", where, f"必須属性 '{k}' がない"))
        for k, v in given.items():
            spec = declared.get(k)
            if spec is None:
                self.problems.append(Problem("warn", where, f"宣言されていない属性 '{k}'"))
                continue
            kind = spec.get("kind")
            if kind == "enum":
                if v not in spec.get("values", []):
                    self.problems.append(Problem(
                        "error", where, f"'{k}' の値 '{v}' は許容値 {spec.get('values')} にない"))
            elif kind in _KIND_CHECKS and not _KIND_CHECKS[kind](v):
                self.problems.append(Problem(
                    "error", where, f"'{k}' は {kind} であるべき (実際: {type(v).__name__})"))

    def _validate_relations(self) -> None:
        for rel in self.relations:
            rdef = self.mm.relation_types[rel.type]
            for side, iid in (("from", rel.src), ("to", rel.dst)):
                item = self.items.get(iid)
                if item is None:
                    self.problems.append(Problem(
                        "error", f"{rel.type}:{rel.src}->{rel.dst}",
                        f"{side} が未定義のアイテム '{iid}' を参照"))
                elif item.type not in self.mm.endpoint_types(rel.type, side):
                    self.problems.append(Problem(
                        "error", f"{rel.type}:{rel.src}->{rel.dst}",
                        f"{side} の種別 '{item.type}' はこの関係に使えない"))
            declared = dict(rdef.get("attributes") or {})
            if rdef.get("ordered"):
                # ordered な関係は暗黙の並び順属性 order を持てる
                declared.setdefault("order", {"kind": "int"})
            self._validate_attrs(declared, rel.attrs,
                                 f"{rel.type}:{rel.src}->{rel.dst}")

    def _check_cardinality(self) -> None:
        """関係種別に宣言された多重度（cardinality: {from: '1..*', …}）を検査する。
        deprecated のアイテムは対象外。"""
        for rname, rdef in self.mm.relation_types.items():
            for side, key in (("from", "src"), ("to", "dst")):
                card = self.mm.cardinality(rname, side)
                if card is None:
                    continue
                lo, hi = card
                spec = rdef["cardinality"][side]
                for item in self.items.values():
                    if (item.type not in self.mm.endpoint_types(rname, side)
                            or item.status == "deprecated"):
                        continue
                    n = sum(1 for r in self.relations
                            if r.type == rname and getattr(r, key) == item.id)
                    if n < lo or (hi is not None and n > hi):
                        self.problems.append(Problem(
                            "error", item.id,
                            f"関係 '{rname}' の {side} 多重度 {spec} に違反（実際 {n} 本）"))

    def _check_uniqueness(self) -> None:
        """unique: true 宣言（アイテム属性は種別内、関係属性は同一 from 内で一意）と
        関係レコードの重複（同じ type/from/to）を検査する。"""
        for t, tdef in self.mm.item_types.items():
            for aname, spec in (tdef.get("attributes") or {}).items():
                if not spec.get("unique"):
                    continue
                seen: dict = {}
                for item in self.items_of(t):
                    v = item.attrs.get(aname)
                    if v is None:
                        continue
                    if v in seen:
                        self.problems.append(Problem(
                            "error", item.id,
                            f"'{aname}' の値 '{v}' が {seen[v]} と重複"))
                    else:
                        seen[v] = item.id
        for rname, rdef in self.mm.relation_types.items():
            for aname, spec in (rdef.get("attributes") or {}).items():
                if not spec.get("unique"):
                    continue
                seen = {}
                for r in self.relations:
                    v = r.attrs.get(aname)
                    if r.type != rname or v is None:
                        continue
                    prev = seen.get((r.src, v))
                    if prev:
                        self.problems.append(Problem(
                            "error", f"{rname}:{r.src}->{r.dst}",
                            f"'{aname}' の値 '{v}' が同じ from 内で {prev} と重複"))
                    else:
                        seen[(r.src, v)] = f"{rname}:{r.src}->{r.dst}"
        seen_rel: set = set()
        for r in self.relations:
            k = (r.type, r.src, r.dst)
            if k in seen_rel:
                self.problems.append(Problem(
                    "warn", f"{r.type}:{r.src}->{r.dst}", "同じ関係レコードが重複している"))
            seen_rel.add(k)

    def _check_unreferenced(self) -> None:
        referenced = {r.dst for r in self.relations}
        for item in self.items.values():
            tdef = self.mm.item_types.get(item.type, {})
            if tdef.get("warn_if_unreferenced") and item.id not in referenced:
                self.problems.append(Problem(
                    "warn", item.id,
                    f"{tdef.get('label', item.type)} '{item.label(self.mm)}' は"
                    "どの関係からも参照されていない（孤児）"))

    # ---------- クエリ API（ジェネレータが使う） ----------

    def items_of(self, t: str) -> list[Item]:
        return [i for i in self.items.values() if i.type == t]

    def relations_of(self, rtype: str | None = None,
                     src: str | None = None, dst: str | None = None) -> list[Relation]:
        rels = [r for r in self.relations
                if (rtype is None or r.type == rtype)
                and (src is None or r.src == src)
                and (dst is None or r.dst == dst)]
        if rtype and (self.mm.relation_types.get(rtype) or {}).get("ordered"):
            # 明示的な order を持つものを先頭に order 順で。無いものは記述順のまま
            rels.sort(key=lambda r: (0, r.attrs["order"]) if "order" in r.attrs else (1, 0))
        return rels

    def relating_to(self, rtype: str, dsts) -> list["Item"]:
        """dsts のいずれかへ rtype 関係を張っている from 側アイテム（逆引き）。"""
        ds = set(dsts)
        srcs = {r.src for r in self.relations if r.type == rtype and r.dst in ds}
        return [i for i in self.items.values() if i.id in srcs]

    def has_errors(self) -> bool:
        return any(p.level == "error" for p in self.problems)


def _records(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    recs = data if isinstance(data, list) else [data]
    return [r for r in recs if isinstance(r, dict)]


def parse_root(args: list[str], default: Path = ROOT) -> tuple[Path, list[str]]:
    """先頭の --root <dir> を取り出し (データルート, 残りの引数) を返す。
    generate.py / diff.py も共用する。

    --root 省略時はカレントディレクトリの .specdb/ (metamodel.yaml を持つもの)
    を優先し、無ければ default (ツール同梱データ) を使う。"""
    if args and args[0] == "--root":
        if len(args) < 2:
            sys.exit("--root にはデータディレクトリを指定する。")
        root = Path(args[1])
        if not (root / "metamodel.yaml").is_file():
            sys.exit(f"{root} に metamodel.yaml が無い。仕様データのルートを指定する。")
        return root, args[2:]
    conventional = Path(DEFAULT_DATA_DIR)
    if (conventional / "metamodel.yaml").is_file():
        return conventional, args
    return default, args


def main() -> int:
    root, _ = parse_root(sys.argv[1:])
    store = Store.load(root)
    for p in store.problems:
        print(p, file=sys.stderr)

    mm = store.mm
    print("アイテム:")
    for t, tdef in mm.item_types.items():
        n = len(store.items_of(t))
        print(f"  {tdef.get('label', t):<8} ({t}): {n} 件")
    print("関係:")
    for rn, rdef in mm.relation_types.items():
        n = len(store.relations_of(rn))
        print(f"  {rdef.get('label', rn):<8} ({rn}): {n} 件")
    errs = sum(1 for p in store.problems if p.level == "error")
    warns = len(store.problems) - errs
    print(f"検証: error {errs} 件 / warn {warns} 件")
    return 1 if store.has_errors() else 0


if __name__ == "__main__":
    sys.exit(main())
