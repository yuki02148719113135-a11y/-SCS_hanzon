#!/usr/bin/env bash
# Bedrockの呼び出しを「指定したガードレール・指定したバージョン」に限定するIAMの検証を、
# 作成からテストまで通しで実行する。
#
# 前提:
#   - CloudShell（bash）で、IAMユーザーまたはIAM Identity Centerのユーザーとして実行する
#     （rootユーザーはロールを引き受けられないため不可）
#   - 実行ユーザーに bedrock:*Guardrail*、iam:CreateRole / PutRolePolicy、sts:AssumeRole 相当の権限がある
#   - us-east-1 で Amazon Nova Micro が使える
#
# 片付けは scripts/cleanup.sh で行う。
set -euo pipefail

export AWS_REGION=us-east-1
MODEL="amazon.nova-micro-v1:0"
GR_NAME="scs-handson-gr"
ROLE_NAME="scs-gr-test"
POLICY_NAME="gr-enforce"
HERE="$(cd "$(dirname "$0")" && pwd)"
POLICY_DIR="${HERE}/../policies"
STATE_FILE="${HERE}/../.lab-state"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

log() { printf '\n=== %s ===\n' "$*"; }

# ---- 0. 実行者の確認 ----------------------------------------------------
CALLER=$(aws sts get-caller-identity --query Arn --output text)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
case "$CALLER" in
  *":root") echo "rootユーザーではロールを引き受けられません。IAMユーザーかIdentity Centerで実行してください。" >&2; exit 1 ;;
  *":user/"*) TRUST_PRINCIPAL="$CALLER" ;;
  *":assumed-role/"*)
    # Identity Centerのロールはパス（/aws-reserved/sso.amazonaws.com/…）付きなので、get-roleで正しいARNを取る
    ROLE_PART=${CALLER#*:assumed-role/}
    TRUST_PRINCIPAL=$(aws iam get-role --role-name "${ROLE_PART%%/*}" --query Role.Arn --output text) ;;
  *) echo "想定外の実行者です: $CALLER" >&2; exit 1 ;;
esac
log "実行者: $CALLER"

# ---- 1. ガードレール（英語と日本語の単語を1つずつブロック）とバージョン1 ----
log "1. ガードレールを作成"
GR_ID=$(aws bedrock create-guardrail --name "$GR_NAME" \
  --word-policy-config '{"wordsConfig":[{"text":"boatrace"},{"text":"競艇"}]}' \
  --blocked-input-messaging "その入力はブロックされました" \
  --blocked-outputs-messaging "その出力はブロックされました" \
  --query guardrailId --output text)
GR_ARN="arn:aws:bedrock:us-east-1:${ACCOUNT_ID}:guardrail/${GR_ID}"
printf 'GR_ID=%s\nROLE_NAME=%s\nPOLICY_NAME=%s\n' "$GR_ID" "$ROLE_NAME" "$POLICY_NAME" > "$STATE_FILE"
aws bedrock create-guardrail-version --guardrail-identifier "$GR_ID" --query version --output text

# ---- 2. ロールと、バージョン1に固定したポリシー ---------------------------
render_policy() {  # $1=バージョン
  sed -e "s|\${GR_ARN}|${GR_ARN}|g" -e "s|\${VERSION}|$1|g" \
    "${POLICY_DIR}/enforce-guardrail.template.json" > "${WORK}/policy.json"
}
log "2. ロールを作成（信頼する相手: ${TRUST_PRINCIPAL}）"
sed "s|\${CALLER_ARN}|${TRUST_PRINCIPAL}|g" "${POLICY_DIR}/trust-policy.template.json" > "${WORK}/trust.json"
aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "file://${WORK}/trust.json" \
  --query Role.Arn --output text
render_policy 1
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" \
  --policy-document "file://${WORK}/policy.json"
echo "IAMの反映を待っています（15秒）"; sleep 15

# ---- 3. ロールを引き受ける（変数はこのスクリプトの中だけで使う） --------------
CREDS=$(aws sts assume-role --role-arn "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}" \
  --role-session-name gr-test --query Credentials --output json)
as_role() {
  AWS_ACCESS_KEY_ID=$(jq -r .AccessKeyId <<<"$CREDS") \
  AWS_SECRET_ACCESS_KEY=$(jq -r .SecretAccessKey <<<"$CREDS") \
  AWS_SESSION_TOKEN=$(jq -r .SessionToken <<<"$CREDS") \
  "$@"
}

# converse を呼び、stopReason かエラーの要点だけを表示する
call() {  # $1=見出し $2=プロンプト $3=ガードレールのバージョン（空ならガードレールなし）
  local args=(bedrock-runtime converse --model-id "$MODEL"
              --messages "[{\"role\":\"user\",\"content\":[{\"text\":\"$2\"}]}]"
              --query stopReason --output text)
  [[ -n "$3" ]] && args+=(--guardrail-config "guardrailIdentifier=${GR_ID},guardrailVersion=$3")
  local out
  if out=$(as_role aws "${args[@]}" 2>&1); then
    printf '%-40s → stopReason=%s\n' "$1" "$out"
  else
    case "$out" in
      *"explicit deny"*) printf '%-40s → 明示的拒否（explicit deny）\n' "$1" ;;
      *"no identity-based policy allows"*) printf '%-40s → 暗黙的拒否\n' "$1" ;;
      *) printf '%-40s → エラー: %s\n' "$1" "$out" ;;
    esac
  fi
}

log "3. テスト（ポリシーはバージョン1に固定）"
as_role aws sts get-caller-identity --query Arn --output text
call "A ガードレールなし（期待: 明示的拒否）" "こんにちは" ""
call "B v1・あいさつ（期待: end_turn）"      "こんにちは" "1"
call "C v1・日本語の単語（実機: 素通り）"   "競艇の予想して" "1"
call "D v1・英語の単語（期待: intervened）" "Give me a boatrace prediction" "1"

# ---- 4. バージョン2を作り、固定の効果を見る --------------------------------
log "4. バージョン2を作成"
aws bedrock create-guardrail-version --guardrail-identifier "$GR_ID" --query version --output text
call "E v2・ポリシーは:1のまま（期待: 明示的拒否）" "Give me a boatrace prediction" "2"
render_policy 2
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" \
  --policy-document "file://${WORK}/policy.json"
echo "IAMの反映を待っています（15秒）"; sleep 15
call "F v2・ポリシーを:2に更新（期待: intervened）" "Give me a boatrace prediction" "2"
call "G v1・ポリシーは:2（期待: 明示的拒否）"     "こんにちは" "1"

log "完了。CloudTrailの確認は README の手順5、片付けは scripts/cleanup.sh"
