#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gateway.py -- Vercel AI Gateway 経由で、読み比べの生成役・判定役を動かす小道具（標準ライブラリのみ）。

run_blind_eval.py の --generator / --judge に渡して使う。依頼文は標準入力で受け、応答を標準出力に出す。

  判定役（別の系列のモデルで読み直す。ファイルには触れない、ただの 1 問 1 答）:
    --judge "python -X utf8 <絶対パス>/evals/gateway.py chat --model google/gemini-3.1-pro-preview"

  生成役（Claude Code をゲートウェイ経由で動かす。スキルの系統は作業ディレクトリの SKILL.md を読める）:
    --generator "python -X utf8 <絶対パス>/evals/gateway.py claude-code --model anthropic/claude-sonnet-5"

  残高の確認:
    python -X utf8 evals/gateway.py credits

鍵の置き場所: 環境変数 AI_GATEWAY_API_KEY。無ければ、環境変数 AI_GATEWAY_KEY_FILE か ~/.ai-gateway-key が指すファイル（中身は鍵だけ）。
リポジトリの中の鍵ファイルは読まない（誤ってコミットしないため）。この道具が外へ出す文字列（応答、エラー、ログ、使用量の記録）は、
切り詰めや JSON 化の前に、鍵の文字列を <key> に置き換える。

入れ子の Claude Code について、保証できることと、できないこと:
  - 道具は Read / Glob / Grep / Write / Edit / Bash だけ。Bash で確認なしに通るのは、スキルの検査スクリプト（scripts/novel_lint.py、
    scripts/count_chars.py）の実行と、cd / ls / cat / head / tail / wc / echo / printf だけ。ファイルの作成と編集は、権限モード acceptEdits により
    作業ディレクトリの中だけ通る。それ以外は確認が要る扱いになり、-p では拒否される。
  - これは Claude Code の権限設定による制限で、OS の隔離ではない。--allow-any-python を付けると任意の python が通り、python の中からは
    作業ディレクトリの外への書き込みも、環境変数の鍵を使った直接の API 呼び出し（--max-budget-usd の集計の外）もできてしまう。
    既定の設定でも、鍵は入れ子の Claude Code の環境変数にあり、許可した道具から読める範囲にある。信頼できる依頼文とスキルに対してだけ使い、
    費用の上限はゲートウェイの側（Vercel の予算設定）にも置くこと。
  - 制限時間を過ぎたら、入れ子の Claude Code を子孫のプロセスごと終了させる。

使った量は --usage-log（既定は環境変数 GATEWAY_USAGE_LOG）の JSON Lines に 1 行ずつ足す。
終了コード: 0 = 成功 / 1 = 呼び出しの失敗 / 2 = 設定の誤り（鍵が無いなど）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://ai-gateway.vercel.sh"
REPO = Path(__file__).resolve().parents[1]
# 入れ子で動かす Claude Code に、この会話の側の設定を引き継がせないための環境変数
NESTED_ENV_DROP = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
DEFAULT_TOOLS = "Read,Glob,Grep,Bash,Write,Edit"
READ_ONLY_COMMANDS = ("cd", "ls", "cat", "head", "tail", "wc", "echo", "printf")
SKILL_SCRIPTS = ("novel_lint.py", "count_chars.py")
_KEY = ""      # redact() が伏せる文字列。load_key() が入れる


class GatewayError(Exception):
    """設定の誤り。メッセージを表示して終了コード 2 で終わる。"""


def redact(text) -> str:
    """外へ出す文字列から鍵を伏せる。切り詰めや JSON 化より前に通すこと（切れた鍵の断片を残さないため）。"""
    text = text if isinstance(text, str) else str(text)
    return text.replace(_KEY, "<key>") if _KEY else text


def say(text, stream=None) -> None:
    (stream or sys.stdout).write(redact(text))


def load_key() -> str:
    global _KEY
    key = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
    if not key:
        path = Path(os.environ.get("AI_GATEWAY_KEY_FILE") or Path.home() / ".ai-gateway-key").expanduser()
        if path.is_file():
            resolved = path.resolve()
            if resolved == REPO or REPO in resolved.parents:
                raise GatewayError(f"鍵ファイル {path} がリポジトリの中にある。コミットされる場所に鍵を置かない。")
            key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise GatewayError("AI Gateway の鍵が無い。環境変数 AI_GATEWAY_API_KEY を設定するか、鍵だけを書いたファイルを ~/.ai-gateway-key に置く"
                           "（別の場所なら AI_GATEWAY_KEY_FILE で指す）。")
    _KEY = key
    return key


def log_usage(path, record: dict) -> None:
    path = path or os.environ.get("GATEWAY_USAGE_LOG")
    if not path:
        return
    record = dict(record, time=datetime.datetime.now().isoformat(timespec="seconds"))
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(redact(json.dumps(record, ensure_ascii=False)) + "\n")


def request_json(url: str, key: str, payload=None, timeout: int = 600, retries: int = 3) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    last = ""
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = redact(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")[:400]
            if e.code not in (408, 409, 429) and e.code < 500:
                break      # 鍵の誤りやモデル名の誤りは、繰り返しても直らない
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = redact(f"{type(e).__name__}: {e}")[:400]
        if attempt < retries:
            time.sleep(5 * attempt)
    raise RuntimeError(last)


def cmd_chat(args) -> int:
    key = load_key()
    prompt = sys.stdin.buffer.read().decode("utf-8")
    payload = {"model": args.model, "messages": [{"role": "user", "content": prompt}]}
    if args.max_tokens:
        payload["max_tokens"] = args.max_tokens
    if args.temperature is not None:
        payload["temperature"] = args.temperature
    if args.search:      # ゲートウェイの側で検索を実行し、結果をモデルの文脈に足してから答えさせる（どのモデルでも使える）
        payload["tools"] = [{"type": f"vercel:{args.search}_search"}]
        payload["tool_choice"] = "required" if args.search_required else "auto"
    started = time.time()
    try:
        res = request_json(f"{BASE_URL}/v1/chat/completions", key, payload, timeout=args.timeout)
        text = res["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("応答の本文が文字列ではない")
    except (RuntimeError, KeyError, IndexError, TypeError) as e:
        say(f"エラー: {args.model} の呼び出しに失敗した: {redact(e)[:400]}\n", sys.stderr)
        log_usage(args.usage_log, {"kind": "chat", "model": args.model, "ok": False, "error": redact(e)[:200]})
        return 1
    meta = ((res["choices"][0]["message"].get("provider_metadata") or {}).get("gateway") or {}) if args.search else {}
    log_usage(args.usage_log, {"kind": "chat", "model": args.model, "ok": True, "seconds": round(time.time() - started, 1),
                               "usage": res.get("usage"), "response_model": res.get("model"), "search": meta.get("gatewayToolCalls")})
    if args.search:
        say(f"[search] {json.dumps(meta.get('gatewayToolCalls'), ensure_ascii=False)}" + chr(10), sys.stderr)
    say(text if text.endswith("\n") else text + "\n")
    return 0


def default_allowed(cwd: Path, any_python: bool) -> list:
    """確認なしで通す道具。python は、作業ディレクトリにあるスキルの検査スクリプトの実行だけ（呼び方の揺れ: 相対・絶対、/ と \\、引用符の有無）。"""
    allowed = ["Read", "Glob", "Grep"] + [f"Bash({c}:*)" for c in READ_ONLY_COMMANDS]
    if any_python:
        return allowed + ["Bash(python:*)", "Bash(python3:*)"]
    posix, native = cwd.as_posix(), str(cwd)
    for script in SKILL_SCRIPTS:
        targets = {f"scripts/{script}", f"./scripts/{script}", f"{posix}/scripts/{script}", f"{native}{os.sep}scripts{os.sep}{script}"}
        for target in sorted(targets):
            for shown in (target, f'"{target}"'):
                for exe in ("python", "python3"):
                    allowed += [f"Bash({exe} {shown}:*)", f"Bash({exe} -X utf8 {shown}:*)"]
    return allowed


def claude_command(args, exe: str, cwd: Path) -> list:
    allowed = args.allowed_tool or default_allowed(cwd, args.allow_any_python)
    cmd = [exe, "-p", "--model", args.model, "--output-format", "stream-json", "--verbose", "--tools", args.tools,
           "--allowedTools", *allowed, "--permission-mode", "acceptEdits",
           "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
           "--max-budget-usd", str(args.max_budget_usd)]
    if args.bare:
        cmd.append("--bare")
    return cmd


def claude_env(key: str, auth: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in NESTED_ENV_DROP and k not in ("AI_GATEWAY_API_KEY", "AI_GATEWAY_KEY_FILE")}
    env["ANTHROPIC_BASE_URL"] = f"{BASE_URL}/claude-code"
    # Vercel の手順どおり、鍵は ANTHROPIC_AUTH_TOKEN に入れ、ANTHROPIC_API_KEY は空にする。--bare は API キーしか読まないので、そのときは逆にする
    env["ANTHROPIC_AUTH_TOKEN"] = key if auth == "token" else ""
    env["ANTHROPIC_API_KEY"] = key if auth == "api-key" else ""
    if auth == "api-key":
        env.pop("ANTHROPIC_AUTH_TOKEN")
    return env


def parse_stream(raw: str) -> tuple:
    """claude -p --output-format stream-json の出力から、(最後の result イベント, 使った道具の一覧) を取り出す。
    イベントの形は種類ごとに違う（message が文字列のものもある）ので、型を確かめながら読む。道具の入力は、鍵を伏せてから切り詰める。"""
    res, tools = {}, []
    for line in raw.split("\n"):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            res = event
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tools.append(f"[tool] {block.get('name')} {redact(json.dumps(block.get('input'), ensure_ascii=False))[:300]}")
    return res, tools


def run_tree(cmd: list, stdin: bytes, timeout: int, env: dict):
    """子プロセスを動かし、制限時間を過ぎたら子孫ごと終了させる。(終了コード, stdout, stderr) を返す。時間切れは終了コード None。"""
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **kwargs)
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        proc.kill()
        out, err = proc.communicate()
        return None, out, err


def cmd_claude_code(args) -> int:
    key = load_key()
    exe = shutil.which("claude")
    if exe is None:
        raise GatewayError("claude コマンドが見つからない。")
    prompt = sys.stdin.buffer.read()
    started = time.time()
    code, out, err = run_tree(claude_command(args, exe, Path.cwd()), prompt, args.timeout, claude_env(key, "api-key" if args.bare else "token"))
    raw = redact(out.decode("utf-8", "replace"))
    stderr = redact(err.decode("utf-8", "replace"))
    raw_dir = os.environ.get("GATEWAY_RAW_DIR")
    if raw_dir:      # 生の出力を残す。読み取りに不具合があっても、費用をかけた生成を失わない
        Path(raw_dir).mkdir(parents=True, exist_ok=True)
        (Path(raw_dir) / f"claude-{int(started)}-{os.getpid()}.jsonl").write_text(raw, encoding="utf-8", newline="\n")
    res, tools = parse_stream(raw)
    text = res.get("result") if isinstance(res.get("result"), str) else ""
    failed = code != 0 or bool(res.get("is_error")) or not text.strip()
    log_usage(args.usage_log, {"kind": "claude-code", "model": args.model, "ok": not failed, "seconds": round(time.time() - started, 1),
                               "timeout": code is None, "cost_usd": res.get("total_cost_usd"), "turns": res.get("num_turns"), "usage": res.get("usage")})
    # 使った道具をログに残す（スキルなしの系統がスキルのファイルを読んでいないか、検査を実際に走らせたかを、あとで確かめられるように）
    say((stderr + "\n" + "\n".join(tools) + "\n")[-30000:], sys.stderr)
    if failed:
        why = "制限時間を過ぎたので、子孫のプロセスごと終了させた" if code is None else f"exit={code}"
        say(f"エラー: claude の実行に失敗した（{why}）: {(text or raw[-400:])[:400]}\n", sys.stderr)
        return 1
    say(f"\n[claude-code] turns={res.get('num_turns')} cost_usd={res.get('total_cost_usd')}\n", sys.stderr)
    say(text if text.endswith("\n") else text + "\n")
    return 0


def cmd_credits(args) -> int:
    try:
        res = request_json(f"{BASE_URL}/v1/credits", load_key(), timeout=60, retries=2)
    except RuntimeError as e:
        say(f"エラー: 残高を取得できなかった: {e}\n", sys.stderr)
        return 1
    say(json.dumps(res, ensure_ascii=False) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)
    chat = sub.add_parser("chat", help="標準入力の依頼文を 1 問 1 答で投げ、応答を標準出力に出す")
    chat.add_argument("--model", required=True, help="ゲートウェイのモデル名（例: google/gemini-3.1-pro-preview）")
    chat.add_argument("--max-tokens", type=int, default=0)
    chat.add_argument("--temperature", type=float, default=None)
    chat.add_argument("--search", choices=["perplexity", "exa", "parallel", "tako"], help="ゲートウェイのウェブ検索を使わせる（1,000 回あたり数ドルの別料金）")
    chat.add_argument("--search-required", action="store_true", help="答える前に必ず 1 回は検索させる")
    cc = sub.add_parser("claude-code", help="Claude Code（claude -p）をゲートウェイ経由で、いまの作業ディレクトリで動かす")
    cc.add_argument("--model", required=True, help="例: anthropic/claude-sonnet-5")
    cc.add_argument("--tools", default=DEFAULT_TOOLS, help=f"使わせる道具（既定 {DEFAULT_TOOLS}）")
    cc.add_argument("--allowed-tool", action="append", default=None, help="確認なしで許す道具を自分で指定する。複数指定可（既定の許可リストを置き換える）")
    cc.add_argument("--allow-any-python", action="store_true",
                    help="任意の python の実行を確認なしで通す。python の中からは作業ディレクトリの外への書き込みも、鍵を使った直接の API 呼び出しもできる。信頼できる入力にだけ使う")
    cc.add_argument("--max-budget-usd", type=float, default=2.0, help="1 回の実行で Claude Code が使ってよい上限（既定 2 ドル。ゲートウェイの側の予算設定の代わりにはならない）")
    cc.add_argument("--bare", action="store_true", help="claude --bare で動かす（CLAUDE.md・メモリ・フック・プラグインを読まない）")
    sub.add_parser("credits", help="残高を表示する")
    for sp in (chat, cc):
        sp.add_argument("--timeout", type=int, default=1500)
        sp.add_argument("--usage-log", default=None, help="使った量を足していく JSON Lines のファイル")
    return ap


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return {"chat": cmd_chat, "claude-code": cmd_claude_code, "credits": cmd_credits}[args.step](args)
    except GatewayError as e:
        say(f"エラー: {e}\n", sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
