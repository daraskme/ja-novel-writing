# -*- coding: utf-8 -*-
"""threshold_measure.py -- 閾値較正用の計測（fp23_regex_test.py の統計部を拡張）。
入力: 1 段落 1 行のプレーンテキスト（青空文庫のルビ・注記は除去済みを想定）。
出力: JSON（論点別の実測値）。標準ライブラリのみ。
usage: python threshold_measure.py file1.txt [file2.txt ...]
"""
import json
import re
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")

WS = re.compile(r"[\s　]")
QUOTE = re.compile(r"「[^「」]*」")
MARK = ""
JP = re.compile(r"[ぁ-んァ-ヶ一-龥]")
KANJI = re.compile(r"[一-龥々]")
SENT_SPLIT = re.compile(r"(?<=[。！？!?])[」』）)]*")
TAIL = re.compile(r"[。！？!?…―—─\s　」』）)]+$")

P = {
    "simile_marked": r"(まるで|あたかも|さながら)[^。\n]{1,40}?(よう|みたい|ごと[くき])",
    "simile_any": r"(かの)?よう(な|に|だ|で)|みたい(な|に|だ)",
    "marude": r"まるで",
    "kanjita": r"[をがも]感じ(た|て|る|ながら|させ|取)",
    "filter_verbs": r"(と思っ|と思う|[をが]感じ|気がし|に気づ|に気付|が見え|が聞こえ|のが(見え|聞こえ|わか|分か))",
    "dokoka": r"どこか(?![にへでをがはらの]|から|まで|遠く)",
    "soft_adv": r"(静かに|そっと|ゆっくりと?|かすかに|微かに|わずかに|小さく|穏やかに|優しく|柔らかく)",
    "degree_adv": r"(とても|非常に|すごく|ひどく|かなり|ますます|あまりにも?|たいへん|大変|実に|少し|すこし|やや|ちょっと)",
    "futo": r"(ふと|思わず|不意に|いつの間にか|気づけば|気がつけば|知らず知らず)",
    "hedge": r"(ような気がし|気がした|のかもしれな|ように(思え|見え|感じ))",
    "not_x_but_y": r"(では|じゃ)な(い|かった)。[^。\n]{0,30}(だ|だった|である)。",
    "pronoun": r"(彼女?|彼ら)(は|が|の|を|に|も|と)",
    "conj_head": r"(?:^|(?<=[。」\n]))[ 　]*(しかし|だが|そして|それでも|けれども?|だから|そのため|また|さらに|つまり|すると|やがて|それから|ところが)[、,]?",
    "taigen_regex": r"[一-龥々ァ-ヶー]。",
    "nodatta": r"の(だった|である|であった)。",
    "emotion_adj": r"(悲し|寂し|嬉し|切な|懐かし|愛おし|虚し|悔し|恐ろし|苦し)(い|かった|くて|くな|さ|み|げ)",
    "emotion_noun": r"(不安|安堵|孤独|後悔|絶望|希望|幸福|喜び|怒り|恐怖|罪悪感|違和感|温もり|温かさ|焦燥)(が|を|に|の|と|で|だ)",
    "body_template": r"息を(呑|飲|の)ん|胸が(締め付け|熱く|痛|ざわ|高鳴)|胸の奥(が|で|に)|目頭が熱|涙が(頬を|こぼれ|溢れ|あふれ|滲)|拳を(握|固)|唇を噛|(声|手|肩|指先?|膝)が震え|視線を(落と|逸ら|そら)|目を(伏せ|見開|細め)|喉の奥|心臓が(跳ね|高鳴|早鐘)|鼓動が",
}
C = {k: re.compile(v, re.M) for k, v in P.items()}
DASH = re.compile(r"[―—─]{1,}")
ELL = re.compile(r"…+|・{3,}|\.{3,}")
PARA_CONJ = re.compile(r"^(しかし|だが|そして|それでも|けれども?|だから|そのため|また|さらに|つまり|すると|やがて|それから|ところが)")


def nws(s):
    return len(WS.sub("", s))


def strip_quotes(s, repl=""):
    prev = None
    while prev != s:
        prev = s
        s = QUOTE.sub(repl, s)
    return s


def ending_class(sent):
    core = TAIL.sub("", sent)
    if not core:
        return "other"
    if re.search(r"(です|ます|でした|ました|ません|でしょう|ましょう)$", core):
        return "masu"
    if re.search(r"(た|んだ|いだ)$", core):
        return "ta"
    if re.search(r"(だ|である)$", core):
        return "da"
    if re.search(r"(ない|ぬ|ず)$", core):
        return "nai"
    if re.search(r"い$", core):
        return "i"
    if re.search(r"[うくぐすつづぬぶむる]$", core):
        return "ru"
    if re.search(r"[一-龥々ァ-ヶー]$", core):
        return "taigen"
    return "other"


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(q * (len(sorted_vals) - 1) + 0.5)))  # 四捨五入（JS 版と一致させる）
    return sorted_vals[i]


def cv(vals):
    if len(vals) < 2:
        return None
    m = statistics.mean(vals)
    return statistics.pstdev(vals) / m if m else None


def r2(x):
    return None if x is None else round(x, 3)


def runs(seq, pred):
    """seq の中で pred(x) が真の連続長のリスト。"""
    out, run = [], 0
    for x in seq:
        if pred(x):
            run += 1
        else:
            if run:
                out.append(run)
            run = 0
    if run:
        out.append(run)
    return out


def same_class_runs(tokens):
    """tokens: 文末クラス列（'D' は台詞段落による切断）。同一クラス連続長を (class,len) で返す。"""
    out, cur, n = [], None, 0
    for t in tokens:
        if t == "D":
            if cur is not None:
                out.append((cur, n))
            cur, n = None, 0
            continue
        if t == cur:
            n += 1
        else:
            if cur is not None:
                out.append((cur, n))
            cur, n = t, 1
    if cur is not None:
        out.append((cur, n))
    return out


def measure(text):
    paras = []
    for line in text.splitlines():
        s = line.rstrip()
        if not s.strip() or not JP.search(s):
            continue
        paras.append(s)
    total_chars = sum(nws(p) for p in paras)

    tokensA, tokensB = [], []  # A: 台詞無視 / B: 純台詞段落で切断
    sent_lens, commas = [], []
    narr_text_parts = []
    N_lens, one_sent_N = [], 0
    all_onesent = 0
    dlg_run_lens, cur_run = [], 0
    dialogues = []
    n_indent = n_nonquote = 0
    para_conj = 0
    for p in paras:
        body = p.lstrip(" 　")
        starts_quote = body.startswith(("「", "『"))
        if not starts_quote:
            n_nonquote += 1
            if p.startswith("　"):
                n_indent += 1
        dialogues += [m.group(0)[1:-1] for m in QUOTE.finditer(body)]
        marked = strip_quotes(body, MARK)
        n_utt = marked.count(MARK)
        narr = marked.replace(MARK, "")
        if len(re.findall(r"[。！？!?]", body)) <= 1:
            all_onesent += 1
        if nws(narr) == 0:  # 純台詞段落
            cur_run += max(1, n_utt)
            tokensB.append("D")
            continue
        if cur_run:
            dlg_run_lens.append(cur_run)
        cur_run = 0
        narr_text_parts.append(narr)
        sents = [x for x in SENT_SPLIT.split(narr) if nws(x) >= 2]
        for s in sents:
            c = ending_class(s)
            tokensA.append(c)
            tokensB.append(c)
            sent_lens.append(nws(s))
            commas.append(s.count("、"))
        if not starts_quote:
            N_lens.append(nws(p))
            if len(sents) <= 1:
                one_sent_N += 1
            if PARA_CONJ.match(body):
                para_conj += 1
    if cur_run:
        dlg_run_lens.append(cur_run)

    narr_text = "\n".join(narr_text_parts)
    narr_chars = nws(narr_text)
    dlg_chars = sum(nws(d) for d in dialogues)
    k_n = 1000 / narr_chars if narr_chars else 0
    k_t = 1000 / total_chars if total_chars else 0

    out = {
        "chars": total_chars,
        "narr_chars": narr_chars,
        "dialogue_ratio": r2(dlg_chars / total_chars if total_chars else None),
        "kanji_ratio": r2(len(KANJI.findall("".join(paras))) / total_chars if total_chars else None),
        "n_paras": len(paras),
        "n_sents": len(sent_lens),
    }

    # (2) 文長
    sl = sorted(sent_lens)
    blocks = [sent_lens[i:i + 20] for i in range(0, len(sent_lens) - 19, 20)]
    bcv = sorted(x for x in (cv(b) for b in blocks) if x is not None)
    mid_runs = runs(sent_lens, lambda n: 30 <= n <= 45)
    out["sent_len"] = {
        "mean": r2(statistics.mean(sl)) if sl else None,
        "median": pct(sl, 0.5),
        "p90": pct(sl, 0.9),
        "max": sl[-1] if sl else None,
        "cv_whole": r2(cv(sent_lens)),
        "block20_cv_p10": r2(pct(bcv, 0.1)),
        "block20_cv_median": r2(pct(bcv, 0.5)),
        "block20_n": len(bcv),
        "block20_share_lt_0.40": r2(sum(x < 0.40 for x in bcv) / len(bcv)) if bcv else None,
        "block20_share_lt_0.42": r2(sum(x < 0.42 for x in bcv) / len(bcv)) if bcv else None,
        "block20_share_lt_0.50": r2(sum(x < 0.50 for x in bcv) / len(bcv)) if bcv else None,
        "share_le10": r2(sum(n <= 10 for n in sl) / len(sl)) if sl else None,
        "share_gt60": r2(sum(n > 60 for n in sl) / len(sl)) if sl else None,
        "share_ge80": r2(sum(n >= 80 for n in sl) / len(sl)) if sl else None,
        "share_gt100": r2(sum(n > 100 for n in sl) / len(sl)) if sl else None,
        "mid30_45_runs_ge5_per10k": r2(sum(r >= 5 for r in mid_runs) * 10 * k_n),
        "commas_per_sent": r2(statistics.mean(commas)) if commas else None,
        "share_commas_ge4": r2(sum(c >= 4 for c in commas) / len(commas)) if commas else None,
    }

    # (1) 文末連続
    def run_stats(tokens):
        rs = same_class_runs(tokens)
        any_l = [n for _, n in rs]
        ta_l = [n for c, n in rs if c == "ta"]
        d = {"max_any": max(any_l) if any_l else 0, "max_ta": max(ta_l) if ta_l else 0}
        for k in (3, 4, 5, 6, 8):
            d[f"any_ge{k}_per10k"] = r2(sum(n >= k for n in any_l) * 10 * k_n)
            d[f"ta_ge{k}_per10k"] = r2(sum(n >= k for n in ta_l) * 10 * k_n)
        ns = sum(any_l)
        d["share_sents_in_run_ge4"] = r2(sum(n for n in any_l if n >= 4) / ns) if ns else None
        d["share_sents_in_run_ge5"] = r2(sum(n for n in any_l if n >= 5) / ns) if ns else None
        return d

    cls_count = {}
    for t in tokensA:
        cls_count[t] = cls_count.get(t, 0) + 1
    nA = len(tokensA) or 1
    tb = [tokensA[i:i + 20] for i in range(0, len(tokensA) - 19, 20)]
    ta_rates = [sum(t == "ta" for t in b) / len(b) for b in tb]
    out["ending"] = {
        "class_share": {k: r2(v / nA) for k, v in sorted(cls_count.items(), key=lambda kv: -kv[1])},
        "ta_rate": r2(cls_count.get("ta", 0) / nA),
        "block20_ta_rate_max": r2(max(ta_rates)) if ta_rates else None,
        "block20_share_ta_gt0.8": r2(sum(x > 0.8 for x in ta_rates) / len(ta_rates)) if ta_rates else None,
        "runs_ignore_dialogue": run_stats(tokensA),
        "runs_dialogue_breaks": run_stats(tokensB),
    }

    # (3) 体言止め
    tg_runs = runs(tokensA, lambda t: t == "taigen")
    out["taigen"] = {
        "per1000_narr": r2(cls_count.get("taigen", 0) * k_n),
        "regex_per1000_narr": r2(len(C["taigen_regex"].findall(narr_text)) * k_n),
        "runs_ge2_per10k": r2(sum(r >= 2 for r in tg_runs) * 10 * k_n),
        "runs_ge3_per10k": r2(sum(r >= 3 for r in tg_runs) * 10 * k_n),
        "max_run": max(tg_runs) if tg_runs else 0,
    }

    # (4) 段落
    nl = sorted(N_lens)
    pblocks = [N_lens[i:i + 10] for i in range(0, len(N_lens) - 9, 10)]
    pcv = sorted(x for x in (cv(b) for b in pblocks) if x is not None)
    out["para"] = {
        "n_narr_paras": len(N_lens),
        "mean": r2(statistics.mean(nl)) if nl else None,
        "median": pct(nl, 0.5),
        "p90": pct(nl, 0.9),
        "max": nl[-1] if nl else None,
        "share_gt150": r2(sum(n > 150 for n in nl) / len(nl)) if nl else None,
        "share_gt200": r2(sum(n > 200 for n in nl) / len(nl)) if nl else None,
        "share_gt250": r2(sum(n > 250 for n in nl) / len(nl)) if nl else None,
        "share_gt300": r2(sum(n > 300 for n in nl) / len(nl)) if nl else None,
        "share_gt400": r2(sum(n > 400 for n in nl) / len(nl)) if nl else None,
        "cv_whole": r2(cv(N_lens)),
        "block10_cv_median": r2(pct(pcv, 0.5)),
        "block10_share_lt_0.45": r2(sum(x < 0.45 for x in pcv) / len(pcv)) if pcv else None,
        "one_sent_rate_narr": r2(one_sent_N / len(N_lens)) if N_lens else None,
        "one_sent_rate_all": r2(all_onesent / len(paras)) if paras else None,
        "indent_rate_nonquote": r2(n_indent / n_nonquote) if n_nonquote else None,
        "para_head_conj_rate": r2(para_conj / len(N_lens)) if N_lens else None,
    }

    # (5) 比喩
    out["simile"] = {
        "marked_per2000_narr": r2(len(C["simile_marked"].findall(narr_text)) * 2 * k_n),
        "marude_per1000_narr": r2(len(C["marude"].findall(narr_text)) * k_n),
        "any_per1000_narr": r2(len(C["simile_any"].findall(narr_text)) * k_n),
    }

    # (6)(7) 会話
    flat = WS.sub("", "\n".join(paras).replace("\n", ""))
    pos = [m.start() for m in re.finditer("「", flat)]
    best, j = 0, 0
    for i in range(len(pos)):
        while pos[i] - pos[j] > 300:
            j += 1
        best = max(best, i - j + 1)
    dl = dlg_run_lens
    out["dialogue"] = {
        "n_utterances": len(dialogues),
        "utter_per1000": r2(len(dialogues) * k_t),
        "run_max": max(dl) if dl else 0,
        **{f"runs_ge{k}_per10k": r2(sum(n >= k for n in dl) * 10 * k_t) for k in (4, 5, 7, 8, 10, 12)},
        "max_utter_in_300chars": best,
        "short6_rate": r2(sum(nws(d) <= 6 for d in dialogues) / len(dialogues)) if dialogues else None,
        "long60_rate": r2(sum(nws(d) >= 60 for d in dialogues) / len(dialogues)) if dialogues else None,
        "ellipsis_start_rate": r2(sum(d.lstrip().startswith("…") for d in dialogues) / len(dialogues)) if dialogues else None,
    }

    # (8) 密度
    whole = "\n".join(paras)
    dens = {
        "dash_per1000_total": r2(len(DASH.findall(whole)) * k_t),
        "ellipsis_per1000_total": r2(len(ELL.findall(whole)) * k_t),
        "conj_head_per1000_narr": r2(len(C["conj_head"].findall(narr_text)) * k_n),
        "conj_head_share_of_sents": r2(len(C["conj_head"].findall(narr_text)) / nA),
    }
    for key in ("filter_verbs", "kanjita", "emotion_adj", "emotion_noun", "body_template", "soft_adv",
                "degree_adv", "futo", "dokoka", "hedge", "pronoun", "nodatta", "not_x_but_y"):
        dens[key + "_per1000_narr"] = r2(len(C[key].findall(narr_text)) * k_n)
    out["density"] = dens
    return out


if __name__ == "__main__":
    res = {}
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            res[path.replace("\\", "/").split("/")[-1]] = measure(f.read())
    print(json.dumps(res, ensure_ascii=False, indent=1))
