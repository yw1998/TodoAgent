#!/usr/bin/env bash
# 一键更新部署：git 同步 → 重建镜像 → docker compose 重启 → 健康检查
set -euo pipefail
cd "$(dirname "$0")"

BRANCH="${BRANCH:-master}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/healthz}"

echo "==> [1/4] 同步代码 origin/$BRANCH"
git fetch origin "$BRANCH" --prune
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")
if [ "$LOCAL" = "$REMOTE" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "已是最新版本 ($LOCAL)，无需重建。强制更新: FORCE=1 ./update.sh"
  exit 0
fi
git reset --hard "origin/$BRANCH"
echo "更新: ${LOCAL:0:8} -> ${REMOTE:0:8}"

echo "==> [2/4] 重建镜像"
docker compose build --pull

echo "==> [3/4] 重启服务"
docker compose up -d

echo "==> [4/4] 健康检查 $HEALTH_URL"
ok=0
for i in $(seq 1 24); do
  if curl -fsS "$HEALTH_URL" > /dev/null 2>&1; then ok=1; break; fi
  sleep 5
done

if [ "$ok" = "1" ]; then
  echo "部署成功 ✅"
  docker compose ps
  docker image prune -f > /dev/null && echo "已清理旧镜像"
else
  echo "健康检查失败 ❌ 最近日志："
  docker compose logs --tail 80
  exit 1
fi
