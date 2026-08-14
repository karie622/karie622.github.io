# 微信读书工作台 —— Railway / 任意 Docker 平台镜像

FROM python:3.11-slim

WORKDIR /app

# 复制应用代码与离线数据包（data/ 下含离线书架与内容索引的加密文件 *.enc）
COPY . .

# 安装依赖：cryptography 用于离线模式解密 data/*.enc
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 默认在线模式（OFFLINE 未设置 = 0）：运行时用 WEREAD_API_KEY 实时拉取微信读书数据。
# 若网关屏蔽数据中心 IP，设 OFFLINE=1 + OFFLINE_KEY：
#   读取 data/*.enc 加密数据包（密钥仅存于环境变量，不落盘），完全离线运行。
EXPOSE 8000

CMD ["python", "server.py", "--host", "0.0.0.0"]
