#!/bin/bash

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TF_DIR="$PROJECT_ROOT/deployment/terraform"
RUNTIME_FILE="$PROJECT_ROOT/deployment/runtime.json"

usage() {
    echo "Usage: $0"
    echo ""
    echo "Environment variables (optional):"
    echo "  AWS_REGION    (default: us-east-1)"
    echo "  PROJECT_NAME  (default: sonic)"
    echo "  IMAGE_TAG     (default: agentcore)"
    echo ""
    exit 1
}

echo -e "${BLUE}🚀 Deploying Sonic (Terraform + AgentCore Runtime)${NC}"
echo ""

# Load .env file if it exists
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${YELLOW}📄 Loading .env file...${NC}"
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a
    echo -e "${GREEN}✅ .env loaded${NC}"
    echo ""
fi

if [ -n "$VITALLENS_API_KEY" ] && [ -z "$TF_VAR_vitallens_api_key" ]; then
    export TF_VAR_vitallens_api_key="$VITALLENS_API_KEY"
fi

# Check prerequisites
echo -e "${YELLOW}📋 Checking prerequisites...${NC}"
for cmd in aws terraform jq podman; do
    if ! command -v "$cmd" &> /dev/null; then
        echo -e "${RED}❌ $cmd is not installed${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✅ All prerequisites met${NC}"
echo ""

# Resolve account/region
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
PROJECT_NAME="${PROJECT_NAME:-sonic}"
IMAGE_TAG="${IMAGE_TAG:-agentcore}"

if [ -z "$ACCOUNT_ID" ]; then
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)
fi

if [ -z "$ACCOUNT_ID" ]; then
    echo -e "${RED}❌ Could not determine ACCOUNT_ID. Set ACCOUNT_ID and retry.${NC}"
    exit 1
fi

echo -e "${YELLOW}📌 Using:${NC}"
echo "   ACCOUNT_ID:   $ACCOUNT_ID"
echo "   AWS_REGION:   $AWS_REGION"
echo "   PROJECT_NAME: $PROJECT_NAME"
echo "   IMAGE_TAG:    $IMAGE_TAG"
echo ""

# ── Step 1: Terraform apply (infra) ───────────────────────────────────────
echo -e "${YELLOW}🧱 Step 1: Terraform apply (infra)${NC}"
terraform -chdir="$TF_DIR" init -upgrade -input=false
terraform -chdir="$TF_DIR" apply -auto-approve \
    -var="account_id=$ACCOUNT_ID" \
    -var="aws_region=$AWS_REGION" \
    -var="project_name=$PROJECT_NAME"
echo -e "${GREEN}✅ Terraform infra applied${NC}"
echo ""

# ── Step 2: Build and push container image ───────────────────────────────
echo -e "${YELLOW}🐳 Step 2: Build + push AgentCore image${NC}"
ECR_REPO_URL=$(terraform -chdir="$TF_DIR" output -raw ecr_repository_url)

if [ -z "$ECR_REPO_URL" ]; then
    echo -e "${RED}❌ Could not read ECR repository URL from Terraform outputs${NC}"
    exit 1
fi

aws ecr get-login-password --region "$AWS_REGION" \
    | podman login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

podman build \
    --platform linux/arm64 \
    -t "${ECR_REPO_URL}:${IMAGE_TAG}" \
    "$PROJECT_ROOT/backend"

podman push "${ECR_REPO_URL}:${IMAGE_TAG}"
echo -e "${GREEN}✅ Image pushed to ECR${NC}"
echo ""

# ── Step 3: Create AgentCore runtime ─────────────────────────────────────
echo -e "${YELLOW}🤖 Step 3: Create AgentCore runtime${NC}"
ROLE_ARN=$(terraform -chdir="$TF_DIR" output -raw agentcore_role_arn)

if [ -z "$ROLE_ARN" ]; then
    echo -e "${RED}❌ Could not read AgentCore role ARN from Terraform outputs${NC}"
    exit 1
fi

RANDOM_ID=$(openssl rand -hex 2)
# AgentCore runtime name must match: [a-zA-Z][a-zA-Z0-9_]{0,47}
SAFE_PROJECT_NAME=$(echo "$PROJECT_NAME" | tr -cd 'a-zA-Z0-9_' )
if [ -z "$SAFE_PROJECT_NAME" ]; then
    SAFE_PROJECT_NAME="sonic"
fi
AGENT_RUNTIME_NAME="${SAFE_PROJECT_NAME}_runtime_${RANDOM_ID}"
AGENT_RESPONSE=$(aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name "$AGENT_RUNTIME_NAME" \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_REPO_URL}:${IMAGE_TAG}\"}}" \
  --lifecycle-configuration '{"idleRuntimeSessionTimeout":180,"maxLifetime":360}' \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --role-arn "$ROLE_ARN" \
  --region "$AWS_REGION" \
  --output json)

AGENT_ARN=$(echo "$AGENT_RESPONSE" | jq -r '.agentRuntimeArn')
if [ -z "$AGENT_ARN" ] || [ "$AGENT_ARN" = "null" ]; then
    echo -e "${RED}❌ AgentCore runtime creation failed${NC}"
    echo "$AGENT_RESPONSE"
    exit 1
fi

cat > "$RUNTIME_FILE" << EOF
{
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "aws_region": "$AWS_REGION",
  "account_id": "$ACCOUNT_ID",
  "project_name": "$PROJECT_NAME",
  "agent_runtime_name": "$AGENT_RUNTIME_NAME",
  "agent_runtime_arn": "$AGENT_ARN",
  "image_uri": "${ECR_REPO_URL}:${IMAGE_TAG}",
  "agentcore_role_arn": "$ROLE_ARN"
}
EOF

echo -e "${GREEN}✅ AgentCore runtime created: $AGENT_ARN${NC}"
echo -e "${GREEN}✅ Runtime saved: $RUNTIME_FILE${NC}"
echo ""

# ── Step 4: Terraform apply (wire runtime ARN) ───────────────────────────
echo -e "${YELLOW}🔁 Step 4: Terraform apply (runtime ARN)${NC}"
terraform -chdir="$TF_DIR" apply -auto-approve \
    -var="account_id=$ACCOUNT_ID" \
    -var="aws_region=$AWS_REGION" \
    -var="project_name=$PROJECT_NAME" \
    -var="runtime_arn=$AGENT_ARN"
echo -e "${GREEN}✅ Lambda updated with runtime ARN${NC}"
echo ""

# ── Step 5: Build + deploy frontend ─────────────────────────────────────
echo -e "${YELLOW}🌐 Step 5: Build + deploy frontend${NC}"
FRONTEND_BUCKET=$(terraform -chdir="$TF_DIR" output -raw s3_bucket_name)
DIST_ID=$(terraform -chdir="$TF_DIR" output -raw cloudfront_distribution_id)

if [ -z "$FRONTEND_BUCKET" ]; then
    echo -e "${RED}❌ Could not read frontend bucket from Terraform outputs${NC}"
    exit 1
fi

cd "$PROJECT_ROOT/frontend"
if [ ! -d "node_modules" ]; then
    npm install
fi
export VITE_VITALLENS_PROXY_URL="${VITE_VITALLENS_PROXY_URL:-/api/vitallens}"
export VITE_VITALLENS_API_KEY=""
npm run build

aws s3 sync "$PROJECT_ROOT/frontend/dist/" "s3://$FRONTEND_BUCKET/" \
    --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "index.html"

aws s3 cp "$PROJECT_ROOT/frontend/dist/index.html" "s3://$FRONTEND_BUCKET/index.html" \
    --cache-control "no-cache, no-store, must-revalidate" \
    --content-type "text/html"

if [ -n "$DIST_ID" ]; then
    aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*" > /dev/null
fi
echo -e "${GREEN}✅ Frontend deployed${NC}"
echo ""

CLOUDFRONT_URL=$(terraform -chdir="$TF_DIR" output -raw cloudfront_url)
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Deploy Complete${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${BLUE}CloudFront URL:${NC} ${CLOUDFRONT_URL}"
echo -e "${BLUE}AgentCore Runtime ARN:${NC} ${AGENT_ARN}"
echo ""
