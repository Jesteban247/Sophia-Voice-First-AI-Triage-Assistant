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

echo -e "${BLUE}🧹 Destroying Sonic (AgentCore Runtime + Terraform)${NC}"
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

# Check prerequisites
for cmd in aws terraform jq; do
    if ! command -v "$cmd" &> /dev/null; then
        echo -e "${RED}❌ $cmd is not installed${NC}"
        exit 1
    fi
done

AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
PROJECT_NAME="${PROJECT_NAME:-sonic}"

if [ -z "$ACCOUNT_ID" ]; then
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)
fi

if [ -z "$ACCOUNT_ID" ]; then
    echo -e "${RED}❌ Could not determine ACCOUNT_ID. Set ACCOUNT_ID and retry.${NC}"
    exit 1
fi

# ── Step 1: Delete AgentCore runtime (if present) ───────────────────────
if [ -f "$RUNTIME_FILE" ]; then
    echo -e "${YELLOW}🤖 Deleting AgentCore runtime...${NC}"
    AGENT_ARN=$(jq -r '.agent_runtime_arn' "$RUNTIME_FILE")
    if [ -n "$AGENT_ARN" ] && [ "$AGENT_ARN" != "null" ]; then
        AGENT_ID=$(echo "$AGENT_ARN" | awk -F'/' '{print $NF}')
        aws bedrock-agentcore-control delete-agent-runtime \
            --agent-runtime-id "$AGENT_ID" \
            --region "$AWS_REGION" \
            --no-cli-pager || true
        echo -e "${GREEN}✅ AgentCore runtime deletion requested${NC}"
    else
        echo -e "${YELLOW}⚠️  runtime.json missing agent_runtime_arn; skipping${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  No runtime.json found; skipping AgentCore deletion${NC}"
fi
echo ""

# ── Step 2: Terraform destroy ───────────────────────────────────────────
echo -e "${YELLOW}🧨 Terraform destroy${NC}"
terraform -chdir="$TF_DIR" destroy -auto-approve \
    -var="account_id=$ACCOUNT_ID" \
    -var="aws_region=$AWS_REGION" \
    -var="project_name=$PROJECT_NAME"
echo -e "${GREEN}✅ Terraform destroyed${NC}"
echo ""

# ── Step 3: Cleanup runtime file ────────────────────────────────────────
if [ -f "$RUNTIME_FILE" ]; then
    rm -f "$RUNTIME_FILE"
    echo -e "${GREEN}✅ Removed $RUNTIME_FILE${NC}"
fi

echo ""
echo -e "${GREEN}✅ Destroy Complete${NC}"
