#!/usr/bin/env bash
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-westbragg-deploy}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPOSITORY="${ECR_REPOSITORY:-portfolio-tracker}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Multi-arch so it works on t3/x86_64 and t4g/ARM64
PLATFORM="${PLATFORM:-linux/amd64,linux/arm64}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

need_command aws
need_command docker

echo "Using AWS profile: $AWS_PROFILE"
echo "Using AWS region:  $AWS_REGION"
echo "Platform:          $PLATFORM"

AWS_ACCOUNT_ID="$(
  aws sts get-caller-identity \
    --profile "$AWS_PROFILE" \
    --query Account \
    --output text
)"

ECR_REGISTRY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
IMAGE="$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG"

echo "AWS account:       $AWS_ACCOUNT_ID"
echo "ECR repository:    $ECR_REPOSITORY"
echo "Image:             $IMAGE"

echo "Checking ECR repository..."
if ! aws ecr describe-repositories \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --repository-names "$ECR_REPOSITORY" >/dev/null 2>&1; then
  echo "Creating ECR repository: $ECR_REPOSITORY"
  aws ecr create-repository \
    --profile "$AWS_PROFILE" \
    --region "$AWS_REGION" \
    --repository-name "$ECR_REPOSITORY" >/dev/null
fi

echo "Logging Docker into ECR..."
aws ecr get-login-password \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

echo "Ensuring Docker buildx builder exists..."
docker buildx inspect portfolio-tracker-builder >/dev/null 2>&1 || \
  docker buildx create --name portfolio-tracker-builder --use

docker buildx use portfolio-tracker-builder

echo "Building and pushing Docker image..."
cd "$ROOT_DIR"

docker buildx build \
  --platform "$PLATFORM" \
  -t "$IMAGE" \
  --push \
  .

echo
echo "Pushed image:"
echo "$IMAGE"

echo
echo "ECR images:"
aws ecr list-images \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --repository-name "$ECR_REPOSITORY"

echo

echo "Build and push complete."
echo "Deploy to EC2 with: ./scripts/deploy-ec2.sh"
