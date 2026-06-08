#!/bin/bash
# health_check.sh — Verify all LogForge services are healthy

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check() {
    local name=$1
    local url=$2
    local expected=$3

    response=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")

    if [ "$response" = "$expected" ]; then
        echo -e "  ${GREEN}✓${NC} $name ($url) — HTTP $response"
    else
        echo -e "  ${RED}✗${NC} $name ($url) — HTTP $response (expected $expected)"
    fi
}

echo ""
echo "LogForge Health Check"
echo "─────────────────────"

check "FastAPI"          "http://localhost:8000/health"          "200"
check "FastAPI Docs"     "http://localhost:8000/docs"            "200"
check "Elasticsearch"    "http://localhost:9200/_cluster/health" "200"
check "Kibana"           "http://localhost:5601"                  "200"
check "React Frontend"   "http://localhost:3000"                  "200"

echo ""
echo "Kafka Check:"
if docker exec logforge-kafka kafka-topics.sh \
    --bootstrap-server localhost:9092 \
    --list 2>/dev/null | grep -q "raw-logs"; then
    echo -e "  ${GREEN}✓${NC} Kafka — topic 'raw-logs' exists"
else
    echo -e "  ${YELLOW}⚠${NC} Kafka — could not verify topics"
fi

echo ""
echo "Done."
