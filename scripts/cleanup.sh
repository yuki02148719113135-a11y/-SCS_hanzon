#!/usr/bin/env bash
# run-lab.sh で作ったロール・インラインポリシー・ガードレールを削除する。
set -euo pipefail

export AWS_REGION=us-east-1
HERE="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="${HERE}/../.lab-state"

# ロールの一時的な認証情報が残っていると削除の権限がないため、外しておく
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE"
else
  ROLE_NAME="scs-gr-test"
  POLICY_NAME="gr-enforce"
  GR_ID=$(aws bedrock list-guardrails --query "guardrails[?name=='scs-handson-gr'].id" --output text)
fi

aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" 2>/dev/null \
  && echo "削除: インラインポリシー ${POLICY_NAME}" || echo "スキップ: インラインポリシー（見つからない）"
aws iam delete-role --role-name "$ROLE_NAME" 2>/dev/null \
  && echo "削除: ロール ${ROLE_NAME}" || echo "スキップ: ロール（見つからない）"
if [[ -n "${GR_ID:-}" ]]; then
  aws bedrock delete-guardrail --guardrail-identifier "$GR_ID" && echo "削除: ガードレール ${GR_ID}"
else
  echo "スキップ: ガードレール（見つからない）"
fi
rm -f "$STATE_FILE"
