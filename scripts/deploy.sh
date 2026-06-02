#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_AWS_PROFILE="westbragg-deploy"
EXPECTED_AWS_ACCOUNT_ID="780710547275"

if ! command -v aws >/dev/null 2>&1; then
  echo "Missing required command: aws" >&2
  exit 1
fi

AWS_PROFILE="$DEPLOY_AWS_PROFILE"
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="$(
  aws sts get-caller-identity \
    --profile "$AWS_PROFILE" \
    --query Account \
    --output text
)"

if [ "$AWS_ACCOUNT_ID" != "$EXPECTED_AWS_ACCOUNT_ID" ]; then
  echo "AWS profile/account mismatch." >&2
  echo "Profile:          $AWS_PROFILE" >&2
  echo "Resolved account: $AWS_ACCOUNT_ID" >&2
  echo "Expected account: $EXPECTED_AWS_ACCOUNT_ID" >&2
  exit 1
fi

export AWS_ACCOUNT_ID
export AWS_PROFILE
export AWS_REGION

cd "$ROOT_DIR"

echo "Deploying portfolio tracker"
echo "Repo:        $ROOT_DIR"
echo "AWS profile: $AWS_PROFILE"
echo "AWS account: $AWS_ACCOUNT_ID"
echo "AWS region:  $AWS_REGION"
echo

./scripts/push-ecr.sh

echo
./scripts/deploy-ec2.sh
