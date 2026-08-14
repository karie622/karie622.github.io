# 微信读书工作台（云端部署版）

本目录是「微信读书工作台」的云端部署代码，由 Render 等平台读取运行。
手机无需连接电脑，直接打开部署后的公网地址即可使用。

## 文件说明
- `server.py` —— Python 本地服务（仅用标准库）
- `index.html` —— 前端界面
- `qrcode.min.js` —— 手机扫码访问的二维码库
- `requirements.txt` / `Procfile` / `render.yaml` —— 云平台部署配置

## 部署到 Render
1. 在 Render 后台 New → Blueprint → 连接本仓库（含 `render.yaml`）
2. 设环境变量：`WEREAD_API_KEY`（微信读书 Key）和 `PUBLIC_URL`（部署后回填）
3. Deploy，约 1 分钟上线

详见仓库根目录的部署说明或控制台输出。
