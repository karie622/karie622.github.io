# 微信读书工作台

一个把**微信读书**里「你的书架 / 划线 / 想法」变成可读、可检索、可对话素材的本地工作台。
纯 Python 标准库实现，零第三方依赖，可一键部署到云端，手机也能随时访问。

> 本程序只是数据的**搬运与整理**工具：书架、划线、想法、热门划线等数据均来自你自己的微信读书账号，
> 通过微信读书 Agent Gateway（`i.weread.qq.com`）实时获取。AI 生成（作者对话、创作灵感）部分由 WorkBuddy 完成。

---

## 一、本地运行（电脑上）

```bash
# 1. 准备微信读书 Key
#    Key 是一串 wrk- 开头的字符串。本地运行时程序按以下顺序找 Key：
#      a) 本目录下的 weread_api_key 文件
#      b) ~/.workbuddy/weread_api_key
#      c) 环境变量 WEREAD_API_KEY
echo "wrk-你的key" > weread_api_key        # 方式 a：放进程序目录最省事

# 2. 启动
python server.py
#   默认 http://127.0.0.1:8787 ；局域网/公网模式加 --host 0.0.0.0
```

浏览器打开 `http://127.0.0.1:8787` 即可使用四个功能页：
`内容检索` · `作者对话` · `书架与目录` · `创作助手`，以及 `📱手机访问`。

---

## 二、手机访问

### 方式 A：同一 WiFi / 热点（无需部署云端）
1. 启动服务时加 `--host 0.0.0.0`：`python server.py --host 0.0.0.0`
2. 打开「📱手机访问」页，用手机扫二维码即可。
   - 二维码地址取 `PUBLIC_URL` 环境变量；未设置时自动用本机局域网 IP。
   - 手机需与电脑连同一 WiFi/热点。

### 方式 B：脱离电脑，部署到云端（推荐 Koyeb）
见下文「三、云端部署」。部署后把 `PUBLIC_URL` 设为云端分配的地址，
「手机访问」页的二维码即为公网地址，任意网络下手机都能打开。

---

## 三、云端部署

### ✅ 推荐：Koyeb（Docker，在线模式，代码不入库）

Koyeb 用 Docker 部署。**推荐在线模式**：仓库只放代码，容器运行时用你的 Key 实时拉取数据，
个人阅读数据（书架/划线/笔记）不进入公开仓库，最私密。

**方式一：网页后台（最省事）**
1. 注册登录 https://www.koyeb.com ，「New App → Deploy from GitHub」。
2. 选择仓库 `karie622/karie622.github.io`（或你自己的仓库）。
3. 构建设置：
   - **Build method**：Dockerfile
   - **Build context / Root directory**：`weread-workbench`（本应用在子目录）
   - **Dockerfile path**：`weread-workbench/Dockerfile`
4. 环境变量（Environment variables）：
   - `WEREAD_API_KEY` = 你的 `wrk-` 开头 Key  ← **必填**，在线模式用它实时拉取数据
   - `OFFLINE` = `0`（默认，可不填）← 在线模式
   - `PORT` = `8000`（Koyeb 会自动注入，可不填）
   - `PUBLIC_URL` = 部署后 Koyeb 分配的地址（如 `https://xxxx.koyeb.app`），用于「手机访问」二维码
5. 部署完成后会分配 `https://xxxx.koyeb.app` 公网地址，手机任意网络直接打开。
6. **首次使用**：进「内容检索」页点「建立内容索引」（或调用 `/api/build_index`），
   后端会拉取你全部有内容的书的划线/想法并建本地索引；之后检索、创作素材即可用。

**方式二：koyeb CLI（可选）**
仓库已含 `koyeb.yaml`，安装 CLI 后：
```bash
koyeb app init weread-workbench --config koyeb.yaml
```

**关于离线模式（OFFLINE=1）**：若微信读书网关屏蔽了 Koyeb 数据中心 IP，实时拉取会失败。
此时可切到离线模式——但离线需要镜像内含 `data/` 数据包（含你的个人数据），
因此**离线模式只适用于私有仓库或本地镜像**，请勿把 `data/` 提交到公开仓库。
详见 README「四、离线模式」。

---

### 备选平台

- **Render**：仓库含 `render.yaml`，后台 New → Blueprint → 连仓库即可识别。
  注意：Render 免费版需信用卡验证；且部分数据中心 IP 可能被微信读书网关屏蔽，
  此时请在 Render 环境变量设 `OFFLINE=1` 走离线数据。
- **PythonAnywhere**：免费版禁止非白名单出站，必须用 `OFFLINE=1` 离线模式（WSGI 部署，
  详见历史部署备忘）。本仓库的 `data/` 包即为离线数据来源。

---

## 四、离线模式（OFFLINE）

设置 `OFFLINE=1` 后，所有接口**只读取本地 `data/` 数据包**，不再调用任何外部接口：

| 功能 | 在线依赖 | 离线数据源 |
|------|----------|------------|
| 内容检索 | `data/content_index.json`（本地索引） | 同左（与联网无关） |
| 作者角色包 | `/book/info` `/book/bestbookmarks` `/book/bookmarklist` | `data/offline_bundle.json` |
| 书架与目录 | `/shelf/sync` | `data/offline_bundle.json` 中的 `shelf` |
| 创作素材 | `data/content_index.json` + `/book/similar` | `data/content_index.json`（相似推荐略） |

**如何生成离线数据包（本地、需联网 + Key）：**
```bash
python export_offline.py     # 导出全部书架简介/热门划线/章节目录 → data/offline_bundle.json
python -c "import server; print(server.build_index())"   # 重建内容索引 → data/content_index.json
```
生成后连同 `data/` 一起部署，`OFFLINE=1` 即可在数据中心的「禁出网 / 网关屏蔽」环境稳定运行。

---

## 五、环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `PORT` | 监听端口 | `8787`（云端平台通常注入 `8000`） |
| `WEREAD_HOST` | 显式监听地址 | 有 `PORT` 时 `0.0.0.0`，否则 `127.0.0.1` |
| `PUBLIC_URL` | 公网地址，用于「手机访问」二维码 | 空 |
| `WEREAD_API_KEY` | 微信读书 Key（在线模式需要） | 空（离线模式可缺省） |
| `OFFLINE` | `1` 走离线数据，不调网关 | `0` |

---

## 六、目录结构

```
weread-workbench/
├── server.py                 # 后端（标准库 HTTP 服务 + 微信读书网关代理 + 离线模式）
├── index.html                # 前端（四大功能 + 手机访问）
├── qrcode.min.js             # 二维码生成库
├── export_offline.py         # 离线数据包导出脚本
├── Dockerfile                # Koyeb / Docker 部署
├── koyeb.yaml                # Koyeb 配置（可选）
├── render.yaml               # Render 配置（备选）
├── Procfile / requirements.txt
├── data/
│   ├── shelf.json            # 书架缓存（在线模式生成）
│   ├── content_index.json    # 内容索引（划线/想法，检索与创作素材用）
│   └── offline_bundle.json   # 离线数据包（全部书简介/热门划线/章节）
└── README.md
```

---

## 七、安全提示

- 微信读书 Key（`wrk-` 开头）等同于你账号的只读凭证，**不要公开提交到公开仓库**。
  本仓库 `data/` 与代码均不含 Key；Key 仅存在于你的本地 `weread_api_key` 文件或部署平台的环境变量里。
- 云端部署若使用 `OFFLINE=1`，连 Key 都不需要，安全性更高。
- 若 Key 曾粘贴给第三方，请到微信读书/WorkBuddy 侧尽快吊销重置。
