# Local Portfolio Tracker

A local-first holdings snapshot tracker for monthly brokerage exports.

The app now uses:

- FastAPI backend
- React/Vite frontend
- SQLite local database
- Raw CSV preservation under `data/imports/`
- yfinance live prices refreshed in the background

## Run In Development

Backend:

```bash
cd /Users/jmcilroy/scripts/investments
.venv/bin/uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd /Users/jmcilroy/scripts/investments/frontend
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Open `http://127.0.0.1:5173`.

## Run With Docker

Set credentials first:

```bash
cp .env.example .env
```

Edit `.env` and change `PORTFOLIO_PASSWORD` and `PORTFOLIO_SECRET_KEY`.

Build and start the whole app:

```bash
cd /Users/jmcilroy/scripts/investments
docker compose up --build
```

Open `http://127.0.0.1:8000`.

If you do not create `.env`, Docker uses `admin` / `changeme`. Treat that as local-only; do not expose a container started with the default password.

The compose file mounts local `./data` into the container at `/app/data`, so the SQLite database and raw uploaded CSVs persist when the image or container is rebuilt.

Run in the background:

```bash
docker compose up --build -d
```

Stop it:

```bash
docker compose down
```

## Build Frontend

```bash
cd /Users/jmcilroy/scripts/investments/frontend
npm run build
```

After the frontend is built, FastAPI can serve the compiled app from `frontend/dist`.

## Import The Current RSP CSV

```bash
cd /Users/jmcilroy/scripts/investments
.venv/bin/python -c "from pathlib import Path; from server import import_path; print(import_path(Path('/Users/jmcilroy/Downloads/AccountHoldings-BookValue.csv')))"
```

Optional cash can be attached to the same monthly snapshot:

```bash
cd /Users/jmcilroy/scripts/investments
.venv/bin/python -c "from pathlib import Path; from server import import_path; print(import_path(Path('/Users/jmcilroy/Downloads/AccountHoldings-BookValue (2).csv'), cash_balance=185959.41, cash_currency='CAD'))"
```

## Import Balance History

Use `History -> Upload History` for daily account marks. The importer supports either of these CSV shapes:

```csv
Date,Jamie RRSP,RESP,Jamie TFSA
2026-05-20,1299000.00,459500.00,134800.00
2026-05-21,1300489.72,460501.87,135052.03
```

```csv
Date,Account,Value
2026-05-20,Jamie RRSP,1299000.00
2026-05-20,RESP,459500.00
2026-05-21,Jamie RRSP,1300489.72
2026-05-21,RESP,460501.87
```

## Manual AWS Deployment With ECR And EC2

This is the simple deployment model:

- Build the Docker image on the Mac.
- Push it to a private ECR repository in the West Bragg AWS account.
- SSH into an EC2 instance.
- Pull the image from ECR.
- Run it with `/opt/portfolio-tracker/data` mounted to `/app/data`.

Use the West Bragg account only. Do not use the ATNV AWS profile.

Recommended placeholders:

```bash
AWS_ACCOUNT_ID=780710547275
AWS_REGION=us-east-1
ECR_REPOSITORY=portfolio-tracker
MAC_AWS_PROFILE=westbragg-deploy
EC2_DATA_DIR=/opt/portfolio-tracker/data
```

### AWS Console Setup

1. Use one AWS region.

   Use `us-east-1` everywhere below.

2. Create a private ECR repository.

   In AWS Console:

   - Open `Elastic Container Registry`.
   - Choose `us-east-1`.
   - Create repository.
   - Visibility: `Private`.
   - Repository name: `portfolio-tracker`.

3. Create a policy for pushing images from the Mac.

   In AWS Console:

   - Open `IAM -> Policies -> Create policy`.
   - Choose `JSON`.
   - Paste this policy.
   - Replace `<REGION>` and `<ACCOUNT_ID>`.
   - Name it `PortfolioTrackerEcrPushPolicy`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrLogin",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EcrPushPortfolioTracker",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:CompleteLayerUpload",
        "ecr:DescribeImages",
        "ecr:DescribeRepositories",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:<REGION>:<ACCOUNT_ID>:repository/portfolio-tracker"
    },
    {
      "Sid": "ReadOnlyEc2Lookup",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets"
      ],
      "Resource": "*"
    }
  ]
}
```

4. Create an IAM group for deployers.

   In AWS Console:

   - Open `IAM -> User groups -> Create group`.
   - Group name: `PortfolioTrackerDeployers`.
   - Attach `PortfolioTrackerEcrPushPolicy`.

5. Create an IAM user for the Mac deployment.

   In AWS Console:

   - Open `IAM -> Users -> Create user`.
   - User name: `portfolio-tracker-deployer`.
   - Access type: CLI access key.
   - Add the user to `PortfolioTrackerDeployers`.
   - Save the access key ID and secret access key somewhere safe.

   This is the AWS user your Mac will use to push Docker images. It should not be used inside EC2.

6. Create an EC2 instance role for pulling images.

   In AWS Console:

   - Open `IAM -> Roles -> Create role`.
   - Trusted entity type: `AWS service`.
   - Use case: `EC2`.
   - Attach AWS managed policy: `AmazonEC2ContainerRegistryReadOnly`.
   - Optional but useful: attach `AmazonSSMManagedInstanceCore` if you want AWS Systems Manager access.
   - Role name: `portfolio-tracker-ec2-role`.

7. Attach the role to the EC2 instance.

   In AWS Console:

   - Open `EC2 -> Instances`.
   - Select the instance.
   - `Actions -> Security -> Modify IAM role`.
   - Choose `portfolio-tracker-ec2-role`.

8. Configure the EC2 security group.

   Keep this tight:

   - SSH `22`: your home/work IP only.
   - App port `8000`: your IP only for initial testing.
   - Later, use HTTPS on `443` with nginx or Caddy and remove public `8000`.

### Mac Setup

Configure the Mac AWS profile. Use the access key from `portfolio-tracker-deployer`.

```bash
aws configure --profile westbragg-deploy
```

Use:

- AWS Access Key ID: from the new IAM user.
- AWS Secret Access Key: from the new IAM user.
- Default region: `us-east-1`.
- Default output: `json`.

Verify the profile:

```bash
AWS_PROFILE=westbragg-deploy aws sts get-caller-identity
AWS_PROFILE=westbragg-deploy aws ecr describe-repositories --region us-east-1
```

Build and push the Docker image:

```bash
cd /Users/jmcilroy/scripts/investments

AWS_PROFILE=westbragg-deploy ./scripts/push-ecr.sh
```

Deploy the latest ECR image to EC2:

```bash
./scripts/deploy-ec2.sh
```

The deploy script requires `/opt/portfolio-tracker/.env` and `/opt/portfolio-tracker/data/portfolio.db` to exist on EC2. It always starts the container with `--env-file /opt/portfolio-tracker/.env`; starting the image without that env file will make the app use its built-in local defaults.

The script defaults to:

```bash
AWS_PROFILE=westbragg-deploy
AWS_REGION=us-east-1
ECR_REPOSITORY=portfolio-tracker
IMAGE_TAG=latest
PLATFORM=linux/amd64
```

Override any value inline when needed:

```bash
AWS_PROFILE=westbragg-deploy IMAGE_TAG=2026-05-22 ./scripts/push-ecr.sh
```

Copy the current local data to EC2 before the first run:

```bash
scp -r /Users/jmcilroy/scripts/investments/data ec2-user@<EC2_HOST>:/tmp/portfolio-data
```

### EC2 Setup

SSH into EC2:

```bash
ssh ec2-user@<EC2_HOST>
```

Install Docker on Amazon Linux:

```bash
sudo dnf update -y
sudo dnf install -y docker awscli
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
```

Log out and back in after `usermod`, then create the app directories:

```bash
sudo mkdir -p /opt/portfolio-tracker
sudo mv /tmp/portfolio-data /opt/portfolio-tracker/data
sudo chown -R ec2-user:ec2-user /opt/portfolio-tracker
```

Create the production env file:

```bash
cat > /opt/portfolio-tracker/.env <<'EOF'
PORTFOLIO_USERNAME=admin
PORTFOLIO_PASSWORD=change-this-password
PORTFOLIO_SECRET_KEY=change-this-to-a-long-random-secret
PORTFOLIO_COOKIE_SECURE=true
PORTFOLIO_PRICE_REFRESH_ENABLED=true
PORTFOLIO_PRICE_REFRESH_SECONDS=60
PORTFOLIO_PRICE_REFRESH_TIMEZONE=America/Edmonton
PORTFOLIO_PRICE_REFRESH_START=07:45
PORTFOLIO_PRICE_REFRESH_END=16:00
EOF
```

Generate a good secret on the Mac or EC2:

```bash
openssl rand -hex 32
```

Pull and run the image on EC2:

```bash
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=780710547275
ECR_REPOSITORY=portfolio-tracker
ECR_REGISTRY="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
IMAGE="$ECR_REGISTRY/$ECR_REPOSITORY:latest"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

docker pull "$IMAGE"

docker stop portfolio-tracker 2>/dev/null || true
docker rm portfolio-tracker 2>/dev/null || true

docker run -d \
  --name portfolio-tracker \
  --restart unless-stopped \
  --env-file /opt/portfolio-tracker/.env \
  -p 8000:8000 \
  -v /opt/portfolio-tracker/data:/app/data \
  "$IMAGE"
```

Check it:

```bash
docker ps
docker logs --tail 100 portfolio-tracker
curl http://127.0.0.1:8000/api/health
```

Back up the production data directory regularly:

```bash
tar -czf "$HOME/portfolio-tracker-data-$(date +%Y%m%d).tgz" -C /opt portfolio-tracker/data
```

## Notes

- The first importer supports the BookValue holdings CSV layout with metadata rows, currency summaries, and a holdings table headed by `Asset type`.
- Cash balances are stored separately from securities, then included in account totals and allocation.
- Use the `Accounts` tab to set up account names, owners, account types, base currencies, and notes before importing holdings.
- Positions are snapshot-based for now. Monthly uploads create import batches and the dashboard uses the latest batch for each account.
- Live prices are stored separately from imported holdings. The default Docker refresh interval is 60 seconds and can be changed with `PORTFOLIO_PRICE_REFRESH_SECONDS`.
- The background price fetcher only runs during the configured market window, defaulting to `07:45` through `16:00` in `America/Edmonton`.
- Canadian tickers are requested from Yahoo Finance with `.TO` suffixes, while US tickers are converted to CAD with `CAD=X`.
- After the market window closes, the background worker saves one account-balance snapshot per local market date when that day's price data is available.
- Uploaded history maps CSV account labels to existing account names and writes them to the same balance snapshot tables used by end-of-day saves.
