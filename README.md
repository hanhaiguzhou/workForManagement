# 勤工助学值班排班系统（Duty Scheduler）

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-单文件-003B57?logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

一套面向高校勤工助学岗位的**课表感知排班系统**：成员登录即同步**强智教务系统**的课表，请假按节次登记；管理员一键排出"不撞课"的值班表并导出 Excel，系统按月度周期自动归档每一次排班。

> 本仓库为开源演示版：所有姓名、学号、账号均为**编造的演示数据**，值班表模板不随仓库分发，与任何真实人员和单位无关。

## 功能特性

### 排班
- **课表同步**：学号 + 强智教务密码一次验证即抓取课表，密码验证后立即丢弃、不落库；支持离线 stub 模式
- **智能排班**：以"线下课 ∪ 请假"为占用约束，最大流算法求"每人恰好 N 班、每格至多一人"的可行解；排不出来会给出具体原因（谁缺几班、为什么）
- **独占规则**：周六整列与每天 11-12 节可指定常驻人员独占，其余格子普通成员与常驻人员按配额竞争
- **线上课豁免**：教室为"线上"的课不算占用——值班与网课两不误
- **周次校正**：学期初管理员填"本周是第 N 周"，全站（含学生端）即时生效；自然周结构不变，只平移编号

### 使用
- **我的课表**：按节次查看每周课程与值班；有课/线上课/已请假/空闲四种状态一目了然；按节次请假；十字对位辅助辨认"周几 · 第几节"（悬浮跟随、手机点按钉住）
- **全员总览**：整周"谁有空 / 谁有课 / 谁请假 / 谁值班"矩阵 + 每人占用统计
- **总体导出**：每次排班自动存档该周 Excel（与下载文件完全一致的快照）；按月度周期（25 日 → 次月 25 日）归档，同一周重排只留最新；历史周期折叠展示、整周期打包 zip 下载
- **值班表模板**：支持自定义 Excel 模板与在线改文案（标题/地点/老师/备注），上传时自动校验结构
- **多角色**：超级管理员 / 部门管理员 / 老师（应急代管，本地凭据登录）/ 学生
- **明暗双主题**：按北京时间自动切换，支持手动临时切换；移动端自适应

## 快速开始

```bash
git clone https://github.com/<you>/duty-scheduler.git
cd duty-scheduler
pip install -r requirements-web.txt

export APP_DB_PATH=./data/app.db
export APP_SEMESTER=2026-2027-1
export APP_SEMESTER_START=2026-09-07
export SCHEDULE_TEMPLATE=./your-duty-roster.xlsx   # 模板不随仓库分发，见下文约定

python run.py   # http://127.0.0.1:8000
```

第一个用 `SUPER_ADMIN_STUDENT_NO` 学号登录的人自动成为超级管理员；其他成员首次登录建号（待审批），由管理员审批后加入部门。

### 教务系统适配

默认适配**强智科技教务系统**（标准版课表页）。

- 教务地址在 `app/jiaowu.py` 顶部配置（仓库里是演示占位 `jw1/jw2.example.com`，替换成你们学校强智教务的实际地址即可）
- 其他厂商的教务需要自行适配 `jiaowu.py` 的登录与课表解析

### 离线模式（没有教务系统也能玩）

```bash
export JIAOWU_MODE=stub
export JIAOWU_STUB_ACCOUNTS=2025000001:pw1,2025000002:pw2
```

`stub` 模式用假账号登录并生成演示课表，适合本地体验、二次开发与自动化测试。

### 演示账号

| 角色 | 账号 | 密码 | 说明 |
|---|---|---|---|
| 老师（应急代管） | `20259990001` | `demo12345` | 需配置 `APP_TEACHER_ACCOUNTS`；本地密码登录，不走教务 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_DB_PATH` | `./data/app.db` | SQLite 数据库路径 |
| `APP_SEMESTER` | 按日期推算 | 学期名（如 `2026-2027-1`） |
| `APP_SEMESTER_START` | `2026-09-07` | 学期第一周周一（周次推算基准） |
| `SCHEDULE_TEMPLATE` | 见 `app/config.py` | 值班表 Excel 模板路径（**不随仓库分发**） |
| `SUPER_ADMIN_STUDENT_NO` | 空 | 该学号首次登录即成为超级管理员 |
| `APP_TEACHER_ACCOUNTS` | 空 | 老师账号：`学号:密码:姓名`，逗号分隔；本地密码登录，不走教务 |
| `JIAOWU_MODE` | `real` | `real` 连强智教务；`stub` 离线演示 |
| `JIAOWU_STUB_ACCOUNTS` | 空 | stub 模式账号：`学号:密码`，逗号分隔 |
| `APP_SESSION_DAYS` | `30` | 会话有效期（天） |
| `APP_SECURE_COOKIE` | `false` | 走 HTTPS 时建议开启 |
| `APP_LOG_PATH` | 空 | 访问日志落盘路径 |

## 值班表模板约定

排班结果导出基于 Excel 模板（**不随仓库分发**——各单位的表格含真实信息，请自备）：

- 可排班的格子（周一~周六 × 6 个节次，共 36 个必需格）里写占位标记 `A2`；周日列可选
- 职务人的姓名写在 `A2` 标记右侧一格
- 程序在排班/导出前逐格校验，缺格、错位都会明确报错，绝不静默导出错表
- 标题、地点、老师、备注支持在线改文案，不用重传 Excel

细节与解析逻辑见 [`app/xlsx.py`](app/xlsx.py) 模块文档。

## 排班规则

- 占用 = **线下课 ∪ 请假**（教室为"线上"的课不算占用）
- 硬约束：每人恰好排 N 班（可配）；每格至多一人；周日不排；1-2 节黑名单；独占格只归常驻人员
- 算法：人 → 格子二部图最大流；不可行时输出每人缺口与原因，绝不返回"看起来能用但错了"的部分结果
- 排完后再跑一遍独立校验器，任一硬约束不满足即拒绝出表

## Docker 部署

```bash
docker build -t duty-scheduler .
bash deploy/run-container.sh   # 环境变量与挂载示例齐全，改参数改这里
```

两个实战踩坑（脚本内有详细注释）：

1. 老内核宿主机（kernel 3.10 + 旧 runc）需注册新版 runc 为自定义 runtime，否则容器内 Python 报 `can't start new thread`；
2. 基础镜像装不了 `tzdata`（Debian apt 密钥轮换），时区靠挂载宿主机 `/usr/share/zoneinfo` + `TZ=Asia/Shanghai`，否则周次计算差一天。

## 测试

```bash
# 冒烟测试（HTTP 层，需 httpx；覆盖排班/导出/权限/周次校正全流程）
docker exec <容器> pip install httpx
docker exec -i <容器> python - < scripts/enhancements_smoke.py
```

`scripts/` 下共三套冒烟（教师角色 / 总体导出 / 排班页增强）与排班器多种子重放等验证脚本。

## 目录结构

```
app/
  web/          FastAPI 应用、Jinja2 模板、静态资源
  services/     排班、存档、周次、部门、课表等服务层
  scheduler.py  排班器（纯函数、最大流、带独立校验器）
  xlsx.py       模板解析与 Excel 导出
  jiaowu.py     强智教务对接（real / stub 双模式）
  theme.py      明暗主题（北京时间自动切换）
deploy/         容器启动脚本与部署示例
scripts/        冒烟测试与运维脚本
template.xlsx   （不提供）自备值班表模板放这里
```

## 安全设计

- 教务密码只在登录瞬间验证，通过即丢弃；数据库不存任何密码
- 会话 Cookie 只存 token 的 sha256，泄露也无法还原
- 所有状态变更操作强制 CSRF 校验；作用域在查询层强制（学生只见自己、管理员只见本部门）
- 老师等代管角色无法停用/篡改超级管理员账号

## License

[MIT](LICENSE)
