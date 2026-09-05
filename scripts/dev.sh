#!/usr/bin/env bash
# Dev server launcher — starts backend + frontend, restarts on Ctrl+C+Enter
set -euo pipefail
cd "$(dirname "$0")/.."

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

cleanup() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    [ -n "${BE_PID:-}" ] && kill "$BE_PID" 2>/dev/null && wait "$BE_PID" 2>/dev/null
    [ -n "${FE_PID:-}" ] && kill "$FE_PID" 2>/dev/null && wait "$FE_PID" 2>/dev/null
    echo -e "${GREEN}Stopped.${NC}"
}
trap cleanup EXIT

start_backend() {
    echo -e "${GREEN}Starting backend on :8000${NC}"
    .venv/bin/uvicorn options_trading.main:app \
        --host 0.0.0.0 --port 8000 --app-dir src --reload &
    BE_PID=$!
}

start_frontend() {
    echo -e "${GREEN}Starting frontend on :5173${NC}"
    (cd frontend && npm run dev) &
    FE_PID=$!
}

case "${1:-all}" in
    backend|be)
        start_backend
        wait "$BE_PID"
        ;;
    frontend|fe)
        start_frontend
        wait "$FE_PID"
        ;;
    all)
        start_backend
        start_frontend
        echo -e "${GREEN}Both running. Press Ctrl+C to stop.${NC}"
        wait
        ;;
    restart)
        # Kill existing processes on the ports (SIGKILL to ensure they die)
        lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
        lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null || true
        for i in 1 2 3 4 5; do
            lsof -ti:8000 2>/dev/null || break
            sleep 1
        done
        start_backend
        start_frontend
        echo -e "${GREEN}Restarted. Press Ctrl+C to stop.${NC}"
        wait
        ;;
    *)
        echo "Usage: $0 [all|backend|be|frontend|fe|restart]"
        exit 1
        ;;
esac
