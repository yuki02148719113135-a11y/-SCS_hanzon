# Bedrockの呼び出しを「承認済みのガードレール」に限定するIAMハンズオン

AWS認定 Security – Specialty（SCS）の対策として、**Amazon Bedrockのモデルを、指定したガードレール・指定したバージョンを付けたときだけ呼べるようにする**IAMの設計を、実機で試した記録です（2026年11月11日、us-east-1、Amazon Nova Micro）。

うまくいったことだけでなく、つまずいたところと、**ガードレールを付けているのに効いていなかった**失敗もそのまま残しています。

## 何を確かめたか

| # | やったこと | 結果 | 分かったこと |
|---|---|---|---|
| A | ガードレールを付けずに呼ぶ | **明示的拒否** | Allowがあっても、明示的なDenyが常に勝つ |
| B | バージョン1を付けて呼ぶ | 成功 | 条件キー`bedrock:GuardrailIdentifier`で、指定したガードレールのときだけ許可できる |
| C | 日本語の単語「競艇」をブロック対象にして呼ぶ | **素通り** | 単語フィルターは英語・フランス語・スペイン語のみ。付けていても効いているとは限らない |
| – | バージョン2で呼ぶ（ポリシーは`:1`のまま） | **明示的拒否** | バージョンを固定すれば、新しい版や未承認の版は使えない |
| – | ポリシーを`:2`に更新して、英語の単語で呼ぶ | `guardrail_intervened` | 入力の段階で止まり、モデルは呼ばれなかった（トークン0） |
| – | CloudTrailで`Converse`を検索 | 全部記録されていた | **ガードレールが止めた呼び出しも「成功」と記録される** |

実際の出力は [`evidence/test-results.md`](evidence/test-results.md) と [`evidence/cloudtrail-converse-2026-11-11.tsv`](evidence/cloudtrail-converse-2026-11-11.tsv) にあります（アカウントIDは`123456789012`に置き換えています）。

## 構成

```
呼び出す人（IAMユーザー）
   │ sts:AssumeRole（信頼ポリシーでこのユーザーだけを指定）
   ▼
ロール scs-gr-test ── インラインポリシー gr-enforce
   │   Allow : ガードレール <ID>:<版> を付けたときだけ InvokeModel を許可
   │   Deny  : それ以外の GuardrailIdentifier なら InvokeModel を拒否
   ▼
bedrock-runtime Converse ──► ガードレール（単語フィルター）──► Amazon Nova Micro
   │
   └─► CloudTrail（管理イベントとして記録）
```

ポリシーの本体は [`policies/enforce-guardrail.template.json`](policies/enforce-guardrail.template.json) です。AWS公式ドキュメントの「特定のガードレールと版を強制する」例をもとにしています。

## 試験で効くポイント

### 1. 明示的拒否と暗黙的拒否は、エラー文で見分けられる

| エラー文の終わり | 意味 | 直し方 |
|---|---|---|
| `because no identity-based policy allows ...` | 許可するポリシーがなかった（**暗黙的拒否**） | どこかにAllowを足せば通る |
| `with an explicit deny in an identity-based policy` | Deny文に当たった（**明示的拒否**） | Allowを足しても通らない。Deny側を直す |

IAMのポリシー評価では、**どこかに明示的なDenyがあれば、Allowは上書きされます**。このロールに`AdministratorAccess`を付けても、ガードレールなしの呼び出しは通りません（us-east-1の基盤モデルについては。4を参照）。

### 2. Converseの権限は`bedrock:InvokeModel`

Converseを呼んだのに、拒否されたアクションは`bedrock:InvokeModel`でした。Converse APIのリファレンスにも、この操作には`bedrock:InvokeModel`の権限が必要と書かれています。止めたいときは`InvokeModel`と`InvokeModelWithResponseStream`の**両方**をDenyしないと、もう一方の経路が残ります。

### 3. 「ガードレールを付けている」と「効いている」は別物

単語フィルターで「競艇」を指定しても、日本語はブロックされませんでした。単語フィルターが対応している言語は英語・フランス語・スペイン語だけで、日本語に対応したStandard tierでも、単語フィルターは対象外です。統制を作るときは、**言語の対応状況まで確認する**必要があります。

### 4. 100万回のシミュレーションで見つけた弱点：Denyの範囲がus-east-1だけ

元のポリシーは、AWSの例にならって、Denyの`Resource`を`arn:aws:bedrock:us-east-1::foundation-model/*`にしています。このロールにこのポリシー**しか**付いていなければ問題はありません（今回の実機テストもこの状態）。ただ、**もっと広いAllow（たとえば`AdministratorAccess`）が一緒に付くと、ほかのリージョンのモデルや推論プロファイルは、ガードレールなしで呼べてしまいます**。

[`sim/policy_eval_sim.py`](sim/policy_eval_sim.py) で、IAMの評価ルールを写したモデルを使って、100万件の無作為なリクエストを判定しました（シード42）。

| ポリシー | すり抜け（指定外のガードレールで「許可」になった件数） |
|---|---|
| 元のポリシー | **345,668件**（全部「管理者権限あり」で、us-east-1以外の基盤モデルか、推論プロファイルへの呼び出し） |
| 強化版（Denyの`Resource`を`*`に変更） | **0件** |

- 先にモデルが実機の5つの結果を同じように判定することを確かめてから、100万件を回しています（5件すべて一致）
- 強化版は [`policies/enforce-guardrail-hardened.template.json`](policies/enforce-guardrail-hardened.template.json) です。**強化版は実機ではまだ試していません**
- 結果の全文は [`sim/results.md`](sim/results.md) にあります

**シミュレーションの限界**：これはAWSの評価エンジンそのものではなく、公開されているルールを簡略化したモデルです。SCP・アクセス許可の境界・セッションポリシーは扱っていません。「リクエストに条件キーがないとき、`StringNotEquals`は真になる」という前提は、ドキュメントの記述（ガードレールを付けない推論リクエストは拒否される）と実機テストAの結果をもとにしています。本番で使う前には、IAM Policy Simulatorか実機で確かめてください。

### 5. CloudTrailに出るのは「APIが許可されたか」まで

ガードレールが入力をブロックした呼び出しも、CloudTrailでは`errorCode`なし（成功）と記録されていました。CloudTrailでは、ガードレールが止めたかどうかは分かりません。

| 知りたいこと | 使うもの |
|---|---|
| 誰が・どのロールで呼んだか、拒否されたか | CloudTrail（Converseなどは管理イベントとして、追加設定なしで記録される） |
| 何を入力して、何が返ったか | モデル呼び出しのログ（model invocation logging）※今回は未検証 |

### 6. バージョン固定と、ポリシー更新の反映

- ポリシーを`:1`に固定しておくと、ガードレールを更新して新しいバージョン2を作っても、ポリシーを直すまでは使えません。承認していない版を勝手に使わせない統制になります
- インラインポリシーを書き換えると、`assume-role`をやり直さなくても、今のセッションに反映されました

### 7. 料金

- ガードレールが入力をブロックしたときは、ガードレールの評価分だけが料金になり、モデルの推論料金はかかりません（今回もトークン0でした）
- 単語フィルターは料金がかかりません
- 今回の検証でかかったのはNova Microの数回分の呼び出しだけで、ごくわずかです（請求額は未確認）

## 再現する手順

CloudShell（bash）で、**rootではないユーザー**（IAMユーザーかIAM Identity Centerの管理者）で実行してください。

```bash
git clone https://github.com/yuki02148719113135-a11y/-SCS_hanzon.git
cd -SCS_hanzon
bash scripts/run-lab.sh      # 作成からテストAからGまで
bash scripts/cleanup.sh      # ロール・ポリシー・ガードレールを削除
```

`run-lab.sh` は、手で1つずつ実行した手順をまとめたものです。違うのは次の点です。
- 最初から英語（`boatrace`）と日本語（`競艇`）の両方をブロック対象にして、C（日本語）とD（英語）を同じ版で比べられるようにした
- 最後に「ポリシーを`:2`にしたあとで、バージョン1を付けて呼ぶ」テストGを足した

**このスクリプト自体は、まだ通しで実行していません。** 動かなかったら、エラーをIssueに書いてください。

CloudTrailの確認（反映まで数分かかることがあります）：

```bash
aws cloudtrail lookup-events --region us-east-1 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=Converse \
  --max-results 10 --query "Events[].CloudTrailEvent" --output text \
  | jq -r '[.eventTime, .userIdentity.arn, (.errorCode // "成功")] | @tsv'
```

シミュレーション（AWSの認証情報は不要）：

```bash
python3 -I sim/policy_eval_sim.py 1000000 42
```

## つまずいたところ

rootでの実行、zshのコメント、セッション名の長さ、CloudShellの変数が消える、行頭が`>`のまま止まる、などを [`docs/troubleshooting.md`](docs/troubleshooting.md) にまとめています。

## 今回やっていないこと（次の候補）

- 強化版ポリシーの実機での検証
- モデル呼び出しのログを、KMSで暗号化したS3に出す
- Standard tierの拒否トピックで、日本語の話題を止められるか
- `RetrieveAndGenerate`やエージェント経由の呼び出しとの組み合わせ（AWS公式に、内部の呼び出しにガードレールが付かず拒否されることがあるという注意あり）

## 出典

- [Enforce the use of specific guardrails – Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-permissions-id.html)：ポリシーの例、版の指定方法、ガードレールなしの推論は拒否されること、`RetrieveAndGenerate`などとの組み合わせの注意、入力タグでプロンプト側をすり抜けられること
- [Converse – Amazon Bedrock API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)：必要な権限は`bedrock:InvokeModel`、2つのアクションを両方Denyする必要があること、`stopReason`の値
- [Policy evaluation logic – IAM User Guide](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)：明示的なDenyはAllowを上書きする
- [AssumeRole – AWS STS API Reference](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html)：同じアカウントでは、信頼ポリシーでユーザーを直接指定すればユーザー側のポリシーは不要
- [Languages supported by Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-supported-languages.html)：単語フィルターの対応言語
- [Amazon Bedrock Guardrails に日本語対応の Standard Tier が登場（クラスメソッド）](https://dev.classmethod.jp/articles/aws-bedrock-guardrails-standard-tier/)：Standard tierでも単語フィルターは日本語未対応
- [How Amazon Bedrock Guardrails works](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-how.html)：入力がブロックされたときは推論料金がかからない
- [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)：単語フィルターは無料
- [Monitor Amazon Bedrock API calls using CloudTrail](https://docs.aws.amazon.com/bedrock/latest/userguide/logging-using-cloudtrail.html)：Converseなどは管理イベントとして記録される
