# 勤工助学值班排班系统（Duty Scheduler）

一套面向高校勤工助学岗位的**课表感知排班系统**：成员用学号同步教务课表，请假按节次登记；管理员一键排出"不撞课"的值班表，并按月度周期归档导出 Excel。

> 本仓库为开源演示版，所有姓名、学号、账号均为**编造的演示数据**，与任何真实人员无关。

## 功能特性

- **课表同步**：对接学校教务系统，学号+密码一次验证即同步课表（密码只用于验证，不落库）
- **智能排班**：以"课表 ∪ 请假"为占用约束，最大流算法求"每人恰好 N 班、每格至多一人"的可行解；排不出来会明确说明原因
- **独占规则**：周六整列与每天 11-12 节可指定常驻人员独占（适合固定岗）
- **线上课豁免**：教室为"线上"的课不占用时间，值班与网课两不误
- **全员总览**：按周查看"谁有空 / 谁请假 / 谁值班"，含手机端适配
- **总体导出**：每次排班自动存档该周 Excel（快照），按月度周期（每月 25 日 → 次月 25 日）归档，支持整周期打包 zip
- **周次校正**：学期初管理员可全局校正"本周是第几周"，全站即时生效
- **多角色**：超级管理员 / 部门管理员 / 老师（应急代管）/ 学生，权限分明
- **明暗双主题**：按北京时间自动切换，支持手动临时切换
- **十字对位**：课表悬浮/点选格子时高亮所在行与列，手机点按可钉住

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 · FastAPI · Uvicorn · SQLAlchemy |
| 数据库 | SQLite（单文件，随库备份） |
| 前端 | Jinja2 服务端渲染 · 原生 CSS/JS（无构建步骤） |
| Excel | openpyxl（模板解析 + 值班表导出） |
| 部署 | Docker（代码目录 bind-mount，改代码重启即生效） |

## 快速开始

```bash
pip install -r requirements-web.txt

# 必要环境变量（完整示例见 deploy/run-container.sh）
export APP_DB_PATH=./data/app.db
export APP_SEMESTER=2026-2027-1
export APP_SEMESTER_START=2026-09-07
export SCHEDULE_TEMPLATE=./your-duty-roster.xlsx   # 模板不随仓库分发：自备值班表 Excel（约定见 app/xlsx.py）

python run.py   # http://127.0.0.1:8000
```

首次登录即建号（待审批），第一个用 `SUPER_ADMIN_STUDENT_NO` 学号登录的人自动成为超级管理员。

### 演示账号（编造数据）

| 角色 | 账号 | 密码 | 说明 |
|---|---|---|---|
| 老师（代管） | `20259990001` | `demo12345` | 需先配置 `APP_TEACHER_ACCOUNTS=20259990001:demo12345:演示老师`，不走教务验证 |

### 离线模式（无教务系统时）

```bash
export JIAOWU_MODE=stub
export JIAOWU_STUB_ACCOUNTS=2025000001:pw1,2025000002:pw2
```

`stub` 模式用假账号登录并生成演示课表，适合本地体验与自动化测试。

## Docker 部署

```bash
docker build -t duty-scheduler .
bash deploy/run-container.sh   # 环境变量示例齐全，改参数改这里
```

两个部署坑（详见脚本注释）：

1. 老内核宿主机（kernel 3.10 + 旧 runc）需注册新版 runc 为自定义 runtime，否则容器内 Python 起线程报错；
2. 基础镜像装不了 `tzdata`（apt 密钥轮换），时区靠挂载宿主机 `/usr/share/zoneinfo`。

## 测试

```bash
# 冒烟测试（HTTP 层，需 httpx）
docker exec <容器> pip install httpx
docker exec -e TEACHER_SMOKE_PW=... -i <容器> python - < scripts/enhancements_smoke.py
```

`scripts/` 下另有教师角色、总体导出两套冒烟与若干排班器验证脚本。

## 目录结构

```
app/
  web/          FastAPI 应用、模板、静态资源
  services/     排班、存档、周次、部门等服务层
  scheduler.py  排班器（纯函数，最大流）
  xlsx.py       模板解析与 Excel 导出
  jiaowu.py     教务对接（含离线 stub）
deploy/         容器启动脚本与部署示例
scripts/        冒烟测试与运维脚本
```

## 安全说明

- 教务密码只在登录瞬间使用，验证通过立即丢弃；数据库不存任何密码
- 会话 Cookie 只存 token 的 sha256；所有管理操作强制 CSRF 校验
- 演示数据均为编造，请勿与本系统之外任何真实人员关联

## License

MIT
