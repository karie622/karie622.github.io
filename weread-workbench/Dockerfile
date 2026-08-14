# 微信读书工作台 —— Koyeb / 任意 Docker 平台镜像
# 纯 Python 标准库，无需 pip 安装任何依赖。

FROM python:3.11-slim

WORKDIR /app

# 复制应用代码与离线数据包（data/ 下含离线书架与内容索引）
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 默认在线模式（OFFLINE 未设置 = 0）：运行时用 WEREAD_API_KEY 实时拉取你的微信读书数据，
# 个人数据不落盘到镜像/仓库。若数据中心 IP 被网关屏蔽，可在 Koyeb 后台把 OFFLINE 设为 1
# （此时需镜像内含有 data/ 数据包，参考 README「离线模式」章节）。
EXPOSE 8000

CMD ["python", "server.py", "--host", "0.0.0.0"]
