# つまずいたところと直し方

実際にハンズオンをやって、引っかかった順に並べている。

## 1. `no identity-based policy allows the bedrock:CreateGuardrail action`

- **起きたこと**：Macのターミナルで、権限の小さいIAMユーザーで実行していた
- **原因**：そのユーザーにはBedrockやIAMを操作するAllowがなく、**暗黙的拒否**になった
- **直し方**：管理者権限のあるユーザーでコンソールに入って、CloudShellで実行する
- **後から出たエラー**：作成に失敗したせいで`GR_ID`が空のまま進み、`expected one argument`や`The provided guardrail identifier is invalid`が続けて出た。最初のエラーで止まって直すことが大事

## 2. `zsh: command not found: #`

- **原因**：macOSのzshは、対話で使っているシェルでは`#`をコメントとして扱わない（`INTERACTIVE_COMMENTS`オプションがオフ）
- **直し方**：`setopt INTERACTIVE_COMMENTS`を実行するか、bashのCloudShellを使う

## 3. `Invalid length for parameter RoleSessionName, value: 1, valid min length: 2`

- **原因**：`--role-session-name`は2〜64文字でないといけない
- **直し方**：`gr-test`のように2文字以上にする

## 4. `Roles may not be assumed by root accounts.`

- **原因**：rootユーザーはロールを引き受けられない（スイッチロールもできない）
- **直し方**：IAMユーザーかIAM Identity Centerのユーザーで実行する。そもそもrootは、rootでしかできない作業以外では使わない

## 5. `arn:aws:iam:::role/scs-gr-test`（アカウントIDが空）

- **原因**：rootから別のユーザーに入り直したので、CloudShellのセッションが変わり、変数（`ACCOUNT_ID`・`GR_ID`など）が全部消えていた。CloudShellのホームディレクトリも利用者ごとに分かれているので、作った`policy.json`も残っていなかった
- **直し方**：変数を作り直す。`echo "GR_ID=[$GR_ID]"`のように`[ ]`で囲んで表示すると、空かどうかがすぐ分かる

## 6. 行頭が`>`になって、何を打っても何も出ない

- **原因**：`\`で終わる行や`$(`を閉じないまま貼ったので、シェルが続きを待っていた
- **直し方**：`Ctrl + C`で入力を捨てる。複数行のコマンドはブロックごと最後まで貼る

## 7. ガードレールの一覧が空になる

- **原因**：CloudShellは、コンソールで今選んでいるリージョンで動く。東京を選んだままだと、us-east-1のガードレールは見えない
- **直し方**：`export AWS_REGION=us-east-1`

## 8. 日本語の単語がブロックされない

- **原因**：Bedrock Guardrailsの単語フィルターが対応している言語は、英語・フランス語・スペイン語だけ。日本語に対応したStandard tierでも、単語フィルターは対象外
- **直し方**：検証は英語の単語で行う。日本語の話題を止めたいときは、Standard tierの拒否トピック（denied topics）を使うことを検討する（今回は未検証）

## 9. （設計の変更）信頼ポリシーで、引き受けるユーザーを直接指定した

エラーへの対処ではなく、作り直すときに設計を変えたところ。

- 最初は信頼ポリシーの`Principal`を`arn:aws:iam::<アカウントID>:root`（アカウント全体）にしていた。この書き方だと、引き受ける側のユーザーにも`sts:AssumeRole`の許可が必要になる
- 作り直すときに、引き受けるユーザーのARNを直接指定する形に変えた。同じアカウントの中なら、信頼ポリシー（リソースベースのポリシー）でユーザーを指定しておけば、ユーザー側のポリシーを追加しなくても引き受けられる
- 引き受けられる相手が1人に絞られるので、最小権限の考え方にも合っている
- 5のときの`not authorized to perform: sts:AssumeRole`は、アカウントIDが空だったことと、ロール自体がまだ作られていなかったことによるもの。この設計の問題ではない

## 出典

- zshの`INTERACTIVE_COMMENTS`：[Zshインタラクティブモードでコメントアウトを使用する](https://ryonakagami.github.io/regmonkey-datascience-blog/posts/2025-10-10-zsh-inline-comment/index.html)（zshマニュアル「6.7 Comments」の引用あり）
- RoleSessionNameの長さ・同一アカウントの信頼ポリシー：[AssumeRole – AWS STS API Reference](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html)
- rootはスイッチロールできない：[Grant a user permissions to switch roles – IAM User Guide](https://docs.amazonaws.cn/en_us/IAM/latest/UserGuide/id_roles_use_permissions-to-switch.html)
- rootの利用を制限する：[ACCT.02 – Restrict use of the root user – AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/aws-startup-security-baseline/acct-02.html)
- 単語フィルターの対応言語：[Languages supported by Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-supported-languages.html)、[Standard Tier の解説（クラスメソッド）](https://dev.classmethod.jp/articles/aws-bedrock-guardrails-standard-tier/)
