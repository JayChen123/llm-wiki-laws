#!/usr/bin/env bash
# Web 服务冒烟验证：对着已启动的服务跑一遍「健康检查 → 注册 → 建会话 → 流式问答 → 引用持久化 → 登出」。
#
# 用法（先起服务，再跑本脚本）：
#   cd web && npm run build && cd ..          # 构建前端（产物 web/dist 由 FastAPI 托管）
#   uv run python -m app.api.main             # 另开一个终端，默认 :8080
#   scripts/smoke_api.sh                      # 默认打 http://localhost:8080
#
# 只依赖 curl，不需要浏览器；前端页面本身另用浏览器验证。
set -uo pipefail

BASE="${BASE:-http://localhost:8080}"
USER_NAME="${USER_NAME:-smokeuser_$$}"
PASSWORD="${PASSWORD:-secret123}"
JAR="$(mktemp)"
fail=0

step() { printf '\n=== %s ===\n' "$1"; }
ok()   { echo "  ✓ $1"; }
bad()  { echo "  ✗ $1"; fail=1; }

step "1. 健康检查"
if curl -fsS "$BASE/api/health" | grep -q '"ok":true'; then ok "/api/health"; else bad "/api/health"; fi

step "2. 首页（前端构建产物由 FastAPI 托管）"
code=$(curl -s -o /tmp/smoke_index.html -w '%{http_code}' "$BASE/")
if [ "$code" = "200" ] && grep -qi '<div id="root">' /tmp/smoke_index.html; then
  ok "首页 200 且含 SPA 挂载点"
else
  bad "首页异常（HTTP $code）——前端可能未构建或被 SPA 回退成 503"
fi

step "3. 未登录访问受保护接口应 401"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/sessions")
[ "$code" = "401" ] && ok "401 未登录被挡" || bad "期望 401，实得 $code"

step "4. 注册（Cookie 应带 HttpOnly + SameSite=Lax）"
hdr=$(curl -s -D - -o /tmp/smoke_reg.json -c "$JAR" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER_NAME\",\"password\":\"$PASSWORD\"}" \
  "$BASE/api/auth/register")
if grep -qi 'set-cookie' <<<"$hdr" && grep -qi 'httponly' <<<"$hdr" && grep -qi 'samesite=lax' <<<"$hdr"; then
  ok "注册成功且 Cookie 安全属性齐全"
else
  bad "Cookie 属性缺失：$(grep -i set-cookie <<<"$hdr" | head -1)"
fi

step "5. 当前用户"
if curl -fsS -b "$JAR" -c "$JAR" "$BASE/api/auth/me" | grep -q "$USER_NAME"; then ok "/api/auth/me"; else bad "/api/auth/me"; fi

step "6. 新建会话"
sid=$(curl -s -b "$JAR" -c "$JAR" -H 'Content-Type: application/json' -X POST "$BASE/api/sessions" \
  | sed -n 's/.*"session_id":"\([^"]*\)".*/\1/p')
[ -n "$sid" ] && ok "会话 $sid" || bad "新建会话失败"

step "7. 流式问答（SSE 事件序列）"
if [ -n "$sid" ]; then
  curl -s -N -b "$JAR" -c "$JAR" -H 'Content-Type: application/json' \
    -d "{\"session_id\":\"$sid\",\"question\":\"试用期最长可以约定多久？\"}" \
    "$BASE/api/chat" > /tmp/smoke_chat.txt
  for ev in meta delta citations done; do
    grep -q "^event: $ev" /tmp/smoke_chat.txt && ok "事件 $ev" || bad "缺少事件 $ev"
  done
  # SSE 的 data 用 json.dumps 默认分隔符（冒号后有空格），故用宽松匹配
  if grep -qE '"id": ?"KP_' /tmp/smoke_chat.txt; then ok "返回了知识点引用"; else bad "没有引用（检索可能未生效）"; fi
fi

step "8. 引用随历史持久化"
if [ -n "$sid" ]; then
  if curl -s -b "$JAR" -c "$JAR" "$BASE/api/sessions/$sid/messages" | grep -q '"citations":\[{'; then
    ok "历史消息带引用"
  else
    bad "历史消息缺引用"
  fi
fi

step "9. 登出后受保护接口应 401"
curl -s -b "$JAR" -c "$JAR" -X POST "$BASE/api/auth/logout" >/dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/sessions")
[ "$code" = "401" ] && ok "登出后 401" || bad "登出后期望 401，实得 $code"

rm -f "$JAR" /tmp/smoke_reg.json /tmp/smoke_index.html
printf '\n=== 结果：%s ===\n' "$([ $fail -eq 0 ] && echo 全部通过 || echo 有失败项)"
printf '（本次注册的测试账号：%s；如需清理：删除该用户的 users/sessions/messages 行并清其 Milvus 记忆）\n' "$USER_NAME"
exit $fail
