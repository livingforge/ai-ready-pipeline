"""LLM 接続設定の解決 — .env の読み込み・プロバイダ選択・秘密情報の非開示。

秘密情報 (API キー等) は **環境変数または `.env` ファイル**で受け取る。
このモジュールは値を stdout に出さない設計で、:func:`check_payload` は
「設定済みか / どこで設定されたか」だけを返す (値そのものは含めない)。
エージェントは `.env` を直接読まず、`docsummary config --check` の結果だけを見る。

解決の優先順位:

1. 実プロセスの環境変数 (``os.environ``)
2. `.env` ファイル (``--env-file`` > env ``DOCSUMMARY_ENV_FILE`` > cwd から上方探索)

プロバイダの選択は ``--provider`` > env ``DOCSUMMARY_PROVIDER`` (別名 ``LLM_PROVIDER``)
> 「API キーが設定されているプロバイダがちょうど 1 つならそれ」の順。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_FILE_ENV = "DOCSUMMARY_ENV_FILE"
PROVIDER_ENVS = ("DOCSUMMARY_PROVIDER", "LLM_PROVIDER")

# プロバイダ名の表記揺れを正規名へ寄せる (azure 系は normalize_provider が前方一致で吸収)。
_PROVIDER_ALIASES = {
    "openai": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "anthropic": "anthropic",
    "claude": "anthropic",
}


class SettingsError(Exception):
    """設定不備のユーザー向けエラー (次の一手を必ず文言に含める)。"""


@dataclass(frozen=True)
class VarSpec:
    """プロバイダが参照する環境変数 1 つの仕様。"""

    name: str
    required: bool = False
    secret: bool = False
    default: str | None = None


# 各プロバイダの接続情報。required が揃えば利用可能。
PROVIDERS: dict[str, list[VarSpec]] = {
    "openai": [
        VarSpec("OPENAI_API_KEY", required=True, secret=True),
        VarSpec("OPENAI_MODEL", default="gpt-4o-mini"),
        VarSpec("OPENAI_BASE_URL", default="https://api.openai.com/v1"),
    ],
    "azure": [
        VarSpec("AZURE_OPENAI_API_KEY", required=True, secret=True),
        VarSpec("AZURE_OPENAI_ENDPOINT", required=True),
        VarSpec("AZURE_OPENAI_DEPLOYMENT", required=True),
        VarSpec("AZURE_OPENAI_API_VERSION", default="2024-10-21"),
    ],
    "gemini": [
        VarSpec("GEMINI_API_KEY", required=True, secret=True),
        VarSpec("GEMINI_MODEL", default="gemini-2.0-flash"),
        VarSpec("GEMINI_BASE_URL",
                default="https://generativelanguage.googleapis.com/v1beta"),
    ],
    "anthropic": [
        VarSpec("ANTHROPIC_API_KEY", required=True, secret=True),
        VarSpec("ANTHROPIC_MODEL", default="claude-opus-4-8"),
        VarSpec("ANTHROPIC_BASE_URL", default="https://api.anthropic.com"),
        VarSpec("ANTHROPIC_VERSION", default="2023-06-01"),
    ],
}

# 各プロバイダの「モデル指定」に相当する変数 (--model で上書きされる)。
MODEL_VARS = {
    "openai": "OPENAI_MODEL",
    "azure": "AZURE_OPENAI_DEPLOYMENT",
    "gemini": "GEMINI_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
}


def parse_env_file(path: Path) -> dict[str, str]:
    """`.env` を KEY=VALUE の辞書として読む (dotenv の最小互換)。

    - `#` 始まり・空行は無視。行頭の ``export `` は剥がす
    - 値の外側の引用符 (' / ") は 1 組だけ剥がす
    - 壊れた行 (= が無い等) は黙って読み飛ばす (fail-open。設定ミスで止めない)
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def find_env_file(explicit: str | Path | None = None,
                  start: Path | None = None) -> Path | None:
    """使用する `.env` を解決する。--env-file > env 指定 > cwd から上方探索。"""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SettingsError(
                f"--env-file に指定されたファイルがありません: {p}。"
                " `docsummary config --init` で雛形を作れます"
            )
        return p
    env_path = os.environ.get(ENV_FILE_ENV)
    if env_path:
        p = Path(env_path)
        return p if p.is_file() else None
    base = (start or Path.cwd()).resolve()
    for parent in [base, *base.parents]:
        cand = parent / ".env"
        if cand.is_file():
            return cand
    return None


@dataclass
class Settings:
    """環境変数 + .env を重ねた設定ビュー (os.environ は変更しない)。"""

    env_file: Path | None = None
    file_values: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        path = find_env_file(env_file)
        return cls(env_file=path,
                   file_values=parse_env_file(path) if path else {})

    def get(self, name: str) -> str | None:
        value = os.environ.get(name)
        if value is not None and value != "":
            return value
        value = self.file_values.get(name)
        return value if value else None

    def source_of(self, name: str) -> str | None:
        """変数の由来: 'env' / 'file' / None (値は返さない)。"""
        if os.environ.get(name):
            return "env"
        if self.file_values.get(name):
            return "file"
        return None


def normalize_provider(raw: str) -> str:
    key = "".join(c for c in raw.strip().lower() if c.isalnum())
    # azureopenai / azure openai / azure-openai などをまとめて吸収する。
    if key.startswith("azure"):
        return "azure"
    if key in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[key]
    raise SettingsError(
        f"プロバイダ '{raw}' は未対応です。"
        f" 次から選んでください: {', '.join(PROVIDERS)}"
    )


def _configured_providers(settings: Settings) -> list[str]:
    """required の変数がすべて設定済みのプロバイダ一覧。"""
    ready = []
    for name, specs in PROVIDERS.items():
        if all(settings.get(s.name) for s in specs if s.required):
            ready.append(name)
    return ready


def resolve_provider(settings: Settings, explicit: str | None = None) -> str:
    """使用プロバイダを決める。決められないときは次の一手つきで拒否する。"""
    if explicit:
        return normalize_provider(explicit)
    for env_name in PROVIDER_ENVS:
        value = settings.get(env_name)
        if value:
            return normalize_provider(value)
    ready = _configured_providers(settings)
    if len(ready) == 1:
        return ready[0]
    if not ready:
        raise SettingsError(
            "LLM の接続設定が見つかりません。`.env` (または環境変数) に API キーを"
            " 設定してください。雛形の作成: docsummary config --init、"
            " 状態確認: docsummary config --check"
        )
    raise SettingsError(
        f"複数のプロバイダが設定済みです ({', '.join(ready)})。"
        " --provider か環境変数 DOCSUMMARY_PROVIDER でどれを使うか指定してください"
    )


@dataclass
class LLMConfig:
    """プロバイダ 1 つ分の解決済み接続情報 (secret を含む。表示・保存しない)。"""

    provider: str
    model: str
    values: dict[str, str]

    def __repr__(self) -> str:  # 事故で repr がログに乗っても秘密を出さない
        return f"LLMConfig(provider={self.provider!r}, model={self.model!r})"


def resolve_config(settings: Settings, provider: str | None = None,
                   model: str | None = None) -> LLMConfig:
    """プロバイダを決定し、必須変数を検証して接続情報を返す。"""
    name = resolve_provider(settings, provider)
    values: dict[str, str] = {}
    missing: list[str] = []
    for spec in PROVIDERS[name]:
        value = settings.get(spec.name) or spec.default
        if spec.required and not value:
            missing.append(spec.name)
        if value:
            values[spec.name] = value
    if missing:
        hint = f" (.env: {settings.env_file})" if settings.env_file else ""
        raise SettingsError(
            f"プロバイダ {name} に必要な設定が不足しています:"
            f" {', '.join(missing)}{hint}。"
            " `docsummary config --init` で雛形を作り、値を設定してください"
        )
    resolved_model = model or values.get(MODEL_VARS[name], "")
    return LLMConfig(provider=name, model=resolved_model, values=values)


def check_payload(settings: Settings, provider: str | None = None) -> dict:
    """`config --check` 用の状態レポート。**秘密の値は一切含めない。**"""
    providers: dict[str, dict] = {}
    for name, specs in PROVIDERS.items():
        vars_state = []
        missing = []
        for spec in specs:
            source = settings.source_of(spec.name)
            vars_state.append({
                "name": spec.name,
                "required": spec.required,
                "secret": spec.secret,
                "set": source is not None,
                "source": source,
                "default": None if (spec.secret or spec.default is None)
                else spec.default,
            })
            if spec.required and source is None:
                missing.append(spec.name)
        providers[name] = {"configured": not missing, "missing": missing,
                           "vars": vars_state}
    try:
        selected: str | None = resolve_provider(settings, provider)
        error = None
    except SettingsError as e:
        selected, error = None, str(e)
    return {
        "env_file": str(settings.env_file) if settings.env_file else None,
        "selected_provider": selected,
        "selection_error": error,
        "providers": providers,
    }


# `config --init` が書き出す雛形。値はすべて空/プレースホルダで秘密を含まない。
ENV_TEMPLATE = """\
# docsummary の LLM 接続設定 (.env)
# 使うプロバイダ 1 つ分のキーだけ埋めればよい。複数埋めた場合は
# DOCSUMMARY_PROVIDER でどれを使うか指定する (openai / azure / gemini / anthropic)。
# このファイルは秘密情報を含むため、コミットしないこと (.gitignore に追加する)。

# DOCSUMMARY_PROVIDER=anthropic

# --- OpenAI ---
# OPENAI_API_KEY=
# OPENAI_MODEL=gpt-4o-mini
# OPENAI_BASE_URL=https://api.openai.com/v1

# --- Azure OpenAI ---
# AZURE_OPENAI_API_KEY=
# AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
# AZURE_OPENAI_DEPLOYMENT=
# AZURE_OPENAI_API_VERSION=2024-10-21

# --- Google Gemini ---
# GEMINI_API_KEY=
# GEMINI_MODEL=gemini-2.0-flash

# --- Anthropic ---
# ANTHROPIC_API_KEY=
# ANTHROPIC_MODEL=claude-opus-4-8
"""
