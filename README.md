# BizPortal · 客户余额与消费记录平台

面向「客户可自助查询本公司余额与消费记录」场景的轻量 Web 平台。FastAPI + SQLite + Jinja2 服务端渲染，无前端构建步骤，支持中/英/繁多语言。

## 快速部署（Docker Compose，推荐）

前置条件：目标机已安装 Docker 与 Docker Compose 插件。

```bash
mkdir bizportal && cd bizportal
curl -O https://raw.githubusercontent.com/netfuns/rankez-bizportal/main/docker-compose.yml
docker compose up -d
```

首次启动会自动建库并创建默认管理员 `admin` / `Admin@123`（**登录后请立即改密并绑定 TOTP**）。

- 访问：`http://<主机IP>:8888`
- 数据持久化：SQLite 数据库与上传文件全部存放在 **`docker-compose.yml` 所在目录下的 `data/` 文件夹**（容器内挂载到 `/data`）
- 升级版本：

  ```bash
  docker compose pull && docker compose up -d
  ```

- 从源码构建（不用 GHCR 镜像时）：把 compose 文件里的 `image:` 行注释掉，改用 `build: .`（需 clone 本仓库）

### 数据备份与恢复

所有业务数据只在 `./data` 一个目录里，备份即拷贝：

```bash
# 备份（建议容器运行时也可冷备；要求绝对一致可先 down）
tar czf bizportal-data-$(date +%F).tgz data/

# 恢复
docker compose down
tar xzf bizportal-data-YYYY-MM-DD.tgz   # 覆盖 data/
docker compose up -d
```

## 功能地图

| 角色 | 能力 |
| --- | --- |
| 访客 | 自助注册（仅客户白名单域名）、邮箱确认 |
| 客户 | 查看本公司余额 / 消费记录 / 公司信息与成员；修改全名、电话、密码；提交 OV 工单（仅公司联系人/技术联系人）；自助绑定与重置 TOTP |
| 管理员 | 客户与白名单域名维护、用户增删改查（含批量）、OV 工单确认、订单（充值/新购/续费）与余额、系统设置、站内发件箱、审计日志 |

## 关键业务规则

1. **注册白名单**：邮箱 `@` 后的域名必须命中某个客户的白名单域名，否则拒绝自助注册。管理员手工新增不受此限制。
2. **邮箱确认**：注册后账号为「待确认」，需点击邮件中的确认链接才能登录；邮件内含临时密码。
3. **首次登录**：强制修改密码（≥10 位，含大写/小写/数字/特殊字符）+ 绑定 TOTP（二维码与明文密钥同时提供）。
4. **TOTP 重置**：用户自助重置需先通过当前动态口令；管理员可强制重置，下次登录重新绑定。
5. **余额**：只能由管理员通过订单调整。`credit` 类型（如充值）增加余额，`debit` 类型（如新购、续费）扣减余额；订单类型可在设置中自定义并指定方向。订单列表即客户可见的消费记录（时间 / PO 号 / 类型 / 产品 / 数量 / 金额 / 变动后余额）。
6. **可见性**：用户只能看到所属公司的余额与消费记录；管理员可见全部。
7. **OV 工单**：客户域名旁的「验证公司（Verify OV）」按钮仅公司联系人/技术联系人可见；提交后生成工单（状态「申请验证」），管理员确认后域名标记「已验证」。工单类型在设置中以标签形式维护。
8. **邮件**：未配置 SMTP 时，邮件只写入「站内发件箱」；配置后自动真发，失败记录可在发件箱补发。

## 设置项（管理员 → 设置）

- 站点名称、标签页标题、Logo 上传
- 平台绑定域名（留空不限制；设置后仅允许通过该域名访问，保存时有防锁死保护）
- 用户超时时间（分钟，默认 120）
- 强制所有用户绑定 TOTP、开放自助注册
- 订单类型（每行 `名称,credit|debit`）、工单类型（标签式输入）
- SMTP（主机 / 端口 / 账号 / 密码 / 发件人 / STARTTLS，密码混淆存储）+ 连通性测试

## 本地开发

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux: .venv/bin/pip
BIZPORTAL_DATA_DIR=./data uvicorn app.main:app --reload --port 8000
```

测试：

```bash
python -m pytest tests/ -q
```

## 镜像

- GitHub Actions 每次 push 到 `main` 自动构建并推送：`ghcr.io/netfuns/rankez-bizportal:latest`（Alpine 基础，约 95MB）
- 也可在任意有 Docker 的目标机上 `docker build -t bizportal:latest .` 后自行运行
