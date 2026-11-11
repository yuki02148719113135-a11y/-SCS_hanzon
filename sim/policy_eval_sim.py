#!/usr/bin/env python3
"""
ガードレール強制ポリシーを、IAMの評価ルールを簡略化したモデルで
100万リクエスト分判定し、「指定ガードレールなしでモデルを呼べてしまう
（すり抜け）」ケースが何件あるかを数える。

これはAWSの実際の評価エンジンではなく、公開ドキュメントのルールを
写したモデルである。モデルの前提:
  1. 当てはまる Deny が1つでもあれば「明示的拒否」
  2. Deny がなく、当てはまる Allow があれば「許可」
  3. どちらもなければ「暗黙的拒否」
  4. リクエストに条件キーが無いとき、StringEquals は偽、StringNotEquals は真
     （実機テストAで「ガードレールなし → 明示的拒否」になったことと一致）
SCP・アクセス許可の境界・セッションポリシーは扱わない。

使い方: python3 -I sim/policy_eval_sim.py [試行回数] [シード]
"""
import fnmatch
import json
import random
import sys
from collections import Counter
from pathlib import Path

ACCOUNT = "123456789012"
GR_ID = "0c8r3noed2cz"
OTHER_GR_ID = "zzzzotherzzz"
KEY = "bedrock:GuardrailIdentifier"
INVOKE_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"


def load_policy(name, pinned_version):
    """テンプレートのプレースホルダを埋めてポリシーを読み込む"""
    text = (POLICY_DIR / name).read_text()
    text = text.replace("${GR_ARN}", f"arn:aws:bedrock:us-east-1:{ACCOUNT}:guardrail/{GR_ID}")
    text = text.replace("${VERSION}", str(pinned_version))
    return json.loads(text)["Statement"]


ADMIN_ALLOW = {"Effect": "Allow", "Action": "*", "Resource": "*"}


def as_list(v):
    return v if isinstance(v, list) else [v]


def cond_ok(cond, ctx):
    for op, kv in (cond or {}).items():
        for key, expected in kv.items():
            expected = as_list(expected)
            present = key in ctx
            if op == "StringEquals":
                ok = present and ctx[key] in expected
            elif op == "StringNotEquals":
                ok = (not present) or ctx[key] not in expected
            else:
                raise ValueError(f"このモデルが扱わない演算子: {op}")
            if not ok:
                return False
    return True


def matches(stmt, action, resource, ctx):
    if not any(fnmatch.fnmatchcase(action, a) for a in as_list(stmt["Action"])):
        return False
    if not any(fnmatch.fnmatchcase(resource, r) for r in as_list(stmt["Resource"])):
        return False
    return cond_ok(stmt.get("Condition"), ctx)


def evaluate(statements, action, resource, ctx):
    hits = [s for s in statements if matches(s, action, resource, ctx)]
    if any(s["Effect"] == "Deny" for s in hits):
        return "明示的拒否"
    if any(s["Effect"] == "Allow" for s in hits):
        return "許可"
    return "暗黙的拒否"


def random_request(rng):
    action = rng.choice(INVOKE_ACTIONS)
    kind = rng.choice(["fm", "fm", "profile"])
    region = rng.choice(["us-east-1", "us-west-2", "ap-northeast-1"])
    if kind == "fm":
        resource = f"arn:aws:bedrock:{region}::foundation-model/amazon.nova-micro-v1:0"
    else:
        resource = f"arn:aws:bedrock:{region}:{ACCOUNT}:inference-profile/us.amazon.nova-micro-v1:0"
    gr = rng.choice(["なし", "自分", "他"])
    ver = rng.choice(["1", "2", "DRAFT"])
    ctx = {}
    if gr != "なし":
        gid = GR_ID if gr == "自分" else OTHER_GR_ID
        base = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:guardrail/{gid}"
        # DRAFTはARNにバージョンを付けない（AWS公式の例2に準拠）
        ctx[KEY] = base if ver == "DRAFT" else f"{base}:{ver}"
    return action, resource, ctx, gr, ver, region, kind


def run(trials, seed):
    rng = random.Random(seed)
    variants = {
        "元のポリシー（Denyはus-east-1の基盤モデルのみ）": "enforce-guardrail.template.json",
        "強化版（DenyのResourceを*に）": "enforce-guardrail-hardened.template.json",
    }
    report = {}
    for label, fname in variants.items():
        rng = random.Random(seed)
        bypass = Counter()
        totals = Counter()
        legit_allowed = 0
        for _ in range(trials):
            pinned = rng.choice([1, 2])
            admin = rng.random() < 0.5
            action, resource, ctx, gr, ver, region, kind = random_request(rng)
            stmts = load_policy_cached(fname, pinned) + ([ADMIN_ALLOW] if admin else [])
            result = evaluate(stmts, action, resource, ctx)
            totals[result] += 1
            legit = gr == "自分" and ver == str(pinned)
            if result == "許可" and not legit:
                bypass[(admin, kind, region)] += 1
            if result == "許可" and legit:
                legit_allowed += 1
        report[label] = {"totals": totals, "bypass": bypass, "legit_allowed": legit_allowed}
    return report


_cache = {}


def load_policy_cached(fname, pinned):
    k = (fname, pinned)
    if k not in _cache:
        _cache[k] = load_policy(fname, pinned)
    return _cache[k]


def replay_observed():
    """2026-11-11に実機で確かめた結果を、モデルが同じように判定するか確認する"""
    fm = "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
    base = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:guardrail/{GR_ID}"
    cases = [
        ("A ガードレールなし（固定:1）", 1, {}, "明示的拒否"),
        ("B バージョン1（固定:1）", 1, {KEY: f"{base}:1"}, "許可"),
        ("C 日本語の単語（固定:1・バージョン1）", 1, {KEY: f"{base}:1"}, "許可"),
        ("バージョン2（固定:1のまま）", 1, {KEY: f"{base}:2"}, "明示的拒否"),
        ("バージョン2（固定:2に更新後）", 2, {KEY: f"{base}:2"}, "許可"),
    ]
    rows = []
    for name, pinned, ctx, observed in cases:
        got = evaluate(load_policy_cached("enforce-guardrail.template.json", pinned),
                       "bedrock:InvokeModel", fm, ctx)
        rows.append((name, observed, got, got == observed))
    return rows


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    out = [f"# 100万回シミュレーション結果\n", f"- 試行回数: {trials:,}", f"- シード: {seed}",
           "- 1回ごとに、固定するバージョン（1か2）、管理者権限（Allow *）の有無、"
           "呼び出すアクション、リソース（基盤モデルか推論プロファイル）、リージョン、"
           "付けるガードレール（なし・自分・他）、バージョン（1・2・DRAFT）を無作為に選ぶ\n"]

    out.append("## 1. 実機の結果との一致\n")
    out.append("| テスト | 実機 | モデル | 一致 |\n|---|---|---|---|")
    rows = replay_observed()
    for name, obs, got, ok in rows:
        out.append(f"| {name} | {obs} | {got} | {'○' if ok else '×'} |")
    out.append("\nCは「IAMでは許可された（その後ガードレールが日本語を止められなかった）」の意味。"
               "単語フィルターの言語対応はIAMの外の話なので、このモデルでは扱わない。\n")

    report = run(trials, seed)
    out.append("## 2. すり抜け件数（指定ガードレール・指定バージョン以外で「許可」になった件数）\n")
    out.append("| ポリシー | 許可 | 明示的拒否 | 暗黙的拒否 | 正規の許可 | **すり抜け** |\n|---|---|---|---|---|---|")
    for label, r in report.items():
        t = r["totals"]
        out.append(f"| {label} | {t['許可']:,} | {t['明示的拒否']:,} | {t['暗黙的拒否']:,} | "
                   f"{r['legit_allowed']:,} | **{sum(r['bypass'].values()):,}** |")
    out.append("")
    for label, r in report.items():
        if r["bypass"]:
            out.append(f"### すり抜けの内訳：{label}\n")
            out.append("| 管理者権限 | リソース | リージョン | 件数 |\n|---|---|---|---|")
            for (admin, kind, region), n in sorted(r["bypass"].items(), key=lambda x: -x[1]):
                k = "基盤モデル" if kind == "fm" else "推論プロファイル"
                out.append(f"| {'あり' if admin else 'なし'} | {k} | {region} | {n:,} |")
            out.append("")
    text = "\n".join(out)
    print(text)
    Path(__file__).with_name("results.md").write_text(text + "\n")
    ok = all(r[3] for r in rows)
    hardened = report["強化版（DenyのResourceを*に）"]
    sys.exit(0 if ok and sum(hardened["bypass"].values()) == 0 else 1)


if __name__ == "__main__":
    main()
