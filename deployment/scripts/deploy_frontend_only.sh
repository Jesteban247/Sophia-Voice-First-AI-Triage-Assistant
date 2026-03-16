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

echo -e "${BLUE}🌐 Deploying Frontend Only${NC}"
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
echo -e "${YELLOW}📋 Checking prerequisites...${NC}"
for cmd in aws terraform npm; do
    if ! command -v "$cmd" &> /dev/null; then
        echo -e "${RED}❌ $cmd is not installed${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✅ All prerequisites met${NC}"
echo ""

FRONTEND_BUCKET=$(terraform -chdir="$TF_DIR" output -raw s3_bucket_name)
DIST_ID=$(terraform -chdir="$TF_DIR" output -raw cloudfront_distribution_id)

if [ -z "$FRONTEND_BUCKET" ]; then
    echo -e "${RED}❌ Could not read frontend bucket from Terraform outputs${NC}"
    exit 1
fi

echo -e "${YELLOW}📦 Building frontend...${NC}"
cd "$PROJECT_ROOT/frontend"
if [ ! -d "node_modules" ]; then
    npm install
fi
export VITE_VITALLENS_PROXY_URL="${VITE_VITALLENS_PROXY_URL:-/api/vitallens}"
export VITE_VITALLENS_API_KEY=""
npm run build

echo -e "${YELLOW}☁️  Syncing to S3...${NC}"
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
