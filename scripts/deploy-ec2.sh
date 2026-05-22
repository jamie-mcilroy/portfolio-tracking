#!/usr/bin/env bash
set -euo pipefail

AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-780710547275}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPOSITORY="${ECR_REPOSITORY:-portfolio-tracker}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

EC2_USER="${EC2_USER:-ec2-user}"
EC2_HOST="${EC2_HOST:-34.226.67.173}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/west-bragg-portfolio-tracker.pem}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/portfolio-tracker}"
CONTAINER_NAME="${CONTAINER_NAME:-portfolio-tracker}"

if [ ! -f "$SSH_KEY" ]; then
  echo "Missing SSH key: $SSH_KEY" >&2
  exit 1
fi

ECR_REGISTRY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
IMAGE="$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG"

echo "Deploying image: $IMAGE"
echo "Target:          $EC2_USER@$EC2_HOST"
echo "App directory:   $REMOTE_APP_DIR"

ssh -i "$SSH_KEY" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  "$EC2_USER@$EC2_HOST" \
  AWS_REGION="$AWS_REGION" \
  ECR_REGISTRY="$ECR_REGISTRY" \
  IMAGE="$IMAGE" \
  REMOTE_APP_DIR="$REMOTE_APP_DIR" \
  CONTAINER_NAME="$CONTAINER_NAME" \
  'bash -se' <<'REMOTE_SCRIPT'
set -euo pipefail

ENV_FILE="$REMOTE_APP_DIR/.env"
DATA_DIR="$REMOTE_APP_DIR/data"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

if [ ! -f "$DATA_DIR/portfolio.db" ]; then
  echo "Missing database: $DATA_DIR/portfolio.db" >&2
  exit 1
fi

chmod 600 "$ENV_FILE"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null

docker pull "$IMAGE"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rm "$CONTAINER_NAME" >/dev/null
fi

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -p 127.0.0.1:8000:8000 \
  -v "$DATA_DIR:/app/data" \
  "$IMAGE"

sleep 4
curl -fsS http://127.0.0.1:8000/api/health >/dev/null

docker ps --filter "name=$CONTAINER_NAME" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
REMOTE_SCRIPT

echo "Deploy complete."
