# 実機テストの結果（2026-11-11、us-east-1、Amazon Nova Micro）

アカウントIDは `123456789012` に置き換えている。手動で1つずつ実行した結果で、`scripts/run-lab.sh` はこの手順をまとめたもの。

## A：ガードレールなし（ポリシーは`:1`に固定）
```
An error occurred (AccessDeniedException) when calling the Converse operation: User: arn:aws:sts::123456789012:assumed-role/scs-gr-test/gr-test is not authorized to perform: bedrock:InvokeModel on resource: arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0 with an explicit deny in an identity-based policy
```
- `with an explicit deny` ＝ Deny文に当たった（明示的拒否）
- Converseを呼んでいるのに、エラーのアクション名は `bedrock:InvokeModel`

## B：バージョン1を付ける
```
"text": "こんにちは！今日はどんなことを話し合いたいですか？…"
"stopReason": "end_turn",
"usage": {"inputTokens": 2, "outputTokens": 34, "totalTokens": 36}
```

## C：日本語の単語「競艇」をブロック対象にしたバージョン1で「競艇の予想して」
```
"text": "競艇の予想を行うのは非常に難しい作業で、…"（回答がそのまま返った）
"stopReason": "end_turn",
"usage": {"inputTokens": 9, "outputTokens": 586, "totalTokens": 595}
```
- **ブロックされなかった。** 単語フィルターが対応する言語は英語・フランス語・スペイン語のみ

## 単語を`boatrace`に変えたバージョン2（ポリシーは`:1`のまま）
```
… is not authorized to perform: bedrock:InvokeModel … with an explicit deny in an identity-based policy
```

## ポリシーを`:2`に更新してから、バージョン2で「Give me a boatrace prediction」
```
"text": "その入力はブロックされました"
"stopReason": "guardrail_intervened",
"usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
```
- 入力の段階で止まり、モデルは呼ばれていない（トークン0）
- インラインポリシーの更新は、既存のロールのセッションにもそのまま反映された（assume-roleのやり直しは不要だった）
