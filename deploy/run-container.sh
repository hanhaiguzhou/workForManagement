#!/bin/bash
# Demo Study 容器启动脚本（部署记录 + 重建用）
# 任何一处参数要改，改这里再重跑，不要直接 docker run 手敲。
set -eu

IMAGE="${1:-$(cat /opt/duty-scheduler/deploy/image.tag 2>/dev/null || echo duty-scheduler:latest)}"

# 关键点说明：
#  --runtime=demo-runtime  宿主机默认 runc 是 1.0.0-rc92（2019），在这台 kernel 3.10 上
#                         起的容器里 clone(CLONE_THREAD) 会失败，表现为 Python
#                         "can't start new thread"，FastAPI 每个请求 500。
#                         daemon.json 里注册的 demo-runtime runtime 是 runc 1.3.6，用它正常。
#  -p 8000:8000           绑 0.0.0.0 让校园网 DNAT 能进来；公网不可达（VM 在内网），
#                         且 firewalld 只放行 10.0.0.0/8。
#  -v .../app:/app/app   代码目录挂载宿主机，改前端/改代码不用重建镜像，重启容器即生效
#  -v .../template.xlsx   模板文件挂载，模板上传替换时写入 data/templates 不受影响
#  -v /usr/share/zoneinfo 基础镜像 apt 密钥过期装不了 tzdata，靠挂载宿主机时区文件，
#                         否则容器内是 UTC，"第几周"会算错一天。
#  APP_SECURE_COOKIE 不设（=false）：校园网入口 198.51.100.7:9990 是明文 HTTP，
#                         开了 secure cookie 内网就登录不上。
#  APP_TEACHER_ACCOUNTS   老师账号（交接保险丝）：`学号:密码:姓名`，逗号分隔多个。
#                         本地密码登录，完全不走教务验证；换密码 = 改这行 + 重跑本脚本。
#                         老师与超管同权、代管超管名下部门、不参与排班、对注册流程不可见。
#                         学号若与真实学生账号冲突，本地密码对该账号无效（防冒充，见登录逻辑）。

echo "image: $IMAGE"

docker rm -f duty-scheduler 2>/dev/null || true

docker run -d --name duty-scheduler \
  --runtime=demo-runtime \
  --restart=unless-stopped \
  -p 8000:8000 \
  -v /opt/duty-scheduler/app:/app/app \
  -v /opt/duty-scheduler/data:/app/data \
  # 值班表模板不随仓库分发：把你的模板 Excel 放到宿主机后，取消下一行注释
  # -v /opt/duty-scheduler/template.xlsx:/app/template.xlsx:ro \
  -v /usr/share/zoneinfo:/usr/share/zoneinfo:ro \
  -e TZ=Asia/Shanghai \
  -e APP_DB_PATH=/app/data/app.db \
  -e SCHEDULE_TEMPLATE=/app/template.xlsx \
  -e APP_SEMESTER=2026-2027-1 \
  -e APP_SEMESTER_START=2026-09-07 \
  -e APP_SESSION_DAYS=30 \
  -e JIAOWU_MODE=real \
  -e SUPER_ADMIN_STUDENT_NO=2025000001 \
  -e APP_LOG_PATH=/app/data/access.log \
  -e APP_TEACHER_ACCOUNTS=20259990001:demo12345:演示老师 \
  "$IMAGE"

sleep 6
docker ps --filter name=duty-scheduler --format "{{.Names}} {{.Status}} {{.Ports}}"
curl -s -o /dev/null -w "healthz -> %{http_code}\n" http://127.0.0.1:8000/healthz
