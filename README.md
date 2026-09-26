# BizPortal · 客户余额与消费记录平台

面向「客户可自助查询本公司余额与消费记录」场景的轻量 Web 平台。FastAPI + SQLite + Jinja2 服务端渲染，无前端构建步骤。

## 访问

- 地址：http://192.168.254.10:8888
- 默认管理员：`admin` / `Admin@123`（**首次登录后请立即在「设置」或直接改密**）

## 功能地图

| 角色 | 能力 |
| --- | --- |
| 访客 | 自助注册（仅客户白名单域名）、邮箱确认 |
| 客户用户 | 查看本公司余额 / 消费记录 / 公司信息与成员；修改全名、电话、密码；自助绑定与重置 TOTP |
| 管理员 | 客户与白名单域名维护、用户增删改查（含批量）、订单（充值/新购/续费）与余额、系统设置、站内发件箱、审计日志 |

## 关键业务规则

1. **注册白名单**：邮箱 `@` 后的域名必须命中某个客户的白名单域名，否则拒绝自助注册。管理员手工新增不受此限制。
2. **邮箱确认**：注册后账号为「待确认」，需点击邮件中的确认链接才能登录；邮件内含临时密码。
3. **首次登录**：强制修改密码（≥10 位，含大写/小写/数字/特殊字符）+ 绑定 TOTP（二维码与明文密钥同时提供）。
4. **TOTP 重置**：用户自助重置需先通过当前动态口令；管理员可强制重置，下次登录重新绑定。
5. **余额**：只能由管理员通过订单调整。`credit` 类型（如充值）增加余额，`debit` 类型（如新购、续费）扣减余额；订单类型可在设置中自定义并指定方向。订单列表即客户可见的消费记录（时间 / PO 号 / 类型 / 产品 / 数量 / 金额 / 变动后余额）。
6. **可见性**：用户只能看到所属公司的余额与消费记录；管理员可见全部。
7. **邮件**：未配置 SMTP 时，邮件只写入「站内发件箱」，管理员可查看确认链接或用「标记已确认」放行账号。

## 设置项（管理员 → 设置）

- 站点名称、标签页标题、Logo 上传
- 平台绑定域名（留空不限制；设置后仅允许通过该域名访问，保存时有防锁死保护）
- 用户超时时间（分钟，默认 120）
- 强制所有用户绑定 TOTP、开放自助注册
- 订单类型（每行 `名称,credit|debit`）
- SMTP（主机 / 端口 / 账号 / 密码 / 发件人 / STARTTLS，密码混淆存储）+ 连通性测试

## 本地开发

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
BIZPORTAL_DATA_DIR=./data uvicorn app.main:app --reload --port 8000
```

测试（Windows 下务必带 `CODEBUDDY_SAFE_DELETE_ENABLED=0`）：

```bash
CODEBUDDY_SAFE_DELETE_ENABLED=0 .venv/Scripts/python.exe -m pytest tests/ -q
```

## 部署

```bash
tar czf bizportal.tgz -C bizportal app deploy requirements.txt
# 上传到目标机后
sudo bash /tmp/bizportal/install.sh /tmp/bizportal/bizportal.tgz
```

安装位置：

- 代码与虚拟环境：`/opt/bizportal`
- 数据与上传：`/var/lib/bizportal`
- 环境配置：`/etc/bizportal/bizportal.env`（0640 root:bizportal）
- 服务：`systemd bizportal.service`，uvicorn 监听 `0.0.0.0:8888`，运行用户 `bizportal`

常用运维：

```bash
systemctl status bizportal
journalctl -u bizportal -f
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8888/healthz
```
