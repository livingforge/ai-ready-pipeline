"""共有仮想環境への自動ブートストラップ。

各スキルのランチャー (run_*.py) が、本体パッケージを import する前に
`ensure_env(...)` を呼ぶ。プロジェクトルート直下の共有 venv (``<root>/.venv``)
を uv で用意し、その venv の python で本体を実行し直す。

- 既に共有 venv の python で動いていれば何もしない (素通り)
- 外部取得・インストールを伴う**高リスク操作**は、実行前に承認ゲート
  (:func:`_gate`) を必ず通す。既定は **fail-closed** (未承認なら停止)。
    - ``uv`` が無ければ公式インストーラで導入
    - venv が無ければ ``uv venv`` で作成 (必要なら Python 本体も uv が調達)
    - スキルの requirements.txt が未反映なら ``uv pip install``
      (数百 MB のダウンロードを伴う)
  承認は次のいずれか: opt-in env ``DOCEXTRACT_AUTOINSTALL=1``、または
  対話端末での y/N 確認。``DOCEXTRACT_NO_UV_AUTOINSTALL=1`` は最優先で禁止する。
- requirements のハッシュを marker に記録し、変化が無ければ再インストールしない
- 共有 venv の python でスクリプトを実行し直し、その終了コードで終わる

共有 venv はルート直下に置くので、他スキルのランチャーからも
同じ ``_bootstrap`` を使って同一環境を共用できる (marker はスキルごとに分離)。

**安全設計 (承認ゲート)**: リモートスクリプトの download→exec や数百 MB の依存
インストールは、既定では自動実行しない。opt-out (抑止フラグ) ではなく **opt-in**
を採用し、承認が無い非対話実行は停止する。エージェント/自動実行では、ユーザに
実行内容と規模を提示して承認を得てから ``DOCEXTRACT_AUTOINSTALL=1`` を付けて呼ぶ。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 再帰実行を防ぐガード。再 exec 後の子プロセスではこれが立っている。
_GUARD_ENV = "DOCEXTRACT_BOOTSTRAPPED"
# 高リスク操作 (外部取得・インストール) の自動実行を明示的に許可する opt-in フラグ。
# 既定 (未設定) は fail-closed。承認を得た実行に限りこれを立てる。
_AUTOINSTALL_ENV = "DOCEXTRACT_AUTOINSTALL"
# 明示的な禁止フラグ。opt-in に反転した現在も、最優先の「絶対に自動実行しない」
# 指定として尊重する (CI・オフライン・監査環境で使う)。
_NO_AUTOINSTALL_ENV = "DOCEXTRACT_NO_UV_AUTOINSTALL"

_UV_INSTALL_HINT = (
    "  Windows      : powershell -ExecutionPolicy ByPass -c "
    '"irm https://astral.sh/uv/install.ps1 | iex"\n'
    "  macOS / Linux: curl -LsSf https://astral.sh/uv/install.sh | sh"
)

_TRUTHY = {"1", "true", "yes", "on"}


def _force_utf8_io() -> None:
    """非 UTF-8 コンソール (Windows 既定の cp932 等) でも非 ASCII 出力で
    クラッシュしないよう、標準出力/標準エラーを UTF-8・エラー耐性つきに再設定する。

    em-dash (—) など cp932 に無い文字を print した際の UnicodeEncodeError を防ぐ。
    ``PYTHONIOENCODING=utf-8`` を毎回外から設定するのと同じ効果を、呼び出し側
    (エージェント/利用者) に意識させずコード側で恒常的に効かせるためのもの。
    既に UTF-8 の環境や stdout がパイプの場合でも実質無害。この _bootstrap を
    import した時点で一度だけ適用されるので、各エントリポイントは import する
    だけでよい。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 既に UTF-8 でラップされていない/差し替え済み等
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


# import 時に一度だけ適用する。run_docextract / run_docagent / setup_env は
# いずれも本モジュールを import するため、これだけで全エントリを覆える。
_force_utf8_io()


def _bootstrap_log_path(root: Path) -> Path:
    """セットアップ (uv venv / pip install) の詳細ログの保存先。

    実行時ログ (obs の ``logs/<run_id>.jsonl``) と同じ基点に置く:
    ``DOCEXTRACT_HOME`` があればその配下、無ければ ``<root>/.docextract``。
    docextract 本体を import する前に呼ぶため、paths を使わず env を直接読む。
    """
    home = os.environ.get("DOCEXTRACT_HOME")
    base = Path(home) if home else root / ".docextract"
    return base / "logs" / "bootstrap.log"


def _tail(path: Path, n: int) -> str:
    """ログ末尾 ``n`` 行。失敗時の手掛かりを stderr に出すのに使う。"""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def _run_step(cmd: list[str], log_path: Path, label: str) -> None:
    """セットアップの外部コマンドを実行する。

    数百 MB のダウンロードを伴う ``uv pip install`` 等は出力が極めて冗長で、
    そのまま標準出力に流すと呼び出し側 (LLM/エージェント) のコンテキストを圧迫する。
    そこで **非対話 (パイプ/エージェント) 実行では出力をログファイルへ退避**し、
    stdout/stderr には要点だけ残す。対話端末ではライブ進捗が見えるようそのまま継承する。
    失敗時はログ末尾を stderr に出して原因を追えるようにする。
    """
    interactive = bool(getattr(sys.stderr, "isatty", lambda: False)())
    if interactive:
        subprocess.run(cmd, check=True)
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n=== {label} ===\n{' '.join(map(str, cmd))}\n")
        f.flush()
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        print(
            f"[bootstrap] {label} に失敗しました (詳細ログ: {log_path})\n"
            + _tail(log_path, 40),
            file=sys.stderr,
        )
        sys.exit(proc.returncode)
    print(f"[bootstrap] {label} 完了 (詳細ログ: {log_path})", file=sys.stderr)


def _autoinstall_opted_in() -> bool:
    """opt-in env ``DOCEXTRACT_AUTOINSTALL`` が承認値かどうか。"""
    return os.environ.get(_AUTOINSTALL_ENV, "").strip().lower() in _TRUTHY


def _gate(action: str, commands: list[str], note: str = "") -> None:
    """高リスク操作 (外部取得・インストール) の承認ゲート。

    実行される**具体的なコマンドとダウンロード規模**を提示したうえで、
    次の順で判定する:

    1. ``DOCEXTRACT_NO_UV_AUTOINSTALL`` が立っていれば常に停止 (手動導入を案内)。
    2. opt-in env ``DOCEXTRACT_AUTOINSTALL=1`` があれば承認済みとして通す。
    3. 対話端末なら実行内容を提示して y/N 確認を取る。
    4. いずれでもない (非対話・未承認) なら **fail-closed** で停止する。

    opt-out (抑止フラグ) ではなく opt-in を採ることで「既定で安全」を担保する。
    """
    detail = "\n".join(f"    {c}" for c in commands)
    banner = (
        f"[bootstrap] 高リスク操作の承認が必要です: {action}\n"
        "  実行されるコマンド:\n"
        f"{detail}\n"
    )
    if note:
        banner += f"  {note}\n"
    print(banner, file=sys.stderr)

    if os.environ.get(_NO_AUTOINSTALL_ENV):
        sys.exit(
            f"[bootstrap] {_NO_AUTOINSTALL_ENV} が設定されているため中止しました。\n"
            "  上記コマンドを手動で実行してから再実行してください:\n"
            + _UV_INSTALL_HINT
        )
    if _autoinstall_opted_in():
        print(
            f"[bootstrap] {_AUTOINSTALL_ENV}=1 により承認済みとして続行します。",
            file=sys.stderr,
        )
        return
    stdin = sys.stdin
    if stdin is not None and stdin.isatty():
        try:
            answer = input("  この操作を実行しますか? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer in ("y", "yes"):
            return
        sys.exit("[bootstrap] 承認されなかったため中止しました。")
    # 非対話 かつ 未承認: 既定で安全側 (fail-closed) に倒す。
    sys.exit(
        "[bootstrap] 承認が無いため中止しました (fail-closed)。\n"
        f"  自動実行を許可するには {_AUTOINSTALL_ENV}=1 を設定して再実行するか、\n"
        "  上記コマンドを手動で実行してから再実行してください:\n"
        + _UV_INSTALL_HINT
    )


def _project_root(start: Path) -> Path:
    """スクリプトの位置からプロジェクトルートを推定する。

    配布物は ``<root>/.claude/skills/.../scripts/`` (または ``.github/...``) に
    展開されるので、``.claude`` / ``.github`` を祖先に見つけたらその親をルートと
    みなす。見つからなければ ``.git`` を辿り、最後は最上位を返す。
    """
    for parent in start.parents:
        if parent.name in (".claude", ".github"):
            return parent.parent
    for parent in start.parents:
        if (parent / ".git").exists():
            return parent
    return start.parents[-1]


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    # PATH に無くても既定のインストール先にはあることが多い。
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"),
        home / ".cargo" / "bin" / ("uv.exe" if os.name == "nt" else "uv"),
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def _install_uv() -> str:
    if os.name == "nt":
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "ByPass",
            "-c", "irm https://astral.sh/uv/install.ps1 | iex",
        ]
        shown = (
            'powershell -ExecutionPolicy ByPass -c '
            '"irm https://astral.sh/uv/install.ps1 | iex"'
        )
    else:
        cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]
        shown = "curl -LsSf https://astral.sh/uv/install.sh | sh"
    # リモートスクリプトを取得してそのまま実行する high-risk 操作。承認ゲート必須。
    _gate(
        "uv (Python パッケージ管理) を公式リモートインストーラで導入",
        [shown],
        note=(
            "リモートスクリプトを取得して即実行します (ネットワーク接続が必要)。"
            "手動導入する場合は上記コマンドを自分で実行してください。"
        ),
    )
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(
            "uv の自動インストールに失敗しました。手動で導入してください:\n"
            + _UV_INSTALL_HINT
        )
    uv = _find_uv()
    if not uv:
        sys.exit(
            "uv を導入しましたが検出できませんでした。新しいシェルで再実行するか、"
            "手動で PATH を通してください。"
        )
    return uv


def _install_source(requirements: Path) -> Path:
    """実際にインストールに使う依存記述を返す。

    隣に ``requirements.lock`` (ハッシュ固定のロックファイル) があればそれを優先し、
    決定論的・改竄検知つきのインストールにする。無ければ ``requirements.txt``
    (floor-pin) にフォールバックする。
    """
    lock = requirements.with_name("requirements.lock")
    return lock if lock.is_file() else requirements


def _requirements_hash(requirements: Path) -> str:
    return hashlib.sha256(requirements.read_bytes()).hexdigest()


def _ensure_venv(uv: str, venv: Path, venv_python: Path, boot_log: Path) -> None:
    """共有 venv が無ければ承認ゲートを通して作成する。"""
    if venv_python.exists():
        return
    # uv venv は要求バージョンが無ければ Python 本体をダウンロードしうる。
    _gate(
        f"共有仮想環境を作成 (必要なら Python 3.10+ を uv が調達): {venv}",
        [f"uv venv --python >=3.10 {venv}"],
        note="Python 本体が未導入の場合は uv がダウンロードします。",
    )
    _run_step(
        [uv, "venv", "--python", ">=3.10", str(venv)],
        boot_log,
        "共有仮想環境の作成",
    )


def _ensure_requirements(uv: str, venv: Path, venv_python: Path,
                         requirements: Path, skill: str, boot_log: Path,
                         note: str) -> None:
    """requirements(.lock) を marker 比較のうえ共有 venv へインストールする。

    依存記述が前回と同じなら再インストールしない (使うファイル自体をハッシュ)。
    """
    req_source = _install_source(requirements)
    marker = venv / f".{skill}.reqhash"
    want = _requirements_hash(req_source)
    have = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if have == want:
        return
    if req_source.name == "requirements.lock":
        note += " ロックファイル (ハッシュ固定) からインストールします。"
    # 依存インストール。承認ゲートで具体コマンドと規模を提示する。
    _gate(
        f"{skill} の依存パッケージを共有仮想環境へインストール",
        [f"uv pip install --python {venv_python} -r {req_source}"],
        note=note,
    )
    _run_step(
        [uv, "pip", "install", "--python", str(venv_python),
         "-r", str(req_source)],
        boot_log,
        f"{skill} 依存パッケージのインストール",
    )
    marker.write_text(want, encoding="utf-8")


def _launcher_hash(launcher_dir: Path) -> str:
    h = hashlib.sha256()
    for name in ("pyproject.toml", "skill_launcher.py"):
        h.update((launcher_dir / name).read_bytes())
    return h.hexdigest()


def _ensure_launcher(uv: str, venv: Path, venv_python: Path,
                     launcher_dir: Path, boot_log: Path) -> None:
    """venv コマンド (specdb / docextract) を提供する探索係パッケージを install する。

    launcher/ はスキル scripts/ に同梱される数十行のローカルパッケージで、
    install されるのは「cwd から上方探索して展開済みスキルへ委譲する」コマンド
    だけ。スキル本体は install しないので、zip 再展開での更新に追従できる。
    内容ハッシュを marker に記録し、変化が無ければ再インストールしない。
    """
    if not (launcher_dir / "pyproject.toml").is_file():
        return  # 同梱されていない構成では何もしない
    marker = venv / ".skill-launcher.hash"
    want = _launcher_hash(launcher_dir)
    have = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if have == want:
        return
    _gate(
        "スキル起動コマンド (specdb / docextract / docsummary) を共有仮想環境へインストール",
        [f"uv pip install --python {venv_python} {launcher_dir}"],
        note="同梱のローカルパッケージのみ (大きな依存のダウンロードはありません)。",
    )
    _run_step(
        [uv, "pip", "install", "--python", str(venv_python), str(launcher_dir)],
        boot_log,
        "スキル起動コマンドのインストール",
    )
    marker.write_text(want, encoding="utf-8")


def ensure_env(script: Path, requirements: Path, skill: str = "docextract") -> None:
    """共有 venv を用意し、その python で ``script`` を実行し直す。

    引数:
        script:       呼び出し元ランチャーの ``__file__`` (Path)。
        requirements: このスキルの requirements.txt (Path)。
        skill:        marker 名の名前空間に使うスキル名。
    """
    script = script.resolve()
    venv = _project_root(script) / ".venv"
    venv_python = _venv_python(venv)

    # 既に共有 venv の python で動いていれば bootstrap 不要。
    try:
        if Path(sys.prefix).resolve() == venv.resolve():
            return
    except OSError:
        pass
    if os.environ.get(_GUARD_ENV):
        # 再 exec 済み。ループ防止のためここで打ち切る。
        return

    uv = _find_uv() or _install_uv()

    # セットアップの冗長な出力 (Python 本体・依存の DL 進捗) を退避するログ先。
    boot_log = _bootstrap_log_path(venv.parent)

    _ensure_venv(uv, venv, venv_python, boot_log)
    _ensure_requirements(
        uv, venv, venv_python, requirements, skill, boot_log,
        note="初回は数百 MB のダウンロードが発生します"
             " (OCR/表検出モデルは実行時に別途取得)。",
    )

    # venv コマンド (specdb / docextract) の探索係を install する。
    _ensure_launcher(uv, venv, venv_python, script.parent / "launcher", boot_log)

    # 共有 venv の python で本体を実行し直す。os.exec* は Windows で呼び出し元が
    # 完了を待たない挙動になるため、subprocess で待ち合わせて終了コードを引き継ぐ。
    env = dict(os.environ)
    env[_GUARD_ENV] = "1"
    # 再 exec 後の子/孫プロセスでも cp932 由来の UnicodeEncodeError を防ぐ。
    # 利用者が明示設定していれば尊重する (setdefault)。
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        [str(venv_python), str(script), *sys.argv[1:]], env=env
    )
    sys.exit(completed.returncode)
